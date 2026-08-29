from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests
from flask import Flask, redirect, render_template_string, request, url_for
from playwright.sync_api import sync_playwright

DATA_DIR = Path("/data")
SESSION_FILE = DATA_DIR / "livongo-session-bundle.json"
DB_FILE = DATA_DIR / "livongo-readings.sqlite3"
OPTIONS_FILE = DATA_DIR / "options.json"
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium")
LOGIN_URL = "https://my.livongo.com/login"
LOGS_URL = "https://my.livongo.com/blood-pressure/all-logs"
HA_API = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
BP_ENDPOINT_RE = re.compile(
    r"^https://usvc\.livongo\.com/v1/users/[^/]+/reading/bp(?:\?|$)",
    re.IGNORECASE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("livongo_bp_collector")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

sync_lock = threading.Lock()
status_lock = threading.Lock()
status: dict[str, Any] = {
    "state": "starting",
    "message": "Starting collector",
    "last_sync": None,
    "last_error": None,
    "last_new_count": 0,
}


@dataclass
class Options:
    sync_interval_minutes: int = 15
    lookback_days: int = 7
    emit_initial_events: bool = False


def load_options() -> Options:
    if not OPTIONS_FILE.exists():
        return Options()
    try:
        raw = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        return Options(
            sync_interval_minutes=max(1, int(raw.get("sync_interval_minutes", 15))),
            lookback_days=max(1, int(raw.get("lookback_days", 7))),
            emit_initial_events=bool(raw.get("emit_initial_events", False)),
        )
    except Exception:
        LOG.exception("Unable to parse options.json; using defaults")
        return Options()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY,
                reading_time TEXT NOT NULL,
                systolic INTEGER NOT NULL,
                diastolic INTEGER NOT NULL,
                heart_rate INTEGER,
                mean_arterial INTEGER,
                is_valid INTEGER NOT NULL,
                triple_mode INTEGER,
                first_seen_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )


def set_kv(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO kv(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_kv(key: str) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_status(state: str, message: str, error: str | None = None, new_count: int | None = None) -> None:
    with status_lock:
        status["state"] = state
        status["message"] = message
        status["last_error"] = error
        if new_count is not None:
            status["last_new_count"] = new_count
        if state == "ok":
            status["last_sync"] = datetime.now(timezone.utc).isoformat()
    try:
        publish_status_sensor()
    except Exception:
        LOG.exception("Unable to publish status sensor")


def ha_headers() -> dict[str, str]:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not available")
    return {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }


def ha_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 15) -> requests.Response:
    url = f"{HA_API}/{path.lstrip('/')}"
    response = requests.request(
        method,
        url,
        headers=ha_headers(),
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response


def ha_timezone() -> str:
    try:
        response = ha_request("GET", "config")
        value = response.json().get("time_zone")
        if value:
            return str(value)
    except Exception:
        LOG.exception("Could not get Home Assistant time zone; using UTC")
    return "UTC"


def publish_entity(entity_id: str, state_value: Any, attributes: dict[str, Any]) -> None:
    ha_request(
        "POST",
        f"states/{entity_id}",
        {"state": str(state_value), "attributes": attributes},
    )


def publish_status_sensor() -> None:
    with status_lock:
        snapshot = dict(status)
    attrs = {
        "friendly_name": "Livongo BP Sync Status",
        "last_sync": snapshot.get("last_sync"),
        "last_error": snapshot.get("last_error"),
        "last_new_count": snapshot.get("last_new_count"),
        "session_uploaded": SESSION_FILE.exists(),
    }
    publish_entity("sensor.livongo_bp_sync_status", snapshot.get("state", "unknown"), attrs)


def publish_latest_sensors() -> None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM readings WHERE is_valid=1 ORDER BY reading_time DESC LIMIT 1"
        ).fetchone()
    if not row:
        return

    common = {
        "state_class": "measurement",
        "source": "Livongo",
        "livongo_id": row["id"],
        "reading_time": row["reading_time"],
    }
    publish_entity(
        "sensor.livongo_bp_systolic",
        row["systolic"],
        {**common, "friendly_name": "Livongo Systolic", "unit_of_measurement": "mmHg"},
    )
    publish_entity(
        "sensor.livongo_bp_diastolic",
        row["diastolic"],
        {**common, "friendly_name": "Livongo Diastolic", "unit_of_measurement": "mmHg"},
    )
    if row["heart_rate"] is not None:
        publish_entity(
            "sensor.livongo_bp_heart_rate",
            row["heart_rate"],
            {**common, "friendly_name": "Livongo Heart Rate", "unit_of_measurement": "bpm"},
        )
    publish_entity(
        "sensor.livongo_bp_last_reading",
        row["reading_time"],
        {
            "friendly_name": "Livongo BP Last Reading",
            "device_class": "timestamp",
            "livongo_id": row["id"],
            "systolic": row["systolic"],
            "diastolic": row["diastolic"],
            "heart_rate": row["heart_rate"],
        },
    )


def event_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "livongo_id": int(row["id"]),
        "client_record_id": f"livongo:{row['id']}",
        "reading_time": row["reading_time"],
        "systolic": int(row["systolic"]),
        "diastolic": int(row["diastolic"]),
        "heart_rate": int(row["heart_rate"]) if row["heart_rate"] is not None else None,
        "mean_arterial": int(row["mean_arterial"]) if row["mean_arterial"] is not None else None,
        "triple_mode": bool(row["triple_mode"]) if row["triple_mode"] is not None else None,
        "source": "livongo",
    }


def fire_reading_event(row: sqlite3.Row | dict[str, Any]) -> None:
    ha_request("POST", "events/livongo_bp_reading", event_payload(row))


def validate_session_bundle(bundle: dict[str, Any]) -> None:
    state = bundle.get("storage_state")
    if not isinstance(state, dict):
        raise ValueError("Missing storage_state object")
    if not isinstance(state.get("cookies", []), list):
        raise ValueError("storage_state.cookies is invalid")
    if not isinstance(state.get("origins", []), list):
        raise ValueError("storage_state.origins is invalid")
    session_storage = bundle.get("session_storage", {})
    if session_storage is not None and not isinstance(session_storage, dict):
        raise ValueError("session_storage is invalid")


def load_session_bundle() -> dict[str, Any]:
    if not SESSION_FILE.exists():
        raise FileNotFoundError("No Livongo session bundle has been uploaded")
    bundle = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    validate_session_bundle(bundle)
    return bundle


def restore_session_storage(context: Any, bundle: dict[str, Any]) -> None:
    all_storage = bundle.get("session_storage") or {}
    if not all_storage:
        return
    serialized = json.dumps(all_storage)
    context.add_init_script(
        """
        (() => {
          const allStorage = %s;
          const values = allStorage[window.location.origin];
          if (!values) return;
          for (const [key, value] of Object.entries(values)) {
            try { window.sessionStorage.setItem(key, value); } catch (e) {}
          }
        })();
        """ % serialized
    )


def endpoint_base(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def fetch_livongo_readings(lookback_days: int) -> list[dict[str, Any]]:
    bundle = load_session_bundle()
    tz_name = ha_timezone()

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(bundle["storage_state"], tmp)
        storage_path = tmp.name

    captured: dict[str, str | None] = {"authorization": None, "endpoint": None}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=CHROMIUM,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = browser.new_context(storage_state=storage_path, timezone_id=tz_name)
            restore_session_storage(context, bundle)
            page = context.new_page()

            def on_request(req: Any) -> None:
                if BP_ENDPOINT_RE.match(req.url):
                    auth = req.headers.get("authorization")
                    if auth:
                        captured["authorization"] = auth
                        captured["endpoint"] = req.url

            page.on("request", on_request)
            page.goto(LOGS_URL, wait_until="domcontentloaded", timeout=60_000)

            deadline = time.monotonic() + 30
            while not captured["authorization"] and time.monotonic() < deadline:
                page.wait_for_timeout(500)

            if "/login" in page.url and not captured["authorization"]:
                raise PermissionError("Livongo session expired; upload a new session bundle")
            if not captured["authorization"] or not captured["endpoint"]:
                raise PermissionError(
                    "Could not obtain Livongo API authorization from the saved session; re-authenticate"
                )

            base = endpoint_base(str(captured["endpoint"]))
            auth_header = str(captured["authorization"])
            today = datetime.now().date()
            start_day = today - timedelta(days=max(lookback_days - 1, 0))
            results: dict[int, dict[str, Any]] = {}

            day = start_day
            while day <= today:
                query = urlencode(
                    {
                        "start": f"{day.isoformat()}T00:00:00",
                        "end": f"{day.isoformat()}T23:59:59",
                    }
                )
                url = f"{base}?{query}"
                response = context.request.get(
                    url,
                    headers={
                        "Authorization": auth_header,
                        "Accept": "application/json, text/plain, */*",
                        "Referer": "https://my.livongo.com/",
                    },
                    timeout=30_000,
                )
                if response.status in (401, 403):
                    raise PermissionError("Livongo authorization expired; upload a new session bundle")
                if not response.ok:
                    raise RuntimeError(f"Livongo API returned HTTP {response.status} for {day.isoformat()}")

                payload = response.json()
                for reading in payload.get("bpReadings", []):
                    if reading.get("id") is None:
                        continue
                    results[int(reading["id"])] = reading
                day += timedelta(days=1)

            context.close()
            browser.close()
            return list(results.values())
    finally:
        try:
            os.unlink(storage_path)
        except OSError:
            pass


def insert_readings(readings: list[dict[str, Any]]) -> list[int]:
    inserted_ids: list[int] = []
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        for r in readings:
            if r.get("id") is None or not r.get("readingTime"):
                continue
            values = (
                int(r["id"]),
                str(r["readingTime"]),
                int(r.get("systolic", 0)),
                int(r.get("diastolic", 0)),
                int(r["heartRate"]) if r.get("heartRate") is not None else None,
                int(r["meanArterial"]) if r.get("meanArterial") is not None else None,
                1 if bool(r.get("isValid", False)) else 0,
                1 if bool(r.get("tripleMode")) else 0 if r.get("tripleMode") is not None else None,
                now,
            )
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO readings
                (id, reading_time, systolic, diastolic, heart_rate, mean_arterial,
                 is_valid, triple_mode, first_seen_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            if cursor.rowcount == 1:
                inserted_ids.append(int(r["id"]))
    return inserted_ids


def rows_by_ids(ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with db() as conn:
        return conn.execute(
            f"SELECT * FROM readings WHERE id IN ({placeholders}) AND is_valid=1 ORDER BY reading_time ASC",
            ids,
        ).fetchall()


def sync_once() -> None:
    if not sync_lock.acquire(blocking=False):
        LOG.info("Sync already running")
        return
    try:
        opts = load_options()
        set_status("syncing", "Sync in progress")
        before_count = 0
        with db() as conn:
            before_count = conn.execute("SELECT COUNT(*) AS c FROM readings").fetchone()["c"]

        readings = fetch_livongo_readings(opts.lookback_days)
        inserted_ids = insert_readings(readings)
        valid_rows = rows_by_ids(inserted_ids)

        first_sync_done = get_kv("initial_sync_done") == "1"
        emit = first_sync_done or opts.emit_initial_events
        emitted = 0
        if emit:
            for row in valid_rows:
                try:
                    fire_reading_event(row)
                    emitted += 1
                except Exception:
                    LOG.exception("Failed to fire event for Livongo reading %s", row["id"])

        set_kv("initial_sync_done", "1")
        publish_latest_sensors()
        message = (
            f"Sync complete: {len(readings)} fetched, {len(inserted_ids)} new, {emitted} events emitted"
        )
        LOG.info(message)
        set_status("ok", message, new_count=len(inserted_ids))
    except FileNotFoundError as exc:
        LOG.warning(str(exc))
        set_status("auth_required", str(exc), error=str(exc), new_count=0)
    except PermissionError as exc:
        LOG.warning(str(exc))
        set_status("auth_required", str(exc), error=str(exc), new_count=0)
    except Exception as exc:
        LOG.exception("Sync failed")
        set_status("error", "Sync failed", error=str(exc), new_count=0)
    finally:
        sync_lock.release()


def start_sync_async() -> None:
    threading.Thread(target=sync_once, daemon=True, name="manual-sync").start()


def replay(days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM readings WHERE is_valid=1 ORDER BY reading_time ASC"
        ).fetchall()
    selected: list[sqlite3.Row] = []
    for row in rows:
        try:
            dt = datetime.fromisoformat(row["reading_time"])
            if dt.astimezone(timezone.utc) >= cutoff:
                selected.append(row)
        except Exception:
            continue
    count = 0
    for row in selected:
        fire_reading_event(row)
        count += 1
    return count


def worker() -> None:
    while True:
        sync_once()
        opts = load_options()
        time.sleep(max(60, opts.sync_interval_minutes * 60))


def recent_rows(limit: int = 10) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM readings WHERE is_valid=1 ORDER BY reading_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Livongo BP Collector</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; margin: 24px; line-height: 1.4; }
    .card { border: 1px solid #ccc; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
    .ok { color: #217a35; } .error { color: #b3261e; } .warn { color: #8a5a00; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border-bottom: 1px solid #ddd; padding: 8px; text-align: left; }
    button { padding: 8px 14px; margin-right: 8px; }
    input[type=file], input[type=number] { margin: 8px 0; }
    code { font-size: 0.9em; }
  </style>
</head>
<body>
  <h1>Livongo BP Collector</h1>
  <div class="card">
    <h2>Status</h2>
    <p><strong>State:</strong> {{ status.state }}</p>
    <p><strong>Message:</strong> {{ status.message }}</p>
    <p><strong>Last sync:</strong> {{ status.last_sync or 'Never' }}</p>
    {% if status.last_error %}<p class="error"><strong>Last error:</strong> {{ status.last_error }}</p>{% endif %}
    <p><strong>Session uploaded:</strong> {{ 'Yes' if session_exists else 'No' }}</p>
    <form method="post" action="sync"><button type="submit">Sync now</button></form>
  </div>

  <div class="card">
    <h2>Livongo session</h2>
    <p>Generate <code>livongo-session-bundle.json</code> with the included Mac helper, then upload it here.</p>
    <form method="post" action="upload" enctype="multipart/form-data">
      <input type="file" name="session" accept="application/json,.json" required>
      <br><button type="submit">Upload session bundle</button>
    </form>
  </div>

  <div class="card">
    <h2>Replay stored readings</h2>
    <p>This re-fires Home Assistant <code>livongo_bp_reading</code> events. It is useful after installing the Android Health Connect bridge.</p>
    <form method="post" action="replay">
      <label>Days: <input type="number" name="days" min="1" max="90" value="7"></label>
      <button type="submit">Replay events</button>
    </form>
    {% if replay_message %}<p>{{ replay_message }}</p>{% endif %}
  </div>

  <div class="card">
    <h2>Recent valid readings</h2>
    {% if rows %}
    <table>
      <thead><tr><th>Time</th><th>BP</th><th>Heart rate</th><th>Livongo ID</th></tr></thead>
      <tbody>
      {% for r in rows %}
        <tr><td>{{ r.reading_time }}</td><td>{{ r.systolic }}/{{ r.diastolic }}</td><td>{{ r.heart_rate or '' }}</td><td>{{ r.id }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}<p>No readings stored yet.</p>{% endif %}
  </div>
</body>
</html>
"""


def ingress_root() -> str:
    base = request.headers.get("X-Ingress-Path")
    if base:
        return base.rstrip("/") + "/"
    return "/"


@app.get("/")
def index() -> str:
    with status_lock:
        snapshot = dict(status)
    return render_template_string(
        PAGE,
        status=snapshot,
        session_exists=SESSION_FILE.exists(),
        rows=recent_rows(),
        replay_message=request.args.get("replay_message"),
    )


@app.post("/upload")
def upload_session() -> Any:
    file = request.files.get("session")
    if not file:
        return "Missing session file", 400
    try:
        bundle = json.load(file.stream)
        validate_session_bundle(bundle)
        SESSION_FILE.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        os.chmod(SESSION_FILE, 0o600)
        set_status("ready", "Session uploaded; starting sync")
        start_sync_async()
    except Exception as exc:
        LOG.exception("Invalid session upload")
        return f"Invalid session bundle: {exc}", 400
    return redirect(ingress_root())


@app.post("/sync")
def sync_now() -> Any:
    start_sync_async()
    return redirect(ingress_root())


@app.post("/replay")
def replay_events() -> Any:
    try:
        days = max(1, min(90, int(request.form.get("days", "7"))))
        count = replay(days)
        message = f"Replayed {count} stored readings from the last {days} days."
    except Exception as exc:
        LOG.exception("Replay failed")
        message = f"Replay failed: {exc}"
    from urllib.parse import quote_plus
    return redirect(f"{ingress_root()}?replay_message={quote_plus(message)}")


def main() -> None:
    init_db()
    try:
        publish_status_sensor()
    except Exception:
        LOG.exception("Initial Home Assistant status publish failed")
    threading.Thread(target=worker, daemon=True, name="sync-worker").start()
    app.run(host="0.0.0.0", port=8099, threaded=True)


if __name__ == "__main__":
    main()
