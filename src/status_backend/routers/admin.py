"""Endpoints admin (setup first-run + CRUD protégé)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    SESSION_COOKIE,
    create_session,
    get_current_admin,
    hash_password,
    verify_password,
)
from ..core.config import get_settings as get_app_settings
from ..database import get_db
from ..models import Admin, CheckResult, Incident, IncidentUpdate, Monitor, SessionToken, utcnow
from ..schemas import (
    AdminInfo,
    IncidentIn,
    IncidentOut,
    IncidentUpdateIn,
    LoginRequest,
    MonitorIn,
    MonitorOut,
    SettingsIn,
    SetupRequest,
)
from ..settings import DEFAULT_SETTINGS, get_settings, save_settings

router = APIRouter(prefix="/api", tags=["admin"])


# ===== Setup first-run =====


@router.get("/setup-required")
async def setup_required(db: AsyncSession = Depends(get_db)):
    count = len((await db.scalars(select(Admin))).all())
    return {
        "setup_required": count == 0,
        "token_required": bool(get_app_settings().setup_token),
    }


def _is_https(request: Request) -> bool:
    # nginx proxifie avec X-Forwarded-Proto ; uvicorn fait confiance via proxy_headers
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
    count = len((await db.scalars(select(Admin))).all())
    if count > 0:
        raise HTTPException(status_code=403, detail="Setup already done")

    expected_token = get_app_settings().setup_token
    if expected_token and not secrets.compare_digest(payload.token, expected_token):
        raise HTTPException(status_code=403, detail="Invalid setup token")

    admin = Admin(username=payload.username, password_hash=hash_password(payload.password))
    db.add(admin)
    await db.flush()
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
    admin = (
        (await db.execute(select(Admin).where(Admin.username == payload.username)))
        .scalars()
        .first()
    )
    if admin is None or not verify_password(payload.password, admin.password_hash):
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


@router.get("/auth/me", response_model=AdminInfo)
async def me(admin: Admin = Depends(get_current_admin)):
    return AdminInfo(username=admin.username)


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
    limit: int = 50,
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
    values = {k: str(v) for k, v in payload.model_dump().items()}
    await save_settings(db, values)
    return await get_settings(db)


# ===== Admin : stats (pour le dashboard) =====


@router.get("/admin/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db), _admin: Admin = Depends(get_current_admin)
):
    monitors = len((await db.scalars(select(Monitor))).all())
    checks = (await db.scalar(select(func.count(CheckResult.id)))) or 0
    open_incidents = len(
        (await db.scalars(select(Incident).where(Incident.status != "resolved"))).all()
    )
    return {"monitors": monitors, "checks_stored": checks, "open_incidents": open_incidents}
