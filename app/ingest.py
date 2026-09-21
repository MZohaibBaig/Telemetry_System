from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models import Device, Reading, utcnow
from app.routers.ws import manager


def _get_owned_device(device_id: int, user_id: int, db: Session) -> Device:
    device = (
        db.query(Device)
        .filter(Device.id == device_id, Device.user_id == user_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


def _create_reading_sync(
    device_id: int,
    user_id: int,
    value: float,
    unit: str,
    db: Session,
) -> tuple[Reading, Device]:
    device = _get_owned_device(device_id, user_id, db)

    reading = Reading(device_id=device.id, value=value, unit=unit)
    db.add(reading)

    device.last_seen_at = utcnow()
    device.is_online = True

    db.commit()
    db.refresh(reading)
    return reading, device


async def ingest_reading(
    db: Session, user_id: int, device_id: int, value: float, unit: str
) -> Reading:
    """Insert a reading, update the device's last_seen, and broadcast it.

    Shared by POST /devices/{id}/readings and the demo simulator.
    """
    reading, device = await run_in_threadpool(
        _create_reading_sync, device_id, user_id, value, unit, db
    )

    await manager.broadcast_to_user(
        user_id,
        {
            "device_id": device.id,
            "device_name": device.device_name,
            "value": reading.value,
            "unit": reading.unit,
            "recorded_at": reading.recorded_at.isoformat(),
        },
    )
    return reading
