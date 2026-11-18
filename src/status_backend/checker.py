"""Scheduler de checks HTTP + incidents automatiques."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from collections.abc import Iterable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import SessionLocal
from .models import (
    CheckResult,
    Incident,
    IncidentUpdate,
    Monitor,
    SessionToken,
    Setting,
    utcnow,
)

logger = logging.getLogger("status.checker")

SCAN_INTERVAL = 10  # seconde entre deux passes de scan
CHECK_TIMEOUT = 10.0
USER_AGENT = "StatusPage/1.0 (+https://github.com)"
DEFAULT_RETENTION_DAYS = 90

# état en mémoire : monitor_id -> next_check_ts
_next_due: dict[int, float] = {}


async def _perform_check(client: httpx.AsyncClient, monitor: Monitor) -> CheckResult:
    started = time.perf_counter()
    ok = False
    status_code: int | None = None
    error = ""
    try:
        response = await client.get(str(monitor.url), follow_redirects=True)
        status_code = response.status_code
        ok = 200 <= response.status_code < 400
        if not ok:
            error = f"HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        error = type(exc).__name__
        detail = str(exc).splitlines()[0][:200] if str(exc) else ""
        if detail:
            error = f"{error}: {detail}"
    except Exception as exc:  # pragma: no cover
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return CheckResult(
        monitor_id=monitor.id,
        checked_at=utcnow(),
        ok=ok,
        status_code=status_code,
        response_time_ms=round(elapsed_ms, 1) if ok else None,
        error=error,
    )


async def _ensure_auto_incident(
    db: AsyncSession, monitor: Monitor, ok: bool, error: str
) -> None:
    result = await db.execute(
        select(Incident).where(
            Incident.monitor_id == monitor.id,
            Incident.auto.is_(True),
            Incident.status != "resolved",
        )
    )
    open_incident = result.scalar_one_or_none()

    if not ok and open_incident is None:
        incident = Incident(
            monitor_id=monitor.id,
            title=f"{monitor.name} est injoignable",
            description=f"Le check automatique a échoué : {error or 'erreur inconnue'}",
            status="investigating",
            auto=True,
        )
        db.add(incident)
        await db.flush()
        db.add(
            IncidentUpdate(
                incident_id=incident.id,
                message=f"Le check a échoué : {error or 'erreur inconnue'}. Investigation en cours.",
                status="investigating",
            )
        )
    elif ok and open_incident is not None:
        open_incident.status = "resolved"
        open_incident.resolved_at = utcnow()
        db.add(
            IncidentUpdate(
                incident_id=open_incident.id,
                message="Le service est de nouveau opérationnel.",
                status="resolved",
            )
        )


async def _run_scan() -> None:
    now_ts = time.monotonic()
    async with SessionLocal() as db:
        monitors: Iterable[Monitor] = (
            await db.scalars(select(Monitor).where(Monitor.active.is_(True)))
        ).all()

        to_check = [
            m for m in monitors if _next_due.get(m.id, 0) <= now_ts
        ]
        if not to_check:
            return

        async with httpx.AsyncClient(
            timeout=CHECK_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            results = await asyncio.gather(
                *(_perform_check(client, m) for m in to_check)
            )

        for monitor, check in zip(to_check, results):
            _next_due[monitor.id] = now_ts + monitor.interval_seconds
            db.add(check)
            try:
                await _ensure_auto_incident(db, monitor, check.ok, check.error)
            except Exception:
                logger.exception("auto incident failed for monitor %s", monitor.id)
        await db.commit()

        if to_check:
            states = ", ".join(
                f"{m.name}={'OK' if c.ok else 'DOWN'}" for m, c in zip(to_check, results)
            )
            logger.debug("checks: %s", states)


async def _cleanup_old_checks() -> None:
    async with SessionLocal() as db:
        result = await db.execute(
            select(Setting).where(Setting.key == "retention_days")
        )
        setting = result.scalar_one_or_none()
        try:
            retention = int(setting.value) if setting else DEFAULT_RETENTION_DAYS
        except ValueError:
            retention = DEFAULT_RETENTION_DAYS
        cutoff = utcnow() - timedelta(days=retention)
        await db.execute(delete(CheckResult).where(CheckResult.checked_at < cutoff))
        # purge des sessions expirées
        await db.execute(delete(SessionToken).where(SessionToken.expires_at < utcnow()))
        await db.commit()


async def scheduler_loop() -> None:
    logger.info("checker démarré (scan toutes les %ss)", SCAN_INTERVAL)
    last_cleanup = 0.0
    while True:
        try:
            await _run_scan()
            if time.monotonic() - last_cleanup > 3600:
                last_cleanup = time.monotonic()
                await _cleanup_old_checks()
        except Exception:
            logger.exception("erreur dans la boucle de checks")
        await asyncio.sleep(SCAN_INTERVAL)
