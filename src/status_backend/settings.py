"""Paramètres du site (clé/valeur) avec valeurs par défaut."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

DEFAULT_SETTINGS: dict[str, str] = {
    "site_name": "Status Page",
    "site_icon": "",  # emoji ou URL d'icône
    "accent_color": "#ffb454",
    "welcome_text": "Suivi en temps réel de l'état de nos services.",
    "footer_text": "Propulsé par Status Page",
    "default_language": "fr",
    "default_theme": "system",  # light | dark | system
    "retention_days": "90",
    "history_days": "90",
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
