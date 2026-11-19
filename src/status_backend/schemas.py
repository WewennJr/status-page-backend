"""Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _max_bcrypt_bytes(value: str) -> str:
    if len(value.encode()) > 72:
        raise ValueError("password must be at most 72 bytes")
    return value


Password = Annotated[
    str, Field(min_length=8, max_length=128), AfterValidator(_max_bcrypt_bytes)
]


# ===== Public =====


class UptimeBar(BaseModel):
    date: str  # YYYY-MM-DD
    ok_ratio: float  # 0..1
    avg_ms: float | None


class MonitorPublic(BaseModel):
    id: int
    name: str
    description: str
    group_name: str
    status: str  # up | down | unknown
    last_check_at: datetime | None
    response_time_ms: float | None
    uptime_ratio: float | None  # over the history_days window
    bars: list[UptimeBar]
    show_response_time: bool
    show_uptime: bool


class IncidentUpdatePublic(BaseModel):
    message: str
    status: str
    created_at: datetime


class IncidentPublic(BaseModel):
    id: int
    monitor_id: int | None
    monitor_name: str | None
    title: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    updates: list[IncidentUpdatePublic]


class PublicOverview(BaseModel):
    setup_done: bool
    settings: dict[str, str]
    monitors: list[MonitorPublic]
    active_incidents: list[IncidentPublic]


class HistoryPoint(BaseModel):
    t: str  # ISO datetime
    avg_ms: float | None
    ok_ratio: float
    checks: int


# ===== Setup / auth =====


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: Password


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AdminInfo(BaseModel):
    username: str


class AuthState(BaseModel):
    authenticated: bool
    username: str | None = None


# ===== Admin: monitors =====


URL_PATTERN = r"^https?://[^\s]+$"


class MonitorIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=8, max_length=512, pattern=URL_PATTERN)
    description: str = Field(default="", max_length=2000)
    group_name: str = Field(default="Services", min_length=1, max_length=64)
    interval_seconds: int = Field(default=60, ge=10, le=86400)
    email: str = Field(default="", max_length=255)
    show_response_time: bool = True
    show_uptime: bool = True
    position: int = Field(default=0, ge=0, le=100000)
    active: bool = True


class MonitorOut(MonitorIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class MonitorOrderItem(BaseModel):
    id: int
    group_name: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=0, le=100000)


class MonitorReorder(BaseModel):
    items: list[MonitorOrderItem] = Field(min_length=1, max_length=500)


# ===== Admin: incidents =====


class IncidentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    monitor_id: int | None = None
    status: str = "investigating"


class IncidentUpdateIn(BaseModel):
    message: str = Field(min_length=1)
    status: str = ""


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    monitor_id: int | None
    title: str
    description: str
    status: str
    auto: bool
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


# ===== Admin: settings =====


HEX_COLOR = r"^$|^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


class SettingsIn(BaseModel):
    site_name: str = Field(min_length=1, max_length=128)
    site_icon: str = Field(default="", max_length=255)
    site_url: str = Field(default="", max_length=255, pattern=r"^$|^https?://[^\s]+$")
    favicon_url: str = Field(default="", max_length=255, pattern=r"^$|^https?://[^\s]+$")
    welcome_text: str = Field(default="", max_length=500)
    footer_text: str = Field(default="", max_length=255)
    accent_color: str = Field(default="", pattern=HEX_COLOR)
    ok_color: str = Field(default="", pattern=HEX_COLOR)
    down_color: str = Field(default="", pattern=HEX_COLOR)
    default_language: Literal["fr", "en"] = "fr"
    default_theme: Literal["system", "light", "dark"] = "system"
    retention_days: int = Field(default=90, ge=7, le=3650)
    history_days: int = Field(default=90, ge=7, le=365)
    default_show_uptime: bool = True
    default_show_response_time: bool = True
    show_incidents_history: bool = True


class AccountUpdate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    current_password: str = Field(min_length=1, max_length=128)
    new_password: Password | None = None
