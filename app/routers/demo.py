import asyncio
import logging
import random
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.auth import hash_password
from app.database import SessionLocal
from app.ingest import ingest_reading
from app.models import Device, Reading, User
from app.routers.ws import manager

router = APIRouter()
logger = logging.getLogger("uvicorn.error")

DEMO_EMAIL = "demo@example.invalid"
MAX_DEMO_CONNECTIONS = 20
SIMULATE_INTERVAL = 3.0  # seconds, matches simulate_device.py
SNAPSHOT_SIZE = 30

# name -> (device_type, unit, min, max)
DEMO_DEVICES = {
    "Living Room Sensor": ("temperature_sensor", "celsius", 18.0, 30.0),
    "Greenhouse Humidity": ("humidity_sensor", "percent", 40.0, 80.0),
    "Server Room Power": ("power_meter", "watts", 200.0, 600.0),
}

# Overridable so tests can point the simulator at the test database.
session_factory = SessionLocal

_viewers = 0
_sim_task: asyncio.Task | None = None


def seed_demo(db) -> None:
    """Get-or-create the demo user and devices. Safe to run on every boot."""
    user = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if user is None:
        # Random, discarded password: nobody can log in as the demo user.
        user = User(email=DEMO_EMAIL, hashed_password=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
        db.commit()
        db.refresh(user)

    existing = {d.device_name for d in db.query(Device).filter(Device.user_id == user.id)}
    for name, (device_type, _unit, _lo, _hi) in DEMO_DEVICES.items():
        if name not in existing:
            db.add(Device(user_id=user.id, device_name=name, device_type=device_type))
    db.commit()


def _demo_devices(db) -> tuple[int, list[Device]] | None:
    user = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if user is None:
        return None
    devices = db.query(Device).filter(Device.user_id == user.id).order_by(Device.id).all()
    return user.id, devices


def _load_snapshot() -> tuple[int, dict] | None:
    db = session_factory()
    try:
        found = _demo_devices(db)
        if found is None:
            return None
        user_id, devices = found
        out = []
        for device in devices:
            rows = (
                db.query(Reading)
                .filter(Reading.device_id == device.id)
                .order_by(Reading.recorded_at.desc())
                .limit(SNAPSHOT_SIZE)
                .all()
            )
            out.append(
                {
                    "device_id": device.id,
                    "device_name": device.device_name,
                    "device_type": device.device_type,
                    "readings": [
                        {"value": r.value, "unit": r.unit, "recorded_at": r.recorded_at.isoformat()}
                        for r in reversed(rows)
                    ],
                }
            )
        return user_id, {"type": "snapshot", "devices": out}
    finally:
        db.close()


async def _tick() -> None:
    db = session_factory()
    try:
        found = await run_in_threadpool(_demo_devices, db)
        if found is None:
            return
        user_id, devices = found
        for device in devices:
            spec = DEMO_DEVICES.get(device.device_name)
            if spec is None:
                continue
            _type, unit, lo, hi = spec
            await ingest_reading(db, user_id, device.id, round(random.uniform(lo, hi), 2), unit)
    finally:
        db.close()


async def _simulate() -> None:
    while True:
        tick = asyncio.ensure_future(_tick())
        try:
            # Shielded: if we're cancelled mid-write, let the tick finish so its
            # thread isn't still using the session when it is closed.
            await asyncio.shield(tick)
        except asyncio.CancelledError:
            await asyncio.gather(tick, return_exceptions=True)
            raise
        except Exception:
            logger.exception("demo simulator tick failed")
        await asyncio.sleep(SIMULATE_INTERVAL)


def _start_simulator() -> None:
    global _sim_task
    if _sim_task is None:
        _sim_task = asyncio.create_task(_simulate())
        logger.info("demo simulator started")


def _stop_simulator() -> None:
    global _sim_task
    if _sim_task is not None:
        _sim_task.cancel()
        _sim_task = None
        logger.info("demo simulator stopped")


@router.get("/demo", include_in_schema=False)
def demo_page():
    return FileResponse("static/demo.html")


@router.websocket("/demo/ws")
async def demo_ws(websocket: WebSocket):
    """Public, read-only stream of the demo devices. Incoming messages are ignored."""
    global _viewers
    if _viewers >= MAX_DEMO_CONNECTIONS:
        await websocket.accept()
        await websocket.close(code=1013, reason="demo full")  # 1013 = try again later
        return

    # Counted before any await so concurrent connects can't slip past the cap.
    _viewers += 1
    user_id = None
    try:
        loaded = await run_in_threadpool(_load_snapshot)
        if loaded is None:
            await websocket.accept()
            await websocket.close(code=1011, reason="demo not seeded")
            return
        user_id, snapshot = loaded
        await manager.connect(websocket, user_id)
        _start_simulator()
        await websocket.send_json(snapshot)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _viewers -= 1
        if user_id is not None:
            manager.disconnect(websocket, user_id)
        if _viewers == 0:
            _stop_simulator()
