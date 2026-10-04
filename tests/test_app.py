from html.parser import HTMLParser
from pathlib import Path

import httpx
import yaml
from fastapi.testclient import TestClient

from app.main import app, registry
from app.services import RateLimiter, ServiceError, validate_registry

client = TestClient(app)
JPEG = b"\xff\xd8\xff\xe0" + b"demo"
PNG = b"\x89PNG\r\n\x1a\n" + b"demo"
PDF = b"%PDF-1.7\n" + b"demo"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and href.startswith("/") and not href.startswith("//"):
            self.links.add(href)


def test_registry_and_contract_are_valid() -> None:
    validate_registry(registry)
    contract_path = Path("contracts/ptk-technologies.openapi.yaml")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    assert contract["openapi"] == "3.1.0"
    assert set(contract["paths"]) == {
        "/api/v1/ocr/ktp",
        "/api/v1/ocr/general",
        "/api/v1/document/detect",
    }


def test_deployment_manifest_limits_container_access() -> None:
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["lutung"]
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert service["read_only"] is True
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert any(entry.startswith("/tmp:") for entry in service["tmpfs"])


def test_primary_pages_render() -> None:
    for path in [
        "/",
        "/technologies",
        "/technologies/ocr-ktp",
        "/playground/ocr-ktp",
        "/api",
        "/api/docs/ocr-ktp",
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert "LUTUNG PTK" in response.text
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_request_id_is_sanitized_before_logging() -> None:
    response = client.get("/health", headers={"X-Request-ID": "unsafe request id"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] != "unsafe request id"
    assert len(response.headers["x-request-id"]) == 36


def test_catalog_search_and_empty_state() -> None:
    matching = client.get("/technologies", params={"q": "KTP"})
    assert matching.status_code == 200
    assert "OCR KTP" in matching.text
    assert "1 teknologi ditemukan" in matching.text

    empty = client.get("/technologies", params={"q": "tidak-ada-hasil"})
    assert empty.status_code == 200
    assert "Tidak ada hasil" in empty.text


def test_visible_internal_links_have_destinations() -> None:
    parser = LinkParser()
    for source in ["/", "/technologies", "/api", "/technologies/ocr-ktp"]:
        parser.feed(client.get(source).text)
    failures = {
        link: response.status_code
        for link in parser.links
        if (response := client.get(link)).status_code != 200
    }
    assert failures == {}


def test_all_playgrounds_return_explicit_demo_results(monkeypatch) -> None:
    monkeypatch.setenv("LUTUNG_DEMO_MODE", "true")
    cases = [
        ("/api/v1/ocr/ktp", "ktp.jpg", "image/jpeg", JPEG, "nik"),
        ("/api/v1/ocr/general", "scan.pdf", "application/pdf", PDF, "text"),
        ("/api/v1/document/detect", "document.png", "image/png", PNG, "document_type"),
    ]
    for path, name, content_type, content, expected_field in cases:
        response = client.post(path, files={"file": (name, content, content_type)})
        assert response.status_code == 200
        payload = response.json()
        assert payload["demo"] is True
        assert expected_field in payload["data"]
        assert payload["request_id"]


def test_upload_rejects_mismatched_signature(monkeypatch) -> None:
    monkeypatch.setenv("LUTUNG_DEMO_MODE", "true")
    response = client.post(
        "/api/v1/ocr/ktp",
        files={"file": ("ktp.jpg", PNG, "image/jpeg")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "file_signature_mismatch"


def test_unconfigured_service_fails_without_leaking_details(monkeypatch) -> None:
    monkeypatch.setenv("LUTUNG_DEMO_MODE", "false")
    monkeypatch.delenv("OCR_KTP_API_URL", raising=False)
    response = client.post(
        "/api/v1/ocr/ktp",
        files={"file": ("ktp.jpg", JPEG, "image/jpeg")},
    )
    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "service_not_configured"
    assert "token" not in response.text.casefold()


def test_upstream_adapter_keeps_token_server_side(monkeypatch) -> None:
    real_async_client = httpx.AsyncClient

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer server-secret"
        assert b'filename="ktp.jpg"' in request.content
        return httpx.Response(200, json={"data": {"nik": "normalized"}})

    transport = httpx.MockTransport(upstream)
    monkeypatch.setenv("LUTUNG_DEMO_MODE", "false")
    monkeypatch.setenv("OCR_KTP_API_URL", "https://service.internal/ocr")
    monkeypatch.setenv("OCR_KTP_API_TOKEN", "server-secret")
    monkeypatch.setattr(
        "app.services.httpx.AsyncClient",
        lambda **_kwargs: real_async_client(transport=transport),
    )

    response = client.post(
        "/api/v1/ocr/ktp",
        files={"file": ("ktp.jpg", JPEG, "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"nik": "normalized"}
    assert "server-secret" not in response.text


def test_rate_limiter_releases_requests_after_window(monkeypatch) -> None:
    clock = iter([0.0, 0.5, 2.0])
    monkeypatch.setattr("app.services.time.monotonic", lambda: next(clock))
    rate_limiter = RateLimiter(limit=1, window_seconds=1)
    rate_limiter.check("client")
    try:
        rate_limiter.check("client")
    except ServiceError as error:
        assert error.status_code == 429
    else:
        raise AssertionError("Second request should be rate limited")
    rate_limiter.check("client")


def test_health_distinguishes_catalog_and_configuration(monkeypatch) -> None:
    monkeypatch.delenv("OCR_KTP_API_URL", raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["technologies"]["ocr-ktp"]["configured"] is False
