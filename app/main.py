from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.db_init import init_db
from app.routers import auth, devices, readings, ws

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Telemetry System API", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(readings.router)
app.include_router(ws.router)

app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")


@app.get("/")
def root():
    return {"status": "ok", "service": "telemetry-system"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}