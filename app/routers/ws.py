import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.models import User
from app.auth import SECRET_KEY, ALGORITHM

router = APIRouter()

# Open sockets keep a Railway Serverless app awake, so every socket has a time cap.
WS_MAX_SECONDS = 30 * 60
IDLE_CLOSE_CODE = 4000  # app-defined (4000-4999): clients show "paused" and offer Resume


class ConnectionManager:
    def __init__(self):
        # user_id -> list of live WebSocket connections for that user
        self.active_connections: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int):
        conns = self.active_connections.get(user_id)
        if conns and websocket in conns:
            conns.remove(websocket)
            if not conns:
                del self.active_connections[user_id]

    async def broadcast_to_user(self, user_id: int, message: dict):
        # Iterate a copy: dead sockets are removed from the live list as we go.
        dead = []
        for connection in list(self.active_connections.get(user_id, [])):
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection, user_id)


manager = ConnectionManager()


def get_user_id_from_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        return int(user_id) if user_id is not None else None
    except JWTError:
        return None


def _get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


async def hold_open(websocket: WebSocket, max_seconds: float) -> None:
    """Drain incoming messages until the client leaves (WebSocketDisconnect
    propagates) or `max_seconds` pass, in which case close with IDLE_CLOSE_CODE."""
    try:
        async with asyncio.timeout(max_seconds):
            while True:
                await websocket.receive_text()
    except TimeoutError:
        await websocket.close(code=IDLE_CLOSE_CODE, reason="idle timeout")


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    user_id = get_user_id_from_token(token)
    if user_id is None:
        await websocket.close(code=1008)  # 1008 = policy violation (standard WS code for auth failure)
        return

    try:
        user = await run_in_threadpool(_get_user_by_id, db, user_id)
    finally:
        # Don't hold a DB connection for the socket's lifetime.
        db.close()
    if user is None:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, user_id)
    try:
        # Dashboard doesn't need to send anything — this just keeps the
        # connection alive and lets us detect disconnects via the exception below.
        await hold_open(websocket, WS_MAX_SECONDS)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, user_id)