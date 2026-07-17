from datetime import datetime
from pydantic import BaseModel, EmailStr


# ---------- Auth ----------

class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- Device ----------

class DeviceCreate(BaseModel):
    device_name: str
    device_type: str


class DeviceOut(BaseModel):
    id: int
    device_name: str
    device_type: str
    is_online: bool
    last_seen_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Reading ----------

class ReadingCreate(BaseModel):
    value: float
    unit: str


class ReadingOut(BaseModel):
    id: int
    device_id: int
    value: float
    unit: str
    recorded_at: datetime

    class Config:
        from_attributes = True

class ReadingAggregateOut(BaseModel):
    bucket: datetime
    avg_value: float
    min_value: float
    max_value: float