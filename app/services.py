from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import UploadFile

BASE_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = BASE_DIR / "config" / "technologies.yaml"
ALLOWED_STATUSES = {"available", "beta", "development", "coming-soon", "maintenance"}
SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/pdf": (b"%PDF-",),
}


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def load_registry() -> dict[str, Any]:
    with REGISTRY_PATH.open(encoding="utf-8") as registry_file:
        registry = yaml.safe_load(registry_file)
    validate_registry(registry)
    return registry


def validate_registry(registry: dict[str, Any]) -> None:
    category_ids = {category["id"] for category in registry.get("categories", [])}
    technology_ids: set[str] = set()
    for technology in registry.get("technologies", []):
        technology_id = technology.get("id")
        if not technology_id or technology_id in technology_ids:
            raise ValueError(f"Technology id is missing or duplicated: {technology_id}")
        technology_ids.add(technology_id)
        if technology.get("category") not in category_ids:
            raise ValueError(f"Unknown category for {technology_id}")
        if technology.get("status") not in ALLOWED_STATUSES:
            raise ValueError(f"Unknown status for {technology_id}")
        if technology.get("playground") and not technology.get("operation"):
            raise ValueError(f"Playground operation is missing for {technology_id}")


def find_technology(registry: dict[str, Any], technology_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in registry["technologies"] if item["id"] == technology_id),
        None,
    )


def find_by_operation(registry: dict[str, Any], operation: str) -> dict[str, Any] | None:
    return next(
        (item for item in registry["technologies"] if item.get("operation") == operation),
        None,
    )


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


async def validate_upload(upload: UploadFile, technology: dict[str, Any]) -> bytes:
    filename = upload.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in technology["accepted_extensions"]:
        raise ServiceError(415, "unsupported_file_type", "Jenis file tidak didukung.")
    if upload.content_type not in technology["accepted_types"]:
        raise ServiceError(415, "unsupported_file_type", "Tipe konten file tidak didukung.")

    max_bytes = int(technology["max_size_mb"]) * 1024 * 1024
    content = await upload.read(max_bytes + 1)
    if not content:
        raise ServiceError(400, "empty_file", "File yang dipilih kosong.")
    if len(content) > max_bytes:
        raise ServiceError(
            413,
            "file_too_large",
            f"Ukuran file melebihi batas {technology['max_size_mb']} MB.",
        )

    expected_signatures = SIGNATURES.get(upload.content_type, ())
    if not any(content.startswith(signature) for signature in expected_signatures):
        raise ServiceError(415, "file_signature_mismatch", "Isi file tidak sesuai dengan tipenya.")
    return content


@dataclass
class RateLimiter:
    limit: int = 20
    window_seconds: int = 60

    def __post_init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client_key: str) -> None:
        now = time.monotonic()
        requests = self._requests[client_key]
        while requests and requests[0] <= now - self.window_seconds:
            requests.popleft()
        if len(requests) >= self.limit:
            raise ServiceError(429, "rate_limit_exceeded", "Terlalu banyak permintaan. Coba lagi.")
        requests.append(now)


async def process_upload(
    technology: dict[str, Any],
    upload: UploadFile,
    request_id: str,
) -> dict[str, Any]:
    content = await validate_upload(upload, technology)
    if bool_env("LUTUNG_DEMO_MODE"):
        return {
            "status": "success",
            "data": demo_result(technology["id"]),
            "request_id": request_id,
            "demo": True,
        }

    upstream_url = os.getenv(technology["upstream_url_env"])
    if not upstream_url:
        raise ServiceError(
            503,
            "service_not_configured",
            "Layanan teknologi belum dikonfigurasi pada lingkungan ini.",
        )

    headers = {}
    token = os.getenv(technology["upstream_token_env"])
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=technology["timeout_seconds"]) as client:
            response = await client.post(
                upstream_url,
                headers=headers,
                files={"file": (upload.filename, content, upload.content_type)},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise ServiceError(
            504,
            "upstream_timeout",
            "Layanan membutuhkan waktu terlalu lama.",
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ServiceError(
            502,
            "upstream_failure",
            "Layanan teknologi tidak dapat merespons.",
        ) from exc

    data = payload.get("data", payload) if isinstance(payload, dict) else {"result": payload}
    return {"status": "success", "data": data, "request_id": request_id, "demo": False}


def demo_result(technology_id: str) -> dict[str, Any]:
    if technology_id == "ocr-ktp":
        return {
            "nik": "[DATA DEMO]",
            "nama": "[MODE DEMO]",
            "tempat_lahir": None,
            "tanggal_lahir": None,
            "jenis_kelamin": None,
            "alamat": None,
        }
    if technology_id == "general-ocr":
        return {"text": "[Teks contoh dari mode demo LUTUNG]", "confidence": None, "pages": []}
    return {
        "document_type": "[DEMO]",
        "confidence": None,
        "bounding_box": {"x": 0, "y": 0, "width": 0, "height": 0},
    }
