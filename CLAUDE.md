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

# Working agreement

## 1. Think before coding
Don't assume. Don't hide confusion. Surface tradeoffs.
- State assumptions explicitly. If uncertain, ask rather than guess.
- Present multiple interpretations when genuinely ambiguous — don't silently pick one.
- Push back if a simpler approach exists.
- Stop when confused. Name what's unclear and ask.
- If a file or path I referenced doesn't exist, say so and stop — do not create it to make the instruction work.

## 2. Simplicity first
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or configurability that wasn't requested.
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite it.

Test: would a senior engineer call this overcomplicated? If yes, simplify.

## 3. Surgical changes
Touch only what you must.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor what isn't broken.
- Match existing style even if you'd do it differently.
- Notice unrelated dead code? Mention it. Don't delete it.
- Remove imports/variables that YOUR change orphaned. Nothing else.

Test: every changed line traces directly to the request.

## 4. Goal-driven execution
Define success criteria, then loop until verified.
- "Add validation" → "write tests for invalid inputs, then make them pass"
- "Fix the bug" → "write a test reproducing it, then make it pass"
- For multi-step work, state the plan as: step → verify: check

## 5. Verify, don't claim
Never report success without running something that proves it.
- Report real command output, not what you expect the output to be.
- A build that "should work" isn't verified. Build it.
- If you can't verify something (no API key, no network, no test suite), say so plainly rather than implying it passed.
- If you measure something, say what you measured and when — stale measurements have caused real confusion here.

## Project conventions

**Environment**
- Windows. Terminal commands must be PowerShell, not bash.
- Python: use `py -3.13`. Bare `python` resolves to 3.8 on this machine and can't install pinned requirements.
- Working venv is `venv/`. `.venv/` is gitignored.

**Git**
- Never commit or push unless explicitly asked. Default to leaving changes staged or unstaged for review.
- Branch off `main`, commit, push, open PR, wait for green checks, merge, pull. `main` is NOT yet protected — set up the same ruleset as the other two repos (Settings → Rules).
- Before staging: check `git status`. Generated directories (`staticfiles/`, `.venv/`, build output) have been accidentally staged twice. Don't let it happen again.

**Docker**
- Tail long build output (`| Select-Object -Last 60`), don't dump it in full.
- When checking an image, verify you're looking at the tag you just built — stale tags from earlier builds have caused wrong conclusions here.
- These apps bind `0.0.0.0` on `$PORT` with a fallback. Don't hardcode ports.

**Deployment context**
- Three projects deploy to Railway sharing one PostgreSQL instance, each with its own database.
- PG18 volumes mount at `/var/lib/postgresql`, NOT `/var/lib/postgresql/data`. This exact mistake silently broke persistence once.
