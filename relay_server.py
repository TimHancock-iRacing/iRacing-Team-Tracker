#!/usr/bin/env python3
"""
Minimal internet relay for the iRacing standalone tracker.

Features
- Receives session updates from driver publishers
- Stores latest state in memory
- Optional SQLite persistence for latest snapshot + history
- Serves a simple live dashboard
- Supports simple read/write token auth

Run:
    python relay_server.py --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from flask import Flask, Response, jsonify, render_template_string, request

APP = Flask(__name__)

SESSION_STORE: Dict[str, Dict[str, Any]] = {}
SESSION_LOCK = threading.RLock()
DB_PATH = "relay_store.sqlite3"

DASHBOARD_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>iRacing Team Dashboard</title>
  <style>
    :root {
      --bg: #0f1115;
      --card: #171a21;
      --muted: #97a0af;
      --text: #eef2f7;
      --accent: #53a7ff;
      --good: #34c759;
      --warn: #ff9f0a;
      --bad: #ff453a;
      --border: #252a34;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 20px;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    }
    .wrap { max-width: 1320px; margin: 0 auto; }
    .topbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 16px; }
    .subtitle { color: var(--muted); font-size: 14px; }
    .pill {
      display: inline-flex; align-items: center; gap: 8px;
      border: 1px solid var(--border); border-radius: 999px;
      padding: 8px 12px; color: var(--text); background: rgba(255,255,255,0.02);
      font-size: 13px;
    }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .good { background: var(--good); }
    .warn { background: var(--warn); }
    .bad { background: var(--bad); }
    .grid { display: grid; gap: 16px; grid-template-columns: repeat(12, 1fr); }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 8px 30px rgba(0,0,0,0.24);
    }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-6 { grid-column: span 6; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    .value { font-size: 30px; font-weight: 700; margin-top: 8px; }
    .small { font-size: 12px; color: var(--muted); }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); font-size: 14px; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    @media (max-width: 980px) {
      .span-3, .span-4, .span-6, .span-8, .span-12 { grid-column: span 12; }
    }
  </style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div>
      <h1 id="title">iRacing Team Dashboard</h1>
      <div class="subtitle">Remote live state from the active driver PC</div>
    </div>
    <div class="pill"><span class="dot good" id="statusDot"></span><span id="statusText">Waiting</span></div>
  </div>

  <div class="grid">
    <div class="card span-3">
      <div class="label">Current Driver</div>
      <div class="value" id="driverName">-</div>
      <div class="small" id="driverMeta"></div>
    </div>

    <div class="card span-3">
      <div class="label">Current Lap</div>
      <div class="value" id="raceLap">-</div>
      <div class="small" id="raceMeta"></div>
    </div>

    <div class="card span-3">
      <div class="label">Fuel</div>
      <div class="value" id="fuelNow">-</div>
      <div class="small" id="fuelMeta"></div>
    </div>

    <div class="card span-3">
      <div class="label">Next Stop</div>
      <div class="value" id="nextStop">-</div>
      <div class="small" id="stopMeta"></div>
    </div>

    <div class="card span-8">
      <div class="label" style="margin-bottom: 12px;">Strategy</div>
      <table>
        <tbody>
          <tr><th>Session ID</th><td class="mono" id="sessionIdCell">-</td></tr>
          <tr><th>Green flag start lap</th><td id="greenFlagLap">-</td></tr>
          <tr><th>Laps remaining</th><td id="lapsRemaining">-</td></tr>
          <tr><th>Stops required</th><td id="stopsRequired">-</td></tr>
          <tr><th>Fuel to add</th><td id="fuelToAdd">-</td></tr>
          <tr><th>4-tyre delta vs fuel only</th><td id="fourTyreDelta">-</td></tr>
          <tr><th>Pit total loss average</th><td id="pitLossAvg">-</td></tr>
          <tr><th>Publisher status</th><td id="publisherStatus">-</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card span-4">
      <div class="label" style="margin-bottom: 12px;">Raw State</div>
      <pre id="raw" class="mono" style="white-space: pre-wrap; font-size: 12px; margin: 0;"></pre>
    </div>
  </div>
</div>

<script>
  async function refresh() {
    const parts = window.location.pathname.split("/");
    const sessionId = decodeURIComponent(parts[parts.length - 1]);
    const readToken = new URLSearchParams(window.location.search).get("token") || "";
    try {
      const res = await fetch(`/api/session/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(readToken)}`);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();

      const now = Date.now() / 1000;
      const ts = data.timestamp || 0;
      const age = ts ? (now - ts) : 9999;
      const live = age < 5;

      document.getElementById("statusDot").className = "dot " + (live ? "good" : "warn");
      document.getElementById("statusText").textContent = live ? "Live" : "Stale";

      document.getElementById("title").textContent = (data.session_id || "Session") + " dashboard";
      document.getElementById("sessionIdCell").textContent = data.session_id || "-";

      document.getElementById("driverName").textContent = data.driver?.name || "-";
      document.getElementById("driverMeta").textContent = "Stint laps: " + (data.driver?.stint_laps ?? "-");

      document.getElementById("raceLap").textContent = data.race?.lap ?? "-";
      document.getElementById("raceMeta").textContent = "Laps total est: " + (data.race?.laps_total_est ?? "-");

      document.getElementById("fuelNow").textContent = data.fuel?.current_l == null ? "-" : `${data.fuel.current_l.toFixed(1)} L`;
      document.getElementById("fuelMeta").textContent = `Burn ${data.fuel?.burn_lpl ?? "-"} L/lap | ${data.fuel?.laps_left ?? "-"} laps left`;

      document.getElementById("nextStop").textContent = data.strategy?.next_stop_lap ?? "-";
      document.getElementById("stopMeta").textContent = `Target fill: ${data.strategy?.fuel_to_add_l ?? "-"} L`;

      document.getElementById("greenFlagLap").textContent = data.race?.green_flag_lap ?? "-";
      document.getElementById("lapsRemaining").textContent = data.strategy?.laps_remaining ?? "-";
      document.getElementById("stopsRequired").textContent = data.strategy?.stops_required ?? "-";
      document.getElementById("fuelToAdd").textContent = data.strategy?.fuel_to_add_l == null ? "-" : `${data.strategy.fuel_to_add_l.toFixed(1)} L`;
      document.getElementById("fourTyreDelta").textContent = data.strategy?.four_tyre_delta_s == null ? "-" : `+${data.strategy.four_tyre_delta_s.toFixed(1)} s`;
      document.getElementById("pitLossAvg").textContent = data.pit?.pit_loss_avg_s == null ? "-" : `${data.pit.pit_loss_avg_s.toFixed(1)} s`;
      document.getElementById("publisherStatus").textContent = live ? `Live update ${age.toFixed(1)} s ago` : `Last update ${age.toFixed(1)} s ago`;

      document.getElementById("raw").textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      document.getElementById("statusDot").className = "dot bad";
      document.getElementById("statusText").textContent = "Disconnected";
      document.getElementById("raw").textContent = String(err);
    }
  }

  refresh();
  setInterval(refresh, 1000);
</script>
</body>
</html>
"""

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_state (
            session_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

def save_snapshot(session_id: str, payload: Dict[str, Any]) -> None:
    payload_json = json.dumps(payload)
    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO latest_state (session_id, payload_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (session_id, payload_json, now),
    )
    cur.execute(
        "INSERT INTO history (session_id, payload_json, created_at) VALUES (?, ?, ?)",
        (session_id, payload_json, now),
    )
    conn.commit()
    conn.close()

def get_required_env(name: str, fallback: str = "") -> str:
    return os.environ.get(name, fallback)

def check_write_token(request_payload: Dict[str, Any]) -> bool:
    expected = get_required_env("WRITE_TOKEN", "")
    if not expected:
        return True
    actual = request.headers.get("X-Write-Token") or request_payload.get("write_token", "")
    return actual == expected

def check_read_token() -> bool:
    expected = get_required_env("READ_TOKEN", "")
    if not expected:
        return True
    actual = request.args.get("token", "")
    return actual == expected

@APP.get("/")
def index() -> Response:
    return jsonify({
        "ok": True,
        "message": "Relay is running",
        "routes": [
            "/api/update",
            "/api/session/<session_id>",
            "/session/<session_id>",
        ],
    })

@APP.get("/health")
def health() -> Response:
    return jsonify({"ok": True, "server_time": time.time()})

@APP.post("/api/update")
def api_update() -> Response:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Invalid JSON payload"}), 400

    if not check_write_token(payload):
        return jsonify({"ok": False, "error": "Invalid write token"}), 403

    session_id = payload.get("session_id", "").strip()
    if not session_id:
        return jsonify({"ok": False, "error": "Missing session_id"}), 400

    payload["timestamp"] = float(payload.get("timestamp") or time.time())

    with SESSION_LOCK:
        SESSION_STORE[session_id] = payload

    save_snapshot(session_id, payload)
    return jsonify({"ok": True, "session_id": session_id})

@APP.get("/api/session/<session_id>")
def api_session(session_id: str) -> Response:
    if not check_read_token():
        return jsonify({"ok": False, "error": "Invalid read token"}), 403

    with SESSION_LOCK:
        payload = SESSION_STORE.get(session_id)

    if payload is None:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT payload_json FROM latest_state WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            payload = json.loads(row[0])

    if payload is None:
        return jsonify({"ok": False, "error": "Session not found"}), 404

    return jsonify(payload)

@APP.get("/session/<session_id>")
def session_dashboard(session_id: str) -> str:
    return render_template_string(DASHBOARD_HTML)

def main() -> int:
    parser = argparse.ArgumentParser(description="Relay server for internet-shared iRacing tracker state")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="relay_store.sqlite3")
    args = parser.parse_args()

    global DB_PATH
    DB_PATH = args.db
    init_db()

    APP.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
