"""Schemas Pydantic."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    uptime_ratio: float | None  # sur la fenêtre history_days
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
    password: str = Field(min_length=8)
    token: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminInfo(BaseModel):
    username: str


# ===== Admin : monitors =====


class MonitorIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=8, max_length=512)
    description: str = ""
    group_name: str = "Services"
    interval_seconds: int = Field(default=60, ge=10, le=86400)
    email: str = ""
    show_response_time: bool = True
    show_uptime: bool = True
    position: int = 0
    active: bool = True


class MonitorOut(MonitorIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ===== Admin : incidents =====


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


# ===== Admin : settings =====


class SettingsIn(BaseModel):
    site_name: str
    site_icon: str
    accent_color: str
    welcome_text: str
    footer_text: str
    default_language: str
    default_theme: str
    retention_days: int
    history_days: int
