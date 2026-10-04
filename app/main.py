from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.services import (
    RateLimiter,
    ServiceError,
    bool_env,
    find_by_operation,
    find_technology,
    load_registry,
    process_upload,
)

BASE_DIR = Path(__file__).resolve().parent
STATUS_LABELS = {
    "available": "Tersedia",
    "beta": "Beta",
    "development": "Dalam pengembangan",
    "coming-soon": "Segera hadir",
    "maintenance": "Pemeliharaan",
}
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("lutung")
registry = load_registry()
limiter = RateLimiter(
    limit=int(os.getenv("LUTUNG_RATE_LIMIT", "20")),
    window_seconds=int(os.getenv("LUTUNG_RATE_WINDOW_SECONDS", "60")),
)

app = FastAPI(
    title="LUTUNG PTK",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)
allowed_hosts = os.getenv("LUTUNG_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def template_context(request: Request, **values: Any) -> dict[str, Any]:
    return {
        "request": request,
        "statuses": STATUS_LABELS,
        "demo_mode": bool_env("LUTUNG_DEMO_MODE"),
        **values,
    }


@app.middleware("http")
async def request_controls(request: Request, call_next: Any) -> Any:
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request.state.request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid.uuid4())
    )
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob: data:; "
        "style-src 'self'; script-src 'self'; connect-src 'self'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    logger.info(
        "request_complete method=%s path=%s status=%s duration_ms=%d request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        int((time.monotonic() - started) * 1000),
        request.state.request_id,
    )
    return response


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error": {"code": exc.code, "message": exc.message},
            "request_id": request.state.request_id,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "home.html",
        template_context(request, title="Beranda"),
    )


@app.get("/technologies", response_class=HTMLResponse)
async def catalog(
    request: Request,
    q: str = "",
    category: str = "",
    status: str = "",
) -> HTMLResponse:
    query = q.casefold().strip()
    technologies = registry["technologies"]
    if query:
        technologies = [
            item
            for item in technologies
            if query
            in " ".join(
                [
                    item["name"],
                    item["short_description"],
                    item["category"],
                    *item.get("capabilities", []),
                ]
            ).casefold()
        ]
    if category:
        technologies = [item for item in technologies if item["category"] == category]
    if status:
        technologies = [item for item in technologies if item["status"] == status]
    return templates.TemplateResponse(
        request,
        "catalog.html",
        template_context(
            request,
            title="Teknologi",
            technologies=technologies,
            categories=registry["categories"],
            filters={"q": q, "category": category, "status": status},
        ),
    )


@app.get("/technologies/{technology_id}", response_class=HTMLResponse)
async def technology_detail(request: Request, technology_id: str) -> HTMLResponse:
    technology = find_technology(registry, technology_id)
    if technology is None:
        return templates.TemplateResponse(
            request,
            "not_found.html",
            template_context(request, title="Tidak ditemukan"),
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "technology.html",
        template_context(request, title=technology["name"], technology=technology),
    )


@app.get("/playground/{technology_id}", response_class=HTMLResponse)
async def playground(request: Request, technology_id: str) -> HTMLResponse:
    technology = find_technology(registry, technology_id)
    if technology is None or not technology.get("playground"):
        return templates.TemplateResponse(
            request,
            "not_found.html",
            template_context(request, title="Playground tidak ditemukan"),
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "playground.html",
        template_context(request, title=f"Playground {technology['name']}", technology=technology),
    )


@app.get("/api", response_class=HTMLResponse)
async def api_index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "api_index.html",
        template_context(
            request,
            title="Dokumentasi API",
            technologies=[item for item in registry["technologies"] if item.get("api")],
        ),
    )


@app.get("/api/docs/{technology_id}", response_class=HTMLResponse)
async def api_documentation(request: Request, technology_id: str) -> HTMLResponse:
    technology = find_technology(registry, technology_id)
    if technology is None or not technology.get("api"):
        return templates.TemplateResponse(
            request,
            "not_found.html",
            template_context(request, title="Dokumentasi tidak ditemukan"),
            status_code=404,
        )
    base_url = str(request.base_url).rstrip("/")
    operation_url = f"{base_url}{technology['operation']}"
    examples = {
        "curl": (
            f"curl -X POST {operation_url} \\\n"
            f'  -F "file=@document{technology["accepted_extensions"][0]}"'
        ),
        "python": (
            "import requests\n\n"
            f'with open("document{technology["accepted_extensions"][0]}", "rb") as file:\n'
            f'    response = requests.post("{operation_url}", files={{"file": file}})\n'
            "response.raise_for_status()\n"
            "print(response.json())"
        ),
        "javascript": (
            "const form = new FormData();\n"
            'form.append("file", fileInput.files[0]);\n\n'
            f'const response = await fetch("{operation_url}", {{\n'
            '  method: "POST",\n'
            "  body: form,\n"
            "});\n"
            "console.log(await response.json());"
        ),
    }
    return templates.TemplateResponse(
        request,
        "api_docs.html",
        template_context(
            request,
            title=f"API {technology['name']}",
            technology=technology,
            operation_url=operation_url,
            examples=examples,
        ),
    )


@app.post("/api/v1/ocr/ktp")
@app.post("/api/v1/ocr/general")
@app.post("/api/v1/document/detect")
async def technology_api(
    request: Request,
    file: Annotated[UploadFile, File(...)],
) -> JSONResponse:
    technology = find_by_operation(registry, request.url.path)
    if technology is None:
        raise ServiceError(404, "technology_not_found", "Teknologi tidak ditemukan.")
    client_key = request.client.host if request.client else "unknown"
    limiter.check(client_key)
    result = await process_upload(technology, file, request.state.request_id)
    return JSONResponse(result)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "portal": "LUTUNG PTK",
        "technologies": {
            item["id"]: {
                "catalog_status": item["status"],
                "configured": bool(os.getenv(item["upstream_url_env"]))
                if item.get("upstream_url_env")
                else False,
            }
            for item in registry["technologies"]
            if item.get("playground")
        },
    }
