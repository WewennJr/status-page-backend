"""Application configuration, loaded from status-backend/.env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class Settings:
    environment: str  # "development" | "production"

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


def get_settings() -> Settings:
    return Settings(environment=os.environ.get("STATUS_ENVIRONMENT", "development"))
