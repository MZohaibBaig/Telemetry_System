from datetime import datetime, timedelta, timezone

import pytest

from conftest import TestSessionLocal
from app.models import Reading


def create_device(client, headers, device_name="Sensor", device_type="temperature"):
    resp = client.post(
        "/devices",
        json={"device_name": device_name, "device_type": device_type},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def insert_reading(device_id, value, recorded_at, unit="celsius"):
    """Insert a Reading directly via the DB session so `recorded_at` can be
    pinned to an exact, known timestamp - the API only accepts value/unit
    and always stamps recorded_at with the server's current time, which is
    useless for asserting range boundaries and bucket membership.
    """
    db = TestSessionLocal()
    try:
        reading = Reading(device_id=device_id, value=value, unit=unit, recorded_at=recorded_at)
        db.add(reading)
        db.commit()
    finally:
        db.close()


BASE_TIME = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


class TestRecent:
    def test_recent_respects_limit_and_is_newest_first(self, client, alice):
        device_id = create_device(client, alice["headers"])

        for i, value in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
            insert_reading(device_id, value, BASE_TIME + timedelta(minutes=i))

        resp = client.get(
            f"/devices/{device_id}/readings/recent",
            params={"limit": 3},
            headers=alice["headers"],
        )
        assert resp.status_code == 200
        values = [r["value"] for r in resp.json()]
        assert values == [5.0, 4.0, 3.0]

    def test_recent_unowned_device_returns_404(self, client, alice, bob):
        device_id = create_device(client, alice["headers"], device_name="Alice's Sensor")

        resp = client.get(
            f"/devices/{device_id}/readings/recent",
            headers=bob["headers"],
        )
        assert resp.status_code == 404


class TestRange:
    def test_range_excludes_outside_readings_and_is_oldest_first(self, client, alice):
        device_id = create_device(client, alice["headers"])

        # t0, t0+10, t0+20, t0+30, t0+40 - window covers only the middle three
        timestamps = [BASE_TIME + timedelta(minutes=10 * i) for i in range(5)]
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        for ts, value in zip(timestamps, values):
            insert_reading(device_id, value, ts)

        resp = client.get(
            f"/devices/{device_id}/readings/range",
            params={
                "start": timestamps[1].isoformat(),
                "end": timestamps[3].isoformat(),
            },
            headers=alice["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [r["value"] for r in body] == [20.0, 30.0, 40.0]

    def test_range_unowned_device_returns_404(self, client, alice, bob):
        device_id = create_device(client, alice["headers"], device_name="Alice's Sensor")

        resp = client.get(
            f"/devices/{device_id}/readings/range",
            params={
                "start": BASE_TIME.isoformat(),
                "end": (BASE_TIME + timedelta(hours=1)).isoformat(),
            },
            headers=bob["headers"],
        )
        assert resp.status_code == 404


class TestAggregate:
    def test_aggregate_computes_correct_avg_min_max_per_bucket(self, client, alice):
        device_id = create_device(client, alice["headers"])

        # Bucket 1 [10:00, 11:00): values 10, 20, 30 -> avg 20, min 10, max 30
        insert_reading(device_id, 10.0, BASE_TIME + timedelta(minutes=0))
        insert_reading(device_id, 20.0, BASE_TIME + timedelta(minutes=15))
        insert_reading(device_id, 30.0, BASE_TIME + timedelta(minutes=45))

        # Bucket 2 [11:00, 12:00): values 100, 200 -> avg 150, min 100, max 200
        insert_reading(device_id, 100.0, BASE_TIME + timedelta(hours=1, minutes=5))
        insert_reading(device_id, 200.0, BASE_TIME + timedelta(hours=1, minutes=20))

        resp = client.get(
            f"/devices/{device_id}/readings/aggregate",
            params={
                "bucket": "1 hour",
                "start": BASE_TIME.isoformat(),
                "end": (BASE_TIME + timedelta(hours=2)).isoformat(),
            },
            headers=alice["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2

        first, second = body
        assert first["avg_value"] == pytest.approx(20.0)
        assert first["min_value"] == pytest.approx(10.0)
        assert first["max_value"] == pytest.approx(30.0)

        assert second["avg_value"] == pytest.approx(150.0)
        assert second["min_value"] == pytest.approx(100.0)
        assert second["max_value"] == pytest.approx(200.0)

    def test_aggregate_without_start_or_end_defaults_and_returns_200(self, client, alice):
        device_id = create_device(client, alice["headers"])

        reading_resp = client.post(
            f"/devices/{device_id}/readings",
            json={"value": 42.0, "unit": "celsius"},
            headers=alice["headers"],
        )
        assert reading_resp.status_code == 201

        resp = client.get(
            f"/devices/{device_id}/readings/aggregate",
            headers=alice["headers"],
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_aggregate_unowned_device_returns_404(self, client, alice, bob):
        device_id = create_device(client, alice["headers"], device_name="Alice's Sensor")

        resp = client.get(
            f"/devices/{device_id}/readings/aggregate",
            headers=bob["headers"],
        )
        assert resp.status_code == 404
