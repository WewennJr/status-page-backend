"""Endpoints publics (lecture seule)."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Admin, CheckResult, Incident, IncidentUpdate, Monitor, utcnow
from ..schemas import (
    HistoryPoint,
    IncidentPublic,
    IncidentUpdatePublic,
    MonitorPublic,
    PublicOverview,
    UptimeBar,
)
from ..settings import get_settings

router = APIRouter(prefix="/api/public", tags=["public"])


def _days_ago(n: int):
    return utcnow() - timedelta(days=n)


async def _monitors_with_status(db: AsyncSession, history_days: int):
    monitors = (
        await db.scalars(
            select(Monitor).where(Monitor.active.is_(True)).order_by(Monitor.position, Monitor.id)
        )
    ).all()

    # dernier check par monitor
    last_checks: dict[int, CheckResult] = {}
    result = await db.execute(
        select(CheckResult)
        .where(
            CheckResult.monitor_id.in_([m.id for m in monitors] or [0]),
            CheckResult.id.in_(
                select(func.max(CheckResult.id)).group_by(CheckResult.monitor_id)
            ),
        )
    )
    for check in result.scalars().all():
        last_checks[check.monitor_id] = check

    # agrégats par jour sur la fenêtre history_days
    cutoff = _days_ago(history_days)
    agg = await db.execute(
        select(
            CheckResult.monitor_id,
            func.date(CheckResult.checked_at).label("day"),
            func.avg(
                case((CheckResult.ok.is_(True), 1.0), else_=0.0)
            ).label("ok_ratio"),
            func.avg(CheckResult.response_time_ms).label("avg_ms"),
            func.count(CheckResult.id).label("n"),
        )
        .where(CheckResult.checked_at >= cutoff)
        .group_by(CheckResult.monitor_id, "day")
        .order_by("day")
    )
    by_monitor_day: dict[int, dict[str, tuple[float, float | None]]] = {}
    overall: dict[int, tuple[float, int]] = {}
    for row in agg.all():
        by_monitor_day.setdefault(row.monitor_id, {})[row.day] = (
            float(row.ok_ratio or 0),
            row.avg_ms,
        )
        if row.monitor_id not in overall:
            overall[row.monitor_id] = [0.0, 0]
        acc = overall[row.monitor_id]
        acc[0] += float(row.ok_ratio or 0) * int(row.n)
        acc[1] += int(row.n)

    return monitors, last_checks, by_monitor_day, overall


@router.get("/overview", response_model=PublicOverview)
async def overview(db: AsyncSession = Depends(get_db)):
    admin_count = len((await db.scalars(select(Admin))).all())
    settings = await get_settings(db)
    history_days = int(settings.get("history_days", 90))

    monitors, last_checks, by_monitor_day, overall = await _monitors_with_status(
        db, history_days
    )

    monitor_public: list[MonitorPublic] = []
    for m in monitors:
        days = by_monitor_day.get(m.id, {})
        bars = [
            UptimeBar(
                date=day,
                ok_ratio=round(ratio, 4),
                avg_ms=round(avg_ms, 1) if avg_ms is not None else None,
            )
            for day, (ratio, avg_ms) in sorted(days.items())
        ]
        total, count = overall.get(m.id, (0.0, 0))
        last = last_checks.get(m.id)
        monitor_public.append(
            MonitorPublic(
                id=m.id,
                name=m.name,
                description=m.description or "",
                group_name=m.group_name or "Services",
                status="up" if (last and last.ok) else ("down" if last else "unknown"),
                last_check_at=last.checked_at if last else None,
                response_time_ms=last.response_time_ms if last else None,
                uptime_ratio=round(total / count, 4) if count else None,
                bars=bars,
                show_response_time=m.show_response_time,
                show_uptime=m.show_uptime,
            )
        )

    active = (
        await db.scalars(
            select(Incident)
            .where(Incident.status != "resolved")
            .order_by(Incident.created_at.desc())
        )
    ).all()
    active_public = await _incidents_public(db, list(active))

    return PublicOverview(
        setup_done=admin_count > 0,
        settings=settings,
        monitors=monitor_public,
        active_incidents=active_public,
    )


async def _incidents_public(db: AsyncSession, incidents: list[Incident]) -> list[IncidentPublic]:
    """Sérialise une liste d'incidents en 2 requêtes (noms + updates), sans N+1."""
    if not incidents:
        return []

    ids = [i.id for i in incidents]
    monitor_ids = [i.monitor_id for i in incidents if i.monitor_id]
    names: dict[int, str] = {}
    if monitor_ids:
        rows = await db.execute(
            select(Monitor.id, Monitor.name).where(Monitor.id.in_(monitor_ids))
        )
        names = {row.id: row.name for row in rows.all()}

    updates_rows = (
        await db.scalars(
            select(IncidentUpdate)
            .where(IncidentUpdate.incident_id.in_(ids))
            .order_by(IncidentUpdate.created_at)
        )
    ).all()
    updates_by: dict[int, list[IncidentUpdate]] = defaultdict(list)
    for u in updates_rows:
        updates_by[u.incident_id].append(u)

    return [
        IncidentPublic(
            id=incident.id,
            monitor_id=incident.monitor_id,
            monitor_name=names.get(incident.monitor_id) if incident.monitor_id else None,
            title=incident.title,
            description=incident.description or "",
            status=incident.status,
            created_at=incident.created_at,
            updated_at=incident.updated_at,
            resolved_at=incident.resolved_at,
            updates=[
                IncidentUpdatePublic(
                    message=u.message, status=u.status, created_at=u.created_at
                )
                for u in updates_by.get(incident.id, [])
            ],
        )
        for incident in incidents
    ]


@router.get("/incidents", response_model=list[IncidentPublic])
async def incidents_history(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
):
    resolved = (
        await db.scalars(
            select(Incident)
            .where(Incident.status == "resolved")
            .order_by(Incident.updated_at.desc())
            .limit(limit)
        )
    ).all()
    return await _incidents_public(db, list(resolved))


@router.get("/monitor/{monitor_id}/history", response_model=list[HistoryPoint])
async def monitor_history(
    monitor_id: int,
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=1, ge=1, le=365),
):
    monitor = await db.get(Monitor, monitor_id)
    if monitor is None or not monitor.active:
        raise HTTPException(status_code=404, detail="Monitor not found")

    cutoff = _days_ago(days)
    if days <= 1:
        bucket = func.substr(CheckResult.checked_at, 1, 16)  # par minute
    elif days <= 7:
        bucket = func.strftime("%Y-%m-%d %H:00", CheckResult.checked_at)  # par heure
    else:
        bucket = func.date(CheckResult.checked_at)  # par jour

    agg = await db.execute(
        select(
            bucket.label("bucket"),
            func.avg(CheckResult.response_time_ms).label("avg_ms"),
            func.avg(case((CheckResult.ok.is_(True), 1.0), else_=0.0)).label("ok_ratio"),
            func.count(CheckResult.id).label("n"),
        )
        .where(CheckResult.monitor_id == monitor_id, CheckResult.checked_at >= cutoff)
        .group_by("bucket")
        .order_by("bucket")
    )
    return [
        HistoryPoint(
            t=row.bucket,
            avg_ms=round(row.avg_ms, 1) if row.avg_ms is not None else None,
            ok_ratio=round(float(row.ok_ratio or 0), 4),
            checks=int(row.n),
        )
        for row in agg.all()
    ]
