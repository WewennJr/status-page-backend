"""Site settings (key/value) with default values."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

DEFAULT_SETTINGS: dict[str, str] = {
    # Identity
    "site_name": "Status Page",
    "site_icon": "",  # emoji or URL
    "site_url": "",  # optional link on the brand
    "favicon_url": "",
    "welcome_text": "Real-time status of our services.",
    "footer_text": "Made with ♥ by WewennJr",
    # Appearance
    "accent_color": "",  # empty = theme colors
    "ok_color": "",
    "down_color": "",
    "default_language": "fr",
    "default_theme": "system",  # light | dark | system
    # Behavior
    "retention_days": "90",
    "history_days": "90",
    "default_show_uptime": "true",
    "default_show_response_time": "true",
    "show_incidents_history": "true",
}


async def get_settings(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(Setting))
    values = {s.key: s.value for s in result.scalars().all()}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(values)
    return merged


async def save_settings(db: AsyncSession, values: dict[str, str]) -> None:
    for key, value in values.items():
        setting = await db.get(Setting, key)
        if setting is None:
            setting = Setting(key=key, value=value)
            db.add(setting)
        else:
            setting.value = value
    await db.commit()
