# Dashboard status (facts only)

## 1. What the page is
[static/dashboard.html](static/dashboard.html) is a single-file page, served at `/static/dashboard.html` (`app/main.py:25`). It has a login form, a status line, and a grid of per-device cards. Each card shows the device name, type, the latest numeric value plus unit, a 50px-high canvas sparkline of the last 30 values (`dashboard.html:125`, `drawSparkline` at :129), and an online dot with a "Last seen" time. There is no table and no historical chart.

It uses a WebSocket for live data. Initial device list is one HTTP fetch, with no polling (no `setInterval` or `setTimeout` anywhere in the file):
- :62 `fetch('/devices', { headers: { 'Authorization': 'Bearer ' + token } })`
- :69 `ws = new WebSocket(wsProtocol + '//' + location.host + '/ws?token=' + encodeURIComponent(token));`
- :75-78 `ws.onmessage = (event) => { const reading = JSON.parse(event.data); updateDeviceCard(reading); };`

The page never loads history. It never calls `/recent`, `/range` or `/aggregate`, so sparklines start empty on every page load and need at least 2 pushed readings to draw (:131). The device cards are built with `innerHTML` from `device.device_name` and `device.device_type` unescaped (:98-99).

## 2. Auth
Login is required. Nothing renders until `connect()` succeeds: `POST /auth/login` (:53), then `GET /devices` with a Bearer token (:62), then the WebSocket.

The WebSocket needs a JWT. `/ws` declares `token: str = Query(...)` (`app/routers/ws.py:53`). An invalid token gets close code 1008 (:58), and so does a valid token whose user no longer exists (:63).

Login inputs are pre-filled with `test@example.com` / `testpass123` (`dashboard.html:35-36`). The page does not register users. That account must already exist, since `/auth/register` is not called by the page or the simulator.

## 3. Empty-DB behaviour
On a fresh database the tables are created at startup (`app/main.py:13-15`, `app/db_init.py:11-26`), but there are no users. A visitor sees the title, "Not connected", the pre-filled login form and an empty grid. Clicking Connect gives "Error: Login failed" (:58) because the user does not exist.

If a user exists but has no devices, login succeeds, `/devices` returns `[]`, and the status line reads "Connected — waiting for readings..." (:72) over an empty grid.

Nothing pushes data automatically. The only broadcast is inside `POST /devices/{id}/readings` (`app/routers/readings.py:63-72`). No background task, scheduler or startup hook generates readings (`lifespan` only calls `init_db()`).

## 4. simulate_device.py
It is a manual script (`python simulate_device.py`, `CLAUDE.md:14`). It is not referenced by `docker-compose.yml`, the `Dockerfile` or `app/`. `BASE_URL` is hardcoded to `http://127.0.0.1:8000` (`simulate_device.py:7`), whereas compose publishes the API on host port 8001 (`docker-compose.yml:31`).

The default interval is 3.0 s (`simulate_device.py:41`), so it posts 1 reading per 3 s, or about 0.33 readings/s per device. It runs one device per process (`--device-name`, default "Living Room Sensor"). It logs in as `test@example.com` and does not register, so that user must already exist. It exits on any non-2xx response (`raise_for_status`, :58).

## 5. Storage growth
There is no retention policy. A search of the repo (excluding `venv/`) for `retention`, `add_retention_policy`, `drop_chunks` and `compress` found no matches. `db_init.py` only calls `create_hypertable` with default chunking. There is no compression policy and no continuous aggregate.

At the simulator default of 1 reading per 3 s: 86,400 / 3 = **28,800 rows/day per device** (about 864,000 per 30 days). With N simulator processes running, multiply by N. This is arithmetic from the default flag, not a measured figure. Each reading also updates the `devices` row (`last_seen_at`, `is_online`, `readings.py:44-45`).

## 6. WebSocket connection cap
None. `ConnectionManager` (`ws.py:13-31`) appends every accepted socket to `active_connections[user_id]` (:20). It has no maximum per user or global, no rate limit, and no idle timeout. The endpoint waits on `receive_text()` (:71). `broadcast_to_user` awaits `send_json` to each socket in sequence (:30-31) with no per-send error handling. Any valid login can open unlimited sockets, and a send failure on one socket would raise inside the POST reading handler.

Cleanup happens only on `WebSocketDisconnect` (:72-73).

## 7. Multi-device
The page shows several streams. It renders one card per device returned by `/devices` (:66) and routes each pushed message by `reading.device_id` (:112). A device created after page load is rendered on its first pushed reading (:113-116). The socket is per-user and carries all of that user's devices, and other users' devices are never shown.
