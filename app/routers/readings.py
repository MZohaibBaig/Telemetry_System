from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Device, Reading, utcnow
from app.schemas import ReadingCreate, ReadingOut, ReadingAggregateOut
from app.auth import get_current_user
from app.routers.ws import manager

router = APIRouter(prefix="/devices/{device_id}/readings", tags=["readings"])


def _get_owned_device(device_id: int, current_user: User, db: Session) -> Device:
    device = (
        db.query(Device)
        .filter(Device.id == device_id, Device.user_id == current_user.id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.post("", response_model=ReadingOut, status_code=status.HTTP_201_CREATED)
async def create_reading(
    device_id: int,
    reading_in: ReadingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_owned_device(device_id, current_user, db)

    reading = Reading(
        device_id=device.id,
        value=reading_in.value,
        unit=reading_in.unit,
    )
    db.add(reading)

    device.last_seen_at = utcnow()
    device.is_online = True

    db.commit()
    db.refresh(reading)

    await manager.broadcast_to_user(
        current_user.id,
        {
            "device_id": device.id,
            "device_name": device.device_name,
            "value": reading.value,
            "unit": reading.unit,
            "recorded_at": reading.recorded_at.isoformat(),
        },
    )

    return reading


@router.get("/recent", response_model=list[ReadingOut])
def get_recent_readings(
    device_id: int,
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_owned_device(device_id, current_user, db)
    return (
        db.query(Reading)
        .filter(Reading.device_id == device.id)
        .order_by(Reading.recorded_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/range", response_model=list[ReadingOut])
def get_readings_in_range(
    device_id: int,
    start: datetime = Query(...),
    end: datetime = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_owned_device(device_id, current_user, db)
    return (
        db.query(Reading)
        .filter(
            Reading.device_id == device.id,
            Reading.recorded_at >= start,
            Reading.recorded_at <= end,
        )
        .order_by(Reading.recorded_at.asc())
        .all()
    )


@router.get("/aggregate", response_model=list[ReadingAggregateOut])
def get_aggregated_readings(
    device_id: int,
    bucket: str = Query("1 hour", description="Postgres interval string, e.g. '15 minutes', '1 hour', '1 day'"),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = _get_owned_device(device_id, current_user, db)

    if end is None:
        end = utcnow()
    if start is None:
        start = end - timedelta(hours=24)

    query = text("""
        SELECT
            time_bucket(CAST(:bucket AS INTERVAL), recorded_at) AS bucket,
            AVG(value) AS avg_value,
            MIN(value) AS min_value,
            MAX(value) AS max_value
        FROM readings
        WHERE device_id = :device_id
          AND recorded_at >= :start
          AND recorded_at <= :end
        GROUP BY bucket
        ORDER BY bucket
    """)

    rows = db.execute(
        query,
        {"bucket": bucket, "device_id": device.id, "start": start, "end": end},
    ).fetchall()

    return [
        ReadingAggregateOut(
            bucket=row.bucket,
            avg_value=float(row.avg_value),
            min_value=float(row.min_value),
            max_value=float(row.max_value),
        )
        for row in rows
    ]