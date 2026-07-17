from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import auth, devices, readings, ws

app = FastAPI(title="Telemetry System API")

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(readings.router)
app.include_router(ws.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return {"status": "ok", "service": "telemetry-system"}