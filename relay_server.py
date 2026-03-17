
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from flask import Flask, Response, jsonify, render_template_string, request

APP = Flask(__name__)
SESSION_LOCK = threading.RLock()
DB_PATH = "relay_store.sqlite3"
SESSION_STORE: Dict[str, Dict[str, Any]] = {}

DASHBOARD_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>iRacing Team Tracker v2</title>
  <style>
    :root { --bg:#0f1115; --card:#171a21; --muted:#97a0af; --text:#eef2f7; --accent:#53a7ff; --good:#34c759; --warn:#ff9f0a; --bad:#ff453a; --border:#252a34; }
    * { box-sizing:border-box; }
    body { margin:0; padding:20px; background:var(--bg); color:var(--text); font-family:Inter,system-ui,sans-serif; }
    .wrap { max-width:1360px; margin:0 auto; }
    .topbar { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:16px; }
    .pill { display:inline-flex; align-items:center; gap:8px; border:1px solid var(--border); border-radius:999px; padding:8px 12px; background:rgba(255,255,255,0.02); font-size:13px; }
    .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    .good { background:var(--good); } .warn { background:var(--warn); } .bad { background:var(--bad); }
    .grid { display:grid; gap:16px; grid-template-columns:repeat(12,1fr); }
    .card { background:var(--card); border:1px solid var(--border); border-radius:16px; padding:16px; box-shadow:0 8px 30px rgba(0,0,0,0.24); }
    .span-3{grid-column:span 3;} .span-4{grid-column:span 4;} .span-8{grid-column:span 8;} .span-12{grid-column:span 12;}
    .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.06em; }
    .value { font-size:30px; font-weight:700; margin-top:8px; }
    .small { font-size:12px; color:var(--muted); }
    .mono { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    table { width:100%; border-collapse:collapse; }
    th,td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--border); font-size:14px; }
    th { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.06em; }
    @media(max-width:980px){ .span-3,.span-4,.span-8,.span-12{grid-column:span 12;} }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div><h1 id="title">iRacing Team Tracker v2</h1><div class="small">Multi-driver sync + strategy engine</div></div>
      <div class="pill"><span class="dot good" id="statusDot"></span><span id="statusText">Waiting</span></div>
    </div>
    <div class="grid">
      <div class="card span-3"><div class="label">Current Driver</div><div class="value" id="driverName">-</div><div class="small" id="driverMeta"></div></div>
      <div class="card span-3"><div class="label">Current Lap</div><div class="value" id="raceLap">-</div><div class="small" id="raceMeta"></div></div>
      <div class="card span-3"><div class="label">Fuel</div><div class="value" id="fuelNow">-</div><div class="small" id="fuelMeta"></div></div>
      <div class="card span-3"><div class="label">Next Stop</div><div class="value" id="nextStop">-</div><div class="small" id="stopMeta"></div></div>

      <div class="card span-8">
        <div class="label" style="margin-bottom:12px;">Strategy</div>
        <table><tbody>
          <tr><th>Session ID</th><td class="mono" id="sessionIdCell">-</td></tr>
          <tr><th>Publisher client</th><td id="publisherClient">-</td></tr>
          <tr><th>Publisher driver</th><td id="publisherDriver">-</td></tr>
          <tr><th>Fuel source</th><td id="fuelSource">-</td></tr>
          <tr><th>Laps remaining</th><td id="lapsRemaining">-</td></tr>
          <tr><th>Stops required</th><td id="stopsRequired">-</td></tr>
          <tr><th>Current tank range</th><td id="tankRange">-</td></tr>
          <tr><th>Fuel next stop</th><td id="fuelNextStop">-</td></tr>
          <tr><th>Fuel final stop</th><td id="fuelFinalStop">-</td></tr>
          <tr><th>4-tyre delta</th><td id="fourTyreDelta">-</td></tr>
          <tr><th>Tyres covered by fuel?</th><td id="tyresCovered">-</td></tr>
          <tr><th>Pit loss average</th><td id="pitLossAvg">-</td></tr>
        </tbody></table>
      </div>

      <div class="card span-4"><div class="label" style="margin-bottom:12px;">Connected Clients</div><div id="clients"></div></div>
      <div class="card span-12"><div class="label" style="margin-bottom:12px;">Raw State</div><pre id="raw" class="mono" style="white-space:pre-wrap; font-size:12px; margin:0;"></pre></div>
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
        const age = data.timestamp ? (Date.now()/1000 - data.timestamp) : 9999;
        const live = age < 5;
        document.getElementById("statusDot").className = "dot " + (live ? "good" : "warn");
        document.getElementById("statusText").textContent = live ? "Live" : `Stale (${age.toFixed(1)}s)`;
        document.getElementById("title").textContent = `${data.session_id || "Session"} dashboard`;
        document.getElementById("sessionIdCell").textContent = data.session_id || "-";
        document.getElementById("driverName").textContent = data.driver?.name || "-";
        document.getElementById("driverMeta").textContent = `Stint laps: ${data.driver?.stint_laps ?? "-"} | Source: ${data.publisher?.client_id || "-"}`;
        document.getElementById("raceLap").textContent = data.race?.lap ?? "-";
        document.getElementById("raceMeta").textContent = `Green flag: ${data.race?.green_flag_lap ?? "-"} | Total est: ${data.race?.laps_total_est ?? "-"}`;
        document.getElementById("fuelNow").textContent = data.fuel?.current_l == null ? "-" : `${Number(data.fuel.current_l).toFixed(1)} L`;
        document.getElementById("fuelMeta").textContent = `Burn ${data.fuel?.burn_lpl ?? "-"} L/lap | ${data.fuel?.laps_left ?? "-"} laps left`;
        document.getElementById("nextStop").textContent = data.strategy?.next_stop_lap ?? "-";
        document.getElementById("stopMeta").textContent = `Base: fuel only | last update ${age.toFixed(1)} s ago`;
        document.getElementById("publisherClient").textContent = data.publisher?.client_id || "-";
        document.getElementById("publisherDriver").textContent = data.publisher?.driver_name || "-";
        document.getElementById("fuelSource").textContent = data.fuel?.source || "-";
        document.getElementById("lapsRemaining").textContent = data.strategy?.laps_remaining ?? "-";
        document.getElementById("stopsRequired").textContent = data.strategy?.stops_required ?? "-";
        document.getElementById("tankRange").textContent = data.strategy?.full_tank_laps_est ?? "-";
        document.getElementById("fuelNextStop").textContent = data.strategy?.fuel_next_stop_l == null ? "-" : `${Number(data.strategy.fuel_next_stop_l).toFixed(1)} L`;
        document.getElementById("fuelFinalStop").textContent = data.strategy?.fuel_final_stop_l == null ? "-" : `${Number(data.strategy.fuel_final_stop_l).toFixed(1)} L`;
        document.getElementById("fourTyreDelta").textContent = data.strategy?.four_tyre_delta_s == null ? "-" : `+${Number(data.strategy.four_tyre_delta_s).toFixed(1)} s`;
        document.getElementById("tyresCovered").textContent = data.strategy?.four_tyres_covered_by_fuel ? "Yes" : "No";
        document.getElementById("pitLossAvg").textContent = data.pit?.pit_loss_avg_s == null ? "-" : `${Number(data.pit.pit_loss_avg_s).toFixed(1)} s`;

        const clientsWrap = document.getElementById("clients");
        clientsWrap.innerHTML = "";
        (data.connected_clients || []).forEach(c => {
          const div = document.createElement("div");
          div.style.border = "1px solid var(--border)";
          div.style.borderRadius = "12px";
          div.style.padding = "10px";
          div.style.marginBottom = "8px";
          div.innerHTML = `<div><strong>${c.client_id}</strong> ${c.is_active ? "(ACTIVE)" : ""}</div>
                           <div class="small">Driver: ${c.driver_name || "-"}</div>
                           <div class="small">Telemetry: ${c.telemetry_status || "-"}</div>
                           <div class="small">Fuel source: ${c.fuel_source || "-"}</div>
                           <div class="small">Updated: ${typeof c.age_s === "number" ? c.age_s.toFixed(1) : c.age_s} s ago</div>`;
          clientsWrap.appendChild(div);
        });

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
    cur.execute("CREATE TABLE IF NOT EXISTS latest_state (session_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at REAL NOT NULL)")
    conn.commit()
    conn.close()

def save_snapshot(session_id: str, payload: Dict[str, Any]) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO latest_state (session_id, payload_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
        """,
        (session_id, json.dumps(payload), time.time()),
    )
    conn.commit()
    conn.close()

def get_required_env(name: str, fallback: str = "") -> str:
    import os
    return os.environ.get(name, fallback)

def check_write_token(payload: Dict[str, Any]) -> bool:
    expected = get_required_env("WRITE_TOKEN", "")
    if not expected:
        return True
    actual = request.headers.get("X-Write-Token") or payload.get("write_token", "")
    return actual == expected

def check_read_token() -> bool:
    expected = get_required_env("READ_TOKEN", "")
    if not expected:
        return True
    actual = request.args.get("token", "")
    return actual == expected

def choose_active_client(session_id: str) -> Optional[str]:
    session = SESSION_STORE.get(session_id)
    if not session:
        return None
    now = time.time()
    best_id = None
    best_score = -999999
    for client_id, payload in session["clients"].items():
        publisher = payload.get("publisher", {})
        fuel = payload.get("fuel", {})
        ts = float(payload.get("timestamp") or 0)
        age = now - ts
        if age > 30:
            continue
        score = 0
        if publisher.get("active_source"): score += 100
        if publisher.get("telemetry_status") == "live": score += 50
        if fuel.get("source") == "live": score += 30
        if age < 3: score += 10
        score -= int(age)
        if score > best_score:
            best_score = score
            best_id = client_id
    return best_id

def rebuild_session_state(session_id: str) -> Optional[Dict[str, Any]]:
    session = SESSION_STORE.get(session_id)
    if not session:
        return None
    active_id = choose_active_client(session_id)
    session["active_client_id"] = active_id
    if not active_id:
        return None
    active_payload = session["clients"][active_id]
    merged = dict(active_payload)
    merged["publisher"] = dict(active_payload.get("publisher", {}))
    merged["publisher"]["client_id"] = active_id
    now = time.time()
    connected = []
    for client_id, payload in session["clients"].items():
        age = now - float(payload.get("timestamp") or 0)
        connected.append({
            "client_id": client_id,
            "driver_name": payload.get("publisher", {}).get("driver_name"),
            "telemetry_status": payload.get("publisher", {}).get("telemetry_status"),
            "fuel_source": payload.get("fuel", {}).get("source"),
            "age_s": age,
            "is_active": client_id == active_id,
        })
    merged["connected_clients"] = sorted(connected, key=lambda x: (not x["is_active"], x["age_s"]))
    session["state"] = merged
    return merged

@APP.get("/")
def index() -> Response:
    return jsonify({"ok": True, "message": "Relay is running", "routes": ["/api/update", "/api/session/<session_id>", "/session/<session_id>"]})

@APP.get("/health")
def health() -> Response:
    return jsonify({"ok": True, "server_time": time.time()})

@APP.post("/api/update")
def api_update() -> Response:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400
    if not check_write_token(payload):
        return jsonify({"ok": False, "error": "Invalid write token"}), 403
    session_id = str(payload.get("session_id", "")).strip()
    client_id = str(payload.get("publisher", {}).get("client_id", "")).strip()
    if not session_id:
        return jsonify({"ok": False, "error": "Missing session_id"}), 400
    if not client_id:
        return jsonify({"ok": False, "error": "Missing publisher.client_id"}), 400

    payload["timestamp"] = float(payload.get("timestamp") or time.time())
    with SESSION_LOCK:
        if session_id not in SESSION_STORE:
            SESSION_STORE[session_id] = {"clients": {}, "active_client_id": None, "state": {}}
        SESSION_STORE[session_id]["clients"][client_id] = payload
        merged = rebuild_session_state(session_id)
        if merged:
            save_snapshot(session_id, merged)

    return jsonify({"ok": True, "session_id": session_id, "active_client_id": SESSION_STORE[session_id]["active_client_id"]})

@APP.get("/api/session/<session_id>")
def api_session(session_id: str) -> Response:
    if not check_read_token():
        return jsonify({"ok": False, "error": "Invalid read token"}), 403
    with SESSION_LOCK:
        payload = SESSION_STORE.get(session_id, {}).get("state")
    if not payload:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT payload_json FROM latest_state WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            payload = json.loads(row[0])
    if not payload:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    return jsonify(payload)

@APP.get("/session/<session_id>")
def session_dashboard(session_id: str) -> str:
    return render_template_string(DASHBOARD_HTML)

def main() -> int:
    parser = argparse.ArgumentParser()
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
