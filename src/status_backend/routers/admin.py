"""Admin endpoints (first-run setup + protected CRUD)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import case, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE,
    create_session,
    get_current_admin,
    get_optional_admin,
    hash_password,
    verify_password,
)
from ..database import get_db
from ..models import Admin, CheckResult, Incident, IncidentUpdate, Monitor, SessionToken, utcnow
from ..ratelimit import client_key, login_limiter, setup_limiter
from ..schemas import (
    AccountUpdate,
    AdminInfo,
    AuthState,
    IncidentIn,
    IncidentOut,
    IncidentUpdateIn,
    LoginRequest,
    MonitorIn,
    MonitorOut,
    MonitorReorder,
    SettingsIn,
    SetupRequest,
)
from ..settings import DEFAULT_SETTINGS, get_settings, save_settings

router = APIRouter(prefix="/api", tags=["admin"])


# ===== Setup first-run =====


@router.get("/setup-required")
async def setup_required(db: AsyncSession = Depends(get_db)):
    count = len((await db.scalars(select(Admin))).all())
    return {"setup_required": count == 0}


def _is_https(request: Request) -> bool:
    # nginx proxies with X-Forwarded-Proto; uvicorn trusts it via proxy_headers
    return request.headers.get("x-forwarded-proto") == "https"


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=14 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
        path="/",
    )


@router.post("/setup")
async def setup(
    payload: SetupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    setup_limiter.check(client_key(request))
    # Atomic conditional INSERT: prevents two concurrent setups from creating
    # two administrator accounts.
    result = await db.execute(
        text(
            "INSERT INTO admins (username, password_hash, created_at) "
            "SELECT :username, :password_hash, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM admins)"
        ),
        {
            "username": payload.username,
            "password_hash": hash_password(payload.password),
        },
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=403, detail="Setup already done")

    admin = (
        (await db.execute(select(Admin).where(Admin.username == payload.username)))
        .scalars()
        .one()
    )
    session = await create_session(db, admin.id)
    await save_settings(db, dict(DEFAULT_SETTINGS))
    _set_session_cookie(response, session.token, request)
    return {"ok": True}


# ===== Auth =====


@router.post("/auth/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    login_limiter.check(client_key(request))
    admin = (
        (await db.execute(select(Admin).where(Admin.username == payload.username)))
        .scalars()
        .first()
    )
    if admin is None:
        verify_password(payload.password, DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    session = await create_session(db, admin.id)
    _set_session_cookie(response, session.token, request)
    return AdminInfo(username=admin.username)


@router.post("/auth/logout")
async def logout(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.execute(delete(SessionToken).where(SessionToken.token == token))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me", response_model=AuthState)
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await get_optional_admin(request, db)
    return AuthState(
        authenticated=admin is not None,
        username=admin.username if admin else None,
    )


# ===== Admin : monitors =====


@router.get("/admin/monitors", response_model=list[MonitorOut])
async def list_monitors(
    db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    return (
        await db.scalars(select(Monitor).order_by(Monitor.position, Monitor.id))
    ).all()


@router.post("/admin/monitors", response_model=MonitorOut)
async def create_monitor(
    payload: MonitorIn, db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    monitor = Monitor(**payload.model_dump())
    db.add(monitor)
    await db.commit()
    await db.refresh(monitor)
    return monitor


@router.post("/admin/monitors/reorder")
async def reorder_monitors(
    payload: MonitorReorder,
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    ids = [item.id for item in payload.items]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Duplicate monitor ids")
    found = {
        m.id: m
        for m in (
            await db.scalars(select(Monitor).where(Monitor.id.in_(ids)))
        ).all()
    }
    if len(found) != len(ids):
        raise HTTPException(status_code=404, detail="Monitor not found")
    for item in payload.items:
        monitor = found[item.id]
        monitor.group_name = item.group_name
        monitor.position = item.position
    await db.commit()
    return {"ok": True}


@router.put("/admin/monitors/{monitor_id}", response_model=MonitorOut)
async def update_monitor(
    monitor_id: int,
    payload: MonitorIn,
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    monitor = await db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    for key, value in payload.model_dump().items():
        setattr(monitor, key, value)
    await db.commit()
    await db.refresh(monitor)
    return monitor


@router.delete("/admin/monitors/{monitor_id}")
async def delete_monitor(
    monitor_id: int, db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    monitor = await db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    await db.delete(monitor)
    await db.commit()
    return {"ok": True}


# ===== Admin : incidents =====


def _validate_status(status: str) -> None:
    allowed = {"investigating", "identified", "monitoring", "resolved"}
    if status not in allowed:
        raise HTTPException(status_code=422, detail=f"Invalid status: {status}")


@router.get("/admin/incidents", response_model=list[IncidentOut])
async def list_incidents(
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
    limit: int = Query(default=50, ge=1, le=200),
):
    return (
        await db.scalars(
            select(Incident).order_by(Incident.updated_at.desc()).limit(limit)
        )
    ).all()


@router.post("/admin/incidents", response_model=IncidentOut)
async def create_incident(
    payload: IncidentIn, db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    _validate_status(payload.status)
    if payload.monitor_id is not None:
        if await db.get(Monitor, payload.monitor_id) is None:
            raise HTTPException(status_code=404, detail="Monitor not found")
    incident = Incident(**payload.model_dump())
    db.add(incident)
    await db.flush()
    db.add(
        IncidentUpdate(
            incident_id=incident.id,
            message=payload.description or payload.title,
            status=payload.status,
        )
    )
    await db.commit()
    await db.refresh(incident)
    return incident


@router.put("/admin/incidents/{incident_id}", response_model=IncidentOut)
async def update_incident(
    incident_id: int,
    payload: IncidentIn,
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    _validate_status(payload.status)
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.title = payload.title
    incident.description = payload.description
    incident.monitor_id = payload.monitor_id
    if incident.status != payload.status:
        incident.status = payload.status
        incident.resolved_at = utcnow() if payload.status == "resolved" else None
    await db.commit()
    await db.refresh(incident)
    return incident


@router.post("/admin/incidents/{incident_id}/updates", response_model=IncidentOut)
async def add_incident_update(
    incident_id: int,
    payload: IncidentUpdateIn,
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if payload.status:
        _validate_status(payload.status)
        incident.status = payload.status
        incident.resolved_at = utcnow() if payload.status == "resolved" else None
    db.add(IncidentUpdate(incident_id=incident.id, message=payload.message, status=payload.status))
    await db.commit()
    await db.refresh(incident)
    return incident


@router.delete("/admin/incidents/{incident_id}")
async def delete_incident(
    incident_id: int, db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    await db.delete(incident)
    await db.commit()
    return {"ok": True}


# ===== Admin : settings =====


@router.get("/admin/settings")
async def read_settings(
    db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    return await get_settings(db)


@router.put("/admin/settings")
async def write_settings(
    payload: SettingsIn, db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    values = {
        k: ("true" if v is True else "false" if v is False else str(v))
        for k, v in payload.model_dump().items()
    }
    await save_settings(db, values)
    return await get_settings(db)


# ===== Admin : compte =====


@router.get("/admin/account")
async def read_account(admin: Admin = Depends(get_current_admin)):
    return {"username": admin.username}


@router.put("/admin/account")
async def update_account(
    payload: AccountUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    if payload.username != admin.username:
        existing = (
            (await db.execute(select(Admin).where(Admin.username == payload.username)))
            .scalars()
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Username already taken")
        admin.username = payload.username

    if payload.new_password:
        admin.password_hash = hash_password(payload.new_password)
        # invalidate other sessions (keep the current one)
        current_token = request.cookies.get(SESSION_COOKIE)
        await db.execute(
            delete(SessionToken).where(
                SessionToken.admin_id == admin.id,
                SessionToken.token != current_token,
            )
        )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Username already taken")
    return {"username": admin.username}


# ===== Admin : stats (dashboard) =====


@router.get("/admin/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    now = utcnow()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_30d = now - timedelta(days=30)

    monitors = (
        await db.scalars(select(Monitor).order_by(Monitor.position, Monitor.id))
    ).all()

    checks_stored = (await db.scalar(select(func.count(CheckResult.id)))) or 0
    checks_24h = (
        await db.scalar(
            select(func.count(CheckResult.id)).where(CheckResult.checked_at >= cutoff_24h)
        )
    ) or 0
    avg_24h = await db.scalar(
        select(func.avg(CheckResult.response_time_ms)).where(
            CheckResult.checked_at >= cutoff_24h
        )
    )
    open_incidents = (
        await db.scalar(
            select(func.count(Incident.id)).where(Incident.status != "resolved")
        )
    ) or 0
    incidents_30d = (
        await db.scalar(
            select(func.count(Incident.id)).where(Incident.created_at >= cutoff_30d)
        )
    ) or 0

    # Global uptime series (30 days)
    uptime_rows = (
        await db.execute(
            select(
                func.date(CheckResult.checked_at).label("day"),
                func.avg(case((CheckResult.ok.is_(True), 1.0), else_=0.0)).label("ok_ratio"),
                func.avg(CheckResult.response_time_ms).label("avg_ms"),
            )
            .where(CheckResult.checked_at >= cutoff_30d)
            .group_by("day")
            .order_by("day")
        )
    ).all()

    # Global response time series (24 h, per hour)
    response_rows = (
        await db.execute(
            select(
                func.strftime("%Y-%m-%d %H:00", CheckResult.checked_at).label("bucket"),
                func.avg(CheckResult.response_time_ms).label("avg_ms"),
                func.avg(case((CheckResult.ok.is_(True), 1.0), else_=0.0)).label("ok_ratio"),
                func.count(CheckResult.id).label("n"),
            )
            .where(CheckResult.checked_at >= cutoff_24h)
            .group_by("bucket")
            .order_by("bucket")
        )
    ).all()

    # Last check + uptime per monitor
    last_checks = {
        c.monitor_id: c
        for c in (
            await db.scalars(
                select(CheckResult).where(
                    CheckResult.id.in_(
                        select(func.max(CheckResult.id)).group_by(CheckResult.monitor_id)
                    )
                )
            )
        ).all()
    }
    uptime_by_monitor = {
        row.monitor_id: float(row.ok_ratio or 0)
        for row in (
            await db.execute(
                select(
                    CheckResult.monitor_id,
                    func.avg(case((CheckResult.ok.is_(True), 1.0), else_=0.0)).label(
                        "ok_ratio"
                    ),
                )
                .where(CheckResult.checked_at >= cutoff_30d)
                .group_by(CheckResult.monitor_id)
            )
        ).all()
    }

    monitor_statuses = []
    up = down = unknown = 0
    for m in monitors:
        last = last_checks.get(m.id)
        status = "up" if (last and last.ok) else ("down" if last else "unknown")
        if status == "up":
            up += 1
        elif status == "down":
            down += 1
        else:
            unknown += 1
        monitor_statuses.append(
            {
                "id": m.id,
                "name": m.name,
                "group_name": m.group_name,
                "active": m.active,
                "status": status,
                "response_time_ms": last.response_time_ms if last else None,
                "last_check_at": (last.checked_at.isoformat() if last else None),
                "uptime_ratio": uptime_by_monitor.get(m.id),
            }
        )

    return {
        "monitors": len(monitors),
        "monitors_up": up,
        "monitors_down": down,
        "monitors_unknown": unknown,
        "checks_stored": checks_stored,
        "checks_24h": checks_24h,
        "avg_response_ms_24h": round(avg_24h, 1) if avg_24h is not None else None,
        "open_incidents": open_incidents,
        "incidents_30d": incidents_30d,
        "uptime_series": [
            {
                "date": row.day,
                "ok_ratio": round(float(row.ok_ratio or 0), 4),
                "avg_ms": round(row.avg_ms, 1) if row.avg_ms is not None else None,
            }
            for row in uptime_rows
        ],
        "response_series": [
            {
                "t": row.bucket,
                "avg_ms": round(row.avg_ms, 1) if row.avg_ms is not None else None,
                "ok_ratio": round(float(row.ok_ratio or 0), 4),
                "checks": int(row.n),
            }
            for row in response_rows
        ],
        "monitor_statuses": monitor_statuses,
    }
