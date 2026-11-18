"""Factory de l'application FastAPI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .checker import scheduler_loop
from .core.config import get_settings
from .database import init_db
from .routers import admin, public

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db()
        task = asyncio.create_task(scheduler_loop())
        logging.getLogger("status").info(
            "Status Page démarré (env=%s)", settings.environment
        )
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="Status Page",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.is_dev else None,
        openapi_url="/api/openapi.json" if settings.is_dev else None,
        redoc_url=None,
    )

    # CORS utile uniquement en développement (Vite sur un autre port).
    # En production, frontend et API sont servis par le même domaine (nginx).
    if settings.is_dev:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # Défense CSRF complémentaire : SameSite=Lax + contrôle de l'Origin
        # sur les méthodes qui modifient l'état.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                parts = urlsplit(origin)
                allowed_hosts = {
                    request.headers.get("host", ""),
                    request.headers.get("x-forwarded-host", ""),
                } - {""}
                # En dev, Vite réécrit le Host (changeOrigin) : on accepte localhost.
                dev_local = settings.is_dev and parts.hostname in {"localhost", "127.0.0.1", "::1"}
                if parts.netloc not in allowed_hosts and not dev_local:
                    return JSONResponse(status_code=403, content={"detail": "Invalid origin"})

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if request.url.path.startswith(("/api/admin", "/api/auth")):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    app.include_router(public.router)
    app.include_router(admin.router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
