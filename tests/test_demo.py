from contextlib import ExitStack

import pytest
from starlette.testclient import WebSocketDisconnect

from app.models import Device, Reading, User
from app.routers import demo
from app.routers.ws import manager
from conftest import TestSessionLocal
from test_ws import _receive_with_timeout


@pytest.fixture
def seeded_with_simulator(monkeypatch):
    # The simulator opens its own sessions; point them at the test database.
    monkeypatch.setattr(demo, "session_factory", TestSessionLocal)
    with TestSessionLocal() as db:
        demo.seed_demo(db)


@pytest.fixture
def seeded(seeded_with_simulator, monkeypatch):
    # TestClient tears its event loop down as soon as a socket closes, which
    # would abandon an in-flight simulator write and leave its transaction
    # open (blocking the TRUNCATE in conftest). Only the simulator test runs it.
    monkeypatch.setattr(demo, "_start_simulator", lambda: None)


def test_seed_is_idempotent():
    with TestSessionLocal() as db:
        demo.seed_demo(db)
        demo.seed_demo(db)
        assert db.query(User).filter(User.email == demo.DEMO_EMAIL).count() == 1
        assert db.query(Device).count() == len(demo.DEMO_DEVICES)


def test_demo_user_cannot_log_in(client, seeded):
    resp = client.post("/auth/login", json={"email": demo.DEMO_EMAIL, "password": "anything"})
    # 422 today (the .invalid TLD fails EmailStr validation); either way, no login.
    assert resp.status_code in (401, 422)


def test_demo_ws_needs_no_token_and_sends_snapshot(client, seeded):
    with client.websocket_connect("/demo/ws") as ws:
        snapshot = ws.receive_json()
    assert snapshot["type"] == "snapshot"
    names = {d["device_name"] for d in snapshot["devices"]}
    assert names == set(demo.DEMO_DEVICES)


def test_snapshot_contains_at_most_30_recent_readings(client, seeded):
    with TestSessionLocal() as db:
        _user_id, devices = demo._demo_devices(db)
        device_id = devices[0].id
        db.add_all(Reading(device_id=device_id, value=float(i), unit="x") for i in range(40))
        db.commit()
    with client.websocket_connect("/demo/ws") as ws:
        snapshot = ws.receive_json()
    first = next(d for d in snapshot["devices"] if d["device_id"] == device_id)
    assert len(first["readings"]) == 30


def test_simulator_runs_only_while_viewed_and_streams(client, seeded_with_simulator):
    assert demo._sim_task is None
    with client.websocket_connect("/demo/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert demo._sim_task is not None
        # One full tick = one reading per device; wait for all so the tick is
        # finished (session closed) before the socket goes away.
        live = [_receive_with_timeout(ws) for _ in demo.DEMO_DEVICES]
        assert all(m is not None and m["device_name"] in demo.DEMO_DEVICES for m in live)
    assert demo._sim_task is None


def test_demo_sockets_are_registered_only_under_demo_user(client, seeded, alice):
    with client.websocket_connect("/demo/ws") as ws:
        ws.receive_json()
        assert alice["user"]["id"] not in manager.active_connections
        assert len(manager.active_connections) == 1


def test_21st_connection_is_rejected(client, seeded):
    with ExitStack() as stack:
        for _ in range(demo.MAX_DEMO_CONNECTIONS):
            stack.enter_context(client.websocket_connect("/demo/ws"))
        with client.websocket_connect("/demo/ws") as extra:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                extra.receive_json()
        assert exc_info.value.code == 1013
    assert demo._viewers == 0
