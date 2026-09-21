# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Windows/PowerShell, Python 3.13 venv in `venv/`.

```powershell
pip install -r requirements.txt
python create_tables.py                 # creates tables only — does NOT create the hypertable
# then in psql: SELECT create_hypertable('readings', 'recorded_at');   (manual step)
uvicorn app.main:app --reload           # dashboard at /static/dashboard.html
python simulate_device.py               # generate demo readings
pytest tests/ -v
pytest tests/test_ws.py::test_name -v   # single test
docker compose up                       # written but never run locally
```

No linter/formatter is configured.

## Configuration

- `.env` (see `.env.example`): `DATABASE_URL`, `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `POSTGRES_PASSWORD`. `app/database.py` raises at import if `DATABASE_URL` is unset.
- `.env.test` (see `.env.test.example`): `TEST_DATABASE_URL`, pointing at a separate `telemetry_test_db`.

## Architecture

FastAPI app (`app/main.py`) with routers `auth`, `devices`, `readings`, `ws`, plus a static dashboard mounted at `/static`. SQLAlchemy sync sessions via `get_db`; models in `app/models.py`, Pydantic schemas in `app/schemas.py`, JWT/password logic in `app/auth.py`.

Key cross-file flow: `POST /devices/{id}/readings` (`routers/readings.py`) runs the sync DB write in `run_in_threadpool`, then awaits `manager.broadcast_to_user(...)` on the `ConnectionManager` singleton defined in `routers/ws.py`. The manager holds an in-process `user_id -> [WebSocket]` map, so broadcast only reaches sockets on the same process (no Redis/pub-sub; multi-worker deployments would break live push).

Things that are easy to get wrong:
- `readings` is a TimescaleDB hypertable with composite PK `(id, recorded_at)` and `autoincrement=True` on `id`. Removing either breaks inserts/partitioning.
- The `/aggregate` endpoint uses raw SQL with `time_bucket(CAST(:bucket AS INTERVAL), ...)`, which requires TimescaleDB, so SQLite cannot be used.
- WebSocket auth passes the JWT as a `?token=` query param (browsers can't set WS headers); invalid tokens close with code 1008. HTTP uses the Bearer header.
- All queries are scoped to `current_user.id`; cross-user access returns 404, not 403 (tests assert this, including WS broadcast isolation).

## Tests

`tests/conftest.py` runs against a real Postgres/TimescaleDB test database (needs the `timescaledb` extension enabled in it). It overrides `get_db`, creates tables and the hypertable per session, drops all at the end, and TRUNCATEs tables after every test. It refuses to run if the test DB name equals the app's DB name. Shared fixtures: `client`, `alice`, `bob`.
