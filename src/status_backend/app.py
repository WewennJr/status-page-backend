"""Factory de l'application FastAPI."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

    app = FastAPI(title="Status Page", lifespan=lifespan)

    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    if settings.is_dev:
        origins += [
            "http://localhost:8002",
            "http://127.0.0.1:8002",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(public.router)
    app.include_router(admin.router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
