from conftest import ALICE_EMAIL, BOB_EMAIL, register, login, auth_header

# `alice` and `bob` fixtures are defined in conftest.py and auto-discovered
# by pytest; no import needed to use them as test arguments.


# ---------- Registration ----------


class TestRegistration:
    def test_register_returns_201_with_user(self, client):
        resp = register(client)
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == ALICE_EMAIL
        assert "id" in body
        assert "hashed_password" not in body
        assert "password" not in body

    def test_duplicate_email_returns_400(self, client):
        first = register(client)
        assert first.status_code == 201

        dup = register(client)
        assert dup.status_code == 400


# ---------- Login ----------


class TestLogin:
    def test_login_returns_jwt_access_token(self, client):
        register(client)

        resp = login(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert isinstance(body["access_token"], str) and body["access_token"]

    def test_login_wrong_password_returns_401(self, client):
        register(client)

        resp = login(client, password="not-the-right-password")
        assert resp.status_code == 401


# ---------- /auth/me ----------


class TestMe:
    def test_me_with_valid_token_returns_current_user(self, client, alice):
        resp = client.get("/auth/me", headers=alice["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == ALICE_EMAIL
        assert body["id"] == alice["user"]["id"]

    def test_me_with_invalid_token_returns_401(self, client):
        resp = client.get("/auth/me", headers=auth_header("this-is-not-a-real-jwt"))
        assert resp.status_code == 401

    def test_me_without_any_token_is_rejected(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401


# ---------- Devices ----------


class TestDevices:
    def test_create_device_returns_201(self, client, alice):
        resp = client.post(
            "/devices",
            json={"device_name": "Kitchen Sensor", "device_type": "temperature"},
            headers=alice["headers"],
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["device_name"] == "Kitchen Sensor"
        assert body["device_type"] == "temperature"
        assert body["is_online"] is False
        assert body["last_seen_at"] is None

    def test_list_devices_returns_created_device(self, client, alice):
        create_resp = client.post(
            "/devices",
            json={"device_name": "Garage Sensor", "device_type": "humidity"},
            headers=alice["headers"],
        )
        assert create_resp.status_code == 201
        device_id = create_resp.json()["id"]

        list_resp = client.get("/devices", headers=alice["headers"])
        assert list_resp.status_code == 200
        ids = [d["id"] for d in list_resp.json()]
        assert device_id in ids

    def test_get_device_by_id(self, client, alice):
        create_resp = client.post(
            "/devices",
            json={"device_name": "Attic Sensor", "device_type": "smoke"},
            headers=alice["headers"],
        )
        device_id = create_resp.json()["id"]

        get_resp = client.get(f"/devices/{device_id}", headers=alice["headers"])
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == device_id
        assert get_resp.json()["device_name"] == "Attic Sensor"

    def test_user_cannot_list_another_users_device(self, client, alice, bob):
        create_resp = client.post(
            "/devices",
            json={"device_name": "Alice's Sensor", "device_type": "temperature"},
            headers=alice["headers"],
        )
        alice_device_id = create_resp.json()["id"]

        # SECURITY: user isolation. Bob's device list must not contain
        # Alice's device at all.
        bob_list = client.get("/devices", headers=bob["headers"])
        assert bob_list.status_code == 200
        assert alice_device_id not in [d["id"] for d in bob_list.json()]

    def test_user_cannot_get_another_users_device_by_id(self, client, alice, bob):
        create_resp = client.post(
            "/devices",
            json={"device_name": "Alice's Sensor", "device_type": "temperature"},
            headers=alice["headers"],
        )
        alice_device_id = create_resp.json()["id"]

        # SECURITY: fetching another user's device by ID must come back as
        # 404 (not found), not 403 (forbidden) - the API must not confirm
        # that a device with this ID exists for another account.
        bob_get = client.get(f"/devices/{alice_device_id}", headers=bob["headers"])
        assert bob_get.status_code == 404


# ---------- Readings ----------


class TestReadings:
    def test_post_reading_returns_201(self, client, alice):
        device_resp = client.post(
            "/devices",
            json={"device_name": "Pool Sensor", "device_type": "temperature"},
            headers=alice["headers"],
        )
        device_id = device_resp.json()["id"]

        reading_resp = client.post(
            f"/devices/{device_id}/readings",
            json={"value": 21.5, "unit": "celsius"},
            headers=alice["headers"],
        )
        assert reading_resp.status_code == 201
        body = reading_resp.json()
        assert body["device_id"] == device_id
        assert body["value"] == 21.5
        assert body["unit"] == "celsius"

    def test_posting_reading_marks_device_online_with_last_seen(self, client, alice):
        device_resp = client.post(
            "/devices",
            json={"device_name": "Greenhouse Sensor", "device_type": "humidity"},
            headers=alice["headers"],
        )
        device_id = device_resp.json()["id"]
        assert device_resp.json()["is_online"] is False
        assert device_resp.json()["last_seen_at"] is None

        reading_resp = client.post(
            f"/devices/{device_id}/readings",
            json={"value": 55.0, "unit": "percent"},
            headers=alice["headers"],
        )
        assert reading_resp.status_code == 201

        device_after = client.get(f"/devices/{device_id}", headers=alice["headers"])
        assert device_after.status_code == 200
        assert device_after.json()["is_online"] is True
        assert device_after.json()["last_seen_at"] is not None

    def test_posting_reading_to_unowned_device_returns_404(self, client, alice, bob):
        device_resp = client.post(
            "/devices",
            json={"device_name": "Alice's Sensor", "device_type": "temperature"},
            headers=alice["headers"],
        )
        alice_device_id = device_resp.json()["id"]

        # SECURITY: user isolation for writes too - Bob cannot post
        # readings onto a device he does not own.
        bob_post = client.post(
            f"/devices/{alice_device_id}/readings",
            json={"value": 99.9, "unit": "celsius"},
            headers=bob["headers"],
        )
        assert bob_post.status_code == 404
