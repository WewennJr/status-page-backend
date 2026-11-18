"""Configuration de l'application, chargée depuis status-backend/.env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# .env à la racine du backend (src/status_backend/core/config.py -> 3 parents au-dessus)
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class Settings:
    environment: str  # "development" | "production"
    setup_token: str  # si défini, requis par /api/setup (anti-bot)

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


def get_settings() -> Settings:
    return Settings(
        environment=os.environ.get("STATUS_ENVIRONMENT", "development"),
        setup_token=os.environ.get("STATUS_SETUP_TOKEN", ""),
    )
