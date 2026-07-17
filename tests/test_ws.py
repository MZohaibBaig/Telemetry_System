import queue
import threading

import pytest
from starlette.testclient import WebSocketDisconnect

from conftest import register, login


def _receive_with_timeout(ws, timeout=5.0):
    """Receive one JSON message off `ws`, but never block the test forever.

    The Starlette TestClient's websocket receive() blocks on a cross-thread
    call with no built-in timeout, and the server-side handler is a
    `while True: receive_text()` loop that never sends anything on its own
    initiative for these tests unless a broadcast fires. Running the receive
    in a daemon thread and polling a queue with a timeout lets the test
    always terminate, whether or not a message ever arrives.
    """
    result: queue.Queue = queue.Queue()

    def _worker():
        try:
            result.put(("message", ws.receive_json()))
        except Exception as exc:  # includes WebSocketDisconnect
            result.put(("error", exc))

    threading.Thread(target=_worker, daemon=True).start()
    try:
        kind, payload = result.get(timeout=timeout)
    except queue.Empty:
        return None
    if kind == "error":
        raise payload
    return payload


def _assert_no_message(ws, timeout=1.0):
    """Assert that no message arrives on `ws` within `timeout` seconds."""
    result: queue.Queue = queue.Queue()

    def _worker():
        try:
            result.put(("message", ws.receive_json()))
        except Exception as exc:
            result.put(("error", exc))

    threading.Thread(target=_worker, daemon=True).start()
    try:
        kind, payload = result.get(timeout=timeout)
    except queue.Empty:
        return  # nothing arrived in time - this is what we want
    if kind == "message":
        pytest.fail(f"expected no message to be delivered, but received: {payload}")
    pytest.fail(f"expected no message, but socket raised instead: {payload!r}")


class TestWebSocketBroadcast:
    def test_live_reading_is_pushed_to_the_owning_user(self, client, alice):
        device_resp = client.post(
            "/devices",
            json={"device_name": "Rooftop Sensor", "device_type": "temperature"},
            headers=alice["headers"],
        )
        assert device_resp.status_code == 201
        device_id = device_resp.json()["id"]

        with client.websocket_connect(f"/ws?token={alice['token']}") as ws:
            reading_resp = client.post(
                f"/devices/{device_id}/readings",
                json={"value": 23.4, "unit": "celsius"},
                headers=alice["headers"],
            )
            assert reading_resp.status_code == 201

            message = _receive_with_timeout(ws)
            assert message is not None, "expected a broadcast message but none arrived"
            assert message["device_id"] == device_id
            assert message["value"] == 23.4
            assert message["unit"] == "celsius"


class TestWebSocketAuth:
    def test_invalid_token_is_rejected_with_1008(self, client):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?token=this-is-not-a-real-jwt"):
                pass
        assert exc_info.value.code == 1008

    def test_missing_token_is_rejected(self, client):
        # Confirmed by running: FastAPI's required-Query-param validation
        # failure on this route surfaces client-side as a WebSocketDisconnect
        # with code 1008, the same close code the handler itself uses for
        # invalid tokens - not a 403 denial response.
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 1008


class TestWebSocketUserIsolation:
    def test_users_do_not_receive_each_others_readings(self, client, alice, bob):
        device_resp = client.post(
            "/devices",
            json={"device_name": "Alice's Sensor", "device_type": "temperature"},
            headers=alice["headers"],
        )
        assert device_resp.status_code == 201
        alice_device_id = device_resp.json()["id"]

        with client.websocket_connect(f"/ws?token={bob['token']}") as bob_ws:
            reading_resp = client.post(
                f"/devices/{alice_device_id}/readings",
                json={"value": 99.9, "unit": "celsius"},
                headers=alice["headers"],
            )
            assert reading_resp.status_code == 201

            # SECURITY: user isolation over the WS channel - Bob must not
            # see Alice's reading broadcast on his own socket.
            _assert_no_message(bob_ws)
