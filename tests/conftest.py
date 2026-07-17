import os
import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db, DATABASE_URL
from app.main import app
from app import models  # noqa: F401  ensures all tables are registered on Base.metadata


def _derive_test_database_url() -> str:
    """Point tests at the same Postgres server/credentials as the real app,
    but at a dedicated `telemetry_test_db` database. Override with the
    TEST_DATABASE_URL env var if your test DB lives elsewhere.
    """
    override = os.getenv("TEST_DATABASE_URL")
    if override:
        return override
    return re.sub(r"/[^/?]+(\?.*)?$", "/telemetry_test_db", DATABASE_URL)


TEST_DATABASE_URL = _derive_test_database_url()

# We test against a REAL Postgres/TimescaleDB database rather than SQLite.
# `readings` is a TimescaleDB hypertable with a composite primary key
# (id, recorded_at) and chunked storage under the hood - SQLite has no
# concept of extensions, hypertables, or that PK shape, so a SQLite-backed
# suite would only prove the code works against a database the app never
# actually runs on. This DB is separate from telemetry_db (see .env) and is
# created/dropped entirely by this test session.
test_engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    Base.metadata.create_all(bind=test_engine)

    with test_engine.connect() as conn:
        try:
            # if_not_exists=>TRUE makes this safe to call every session,
            # including re-runs against a DB that's already converted.
            conn.execute(
                text(
                    "SELECT create_hypertable("
                    "'readings', 'recorded_at', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(
                "Failed to create the 'readings' hypertable in the test "
                "database. Make sure the TimescaleDB extension is enabled "
                "in telemetry_test_db: "
                'psql -d telemetry_test_db -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"'
            ) from exc

    yield

    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clean_tables():
    """Truncate all app tables after each test so tests never leak state
    into one another, regardless of run order."""
    yield
    with test_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE readings, devices, users RESTART IDENTITY CASCADE"))
        conn.commit()


@pytest.fixture
def client():
    return TestClient(app)


# ---------- Shared auth helpers / fixtures ----------
#
# Used by both test_api.py and test_ws.py so user registration/login isn't
# duplicated across files.

ALICE_EMAIL = "alice@example.com"
ALICE_PASSWORD = "SuperSecret123!"
BOB_EMAIL = "bob@example.com"
BOB_PASSWORD = "AnotherSecret456!"


def register(client, email=ALICE_EMAIL, password=ALICE_PASSWORD):
    return client.post("/auth/register", json={"email": email, "password": password})


def login(client, email=ALICE_EMAIL, password=ALICE_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def alice(client):
    """A registered + logged-in user, with ready-to-use auth headers."""
    reg = register(client)
    assert reg.status_code == 201
    tok = login(client)
    assert tok.status_code == 200
    token = tok.json()["access_token"]
    return {"user": reg.json(), "headers": auth_header(token), "token": token}


@pytest.fixture
def bob(client):
    """A second, distinct registered + logged-in user, for isolation tests."""
    reg = register(client, email=BOB_EMAIL, password=BOB_PASSWORD)
    assert reg.status_code == 201
    tok = login(client, email=BOB_EMAIL, password=BOB_PASSWORD)
    assert tok.status_code == 200
    token = tok.json()["access_token"]
    return {"user": reg.json(), "headers": auth_header(token), "token": token}
