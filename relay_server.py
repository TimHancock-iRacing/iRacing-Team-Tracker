#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from typing import Any, Dict

from flask import Flask, Response, jsonify, render_template_string, request

APP = Flask(__name__)
SESSION_LOCK = threading.RLock()
DB_PATH = "relay_store.sqlite3"
SESSION_STORE: Dict[str, Dict[str, Any]] = {}
OVERRIDES: Dict[str, Dict[str, Any]] = {}

DASHBOARD_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>iRacing Team Tracker — Full Export</title>
  <style>
    :root { --bg:#0b0f16; --card:#121824; --card2:#0f1520; --muted:#93a0b5; --text:#eef3fa; --good:#35c76f; --warn:#ffb020; --bad:#ff5c5c; --border:#202a3a; }
    * { box-sizing:border-box; }
    body { margin:0; padding:20px; background:var(--bg); color:var(--text); font-family:Inter,system-ui,sans-serif; }
    .wrap { max-width: 1550px; margin: 0 auto; }
    .topbar { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:18px; }
    .title { font-size:22px; font-weight:800; margin:0; }
    .subtitle { color:var(--muted); font-size:13px; margin-top:6px; }
    .status-row { display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end; }
    .pill { display:inline-flex; align-items:center; gap:8px; border:1px solid var(--border); border-radius:999px; padding:8px 12px; background:rgba(255,255,255,0.02); font-size:13px; }
    .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    .good { background:var(--good); } .warn { background:var(--warn); } .bad { background:var(--bad); }
    .header-grid { display:grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap:12px; margin-bottom:18px; }
    .grid { display:grid; grid-template-columns: 1.1fr 0.9fr; gap:16px; }
    .subgrid { display:grid; grid-template-columns: 1fr 1fr; gap:16px; margin-top:16px; }
    .card { background:linear-gradient(180deg, var(--card), var(--card2)); border:1px solid var(--border); border-radius:18px; padding:16px; }
    .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px; }
    .big { font-size:28px; font-weight:800; line-height:1.05; }
    .small { font-size:12px; color:var(--muted); }
    .section-title { font-size:14px; font-weight:800; margin-bottom:12px; text-transform:uppercase; letter-spacing:0.06em; }
    .kpi-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:12px; margin-top:12px; }
    .kpi { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:14px; padding:12px; }
    .kpi .value { font-size:24px; font-weight:800; margin-top:4px; }
    .rows { display:grid; gap:10px; margin-top:16px; }
    .row { display:flex; justify-content:space-between; align-items:baseline; gap:16px; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:10px; }
    .row:last-child { border-bottom:none; padding-bottom:0; }
    .name { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.06em; }
    .value { font-size:20px; font-weight:750; }
    .recommend { padding:14px; border-radius:14px; border:1px solid var(--border); background:rgba(255,255,255,0.03); }
    .recommend .main { font-size:26px; font-weight:800; }
    .recommend .why { font-size:13px; color:var(--muted); margin-top:6px; }
    .tyre-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    .tyre { border-radius:14px; padding:14px; border:1px solid var(--border); min-height:110px; color:#fff; }
    .tyre h4 { margin:0 0 8px 0; font-size:16px; }
    .tyre .meta { font-size:14px; line-height:1.5; }
    .controls { display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }
    .btn { cursor:pointer; background:#182133; border:1px solid var(--border); color:var(--text); border-radius:12px; padding:10px 14px; }
    .btn.active { border-color:#6aa9ff; box-shadow:0 0 0 1px #6aa9ff inset; }
    details { margin-top:16px; }
    summary { cursor:pointer; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.08em; }
    pre { white-space:pre-wrap; font-size:12px; margin-top:12px; color:#d9e4f5; background:#0b111a; border:1px solid var(--border); border-radius:14px; padding:12px; overflow:auto; }
    @media (max-width: 1100px) { .header-grid { grid-template-columns: repeat(2, minmax(0,1fr)); } .grid,.subgrid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div><h1 class="title" id="title">iRacing Team Tracker</h1><div class="subtitle">Full export — strategy, projections, trusted tyres only, race-regime controls</div></div>
    <div class="status-row">
      <div class="pill"><span class="dot good" id="statusDot"></span><span id="statusText">Waiting</span></div>
      <div class="pill">Fuel source: <strong id="fuelSourcePill">-</strong></div>
      <div class="pill">Burn model: <strong id="burnSourcePill">-</strong></div>
      <div class="pill">Regime: <strong id="regimePill">-</strong></div>
    </div>
  </div>

  <div class="header-grid">
    <div class="card"><div class="label">Current Driver</div><div class="big" id="driverName">-</div><div class="small" id="driverMeta">-</div></div>
    <div class="card"><div class="label">Current Lap</div><div class="big" id="raceLap">-</div><div class="small" id="raceMeta">-</div></div>
    <div class="card"><div class="label">Fuel</div><div class="big" id="fuelNow">-</div><div class="small" id="fuelMeta">-</div></div>
    <div class="card"><div class="label">Next Stop</div><div class="big" id="nextStop">-</div><div class="small" id="stopMeta">-</div></div>
    <div class="card"><div class="label">Stops Remaining</div><div class="big" id="stopsRemainingHeader">-</div><div class="small">Live strategy model</div></div>
    <div class="card"><div class="label">Pit Call</div><div class="big" id="pitCallHeader">-</div><div class="small" id="pitCallHeaderMeta">-</div></div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="section-title">Strategy</div>
      <div class="recommend">
        <div class="main" id="pitRecommendation">-</div>
        <div class="why" id="pitRecommendationReason">-</div>
      </div>
      <div class="kpi-grid">
        <div class="kpi"><div class="label">Laps Left In Tank</div><div class="value" id="lapsLeftTank">-</div></div>
        <div class="kpi"><div class="label">Projected Laps Remaining</div><div class="value" id="lapsRemaining">-</div></div>
        <div class="kpi"><div class="label">Fuel Next Stop</div><div class="value" id="fuelNextStop">-</div></div>
        <div class="kpi"><div class="label">Fuel Final Stop</div><div class="value" id="fuelFinalStop">-</div></div>
      </div>
      <div class="rows">
        <div class="row"><div class="name">Race Limit Type</div><div class="value" id="limitType">-</div></div>
        <div class="row"><div class="name">Projected Total Laps</div><div class="value" id="projectedTotalLaps">-</div></div>
        <div class="row"><div class="name">Projection Delta</div><div class="value" id="projectionDelta">-</div></div>
        <div class="row"><div class="name">Current Tank Range</div><div class="value" id="tankRange">-</div></div>
        <div class="row"><div class="name">Effective Tank</div><div class="value" id="effectiveTank">-</div></div>
        <div class="row"><div class="name">Nominal Tank</div><div class="value" id="nominalTank">-</div></div>
        <div class="row"><div class="name">Reported Fuel To Add</div><div class="value" id="reportedFuelToAdd">-</div></div>
        <div class="row"><div class="name">Fuel Time Next Stop</div><div class="value" id="fuelTimeNextStop">-</div></div>
        <div class="row"><div class="name">4-Tyre Delta</div><div class="value" id="fourTyreDelta">-</div></div>
        <div class="row"><div class="name">Tyres Covered By Fuel</div><div class="value" id="tyresCovered">-</div></div>
        <div class="row"><div class="name">Pit Loss Average</div><div class="value" id="pitLossAvg">-</div></div>
        <div class="row"><div class="name">Pit Marker</div><div class="value" id="pitMarker">-</div></div>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Trusted Tyres Only</div>
      <div class="small" id="tyreSnapshotMeta">Awaiting trusted pit snapshot</div>
      <div class="tyre-grid" style="margin-top:12px;">
        <div id="tyre_LF" class="tyre"><h4>LF</h4><div class="meta">No trusted data</div></div>
        <div id="tyre_RF" class="tyre"><h4>RF</h4><div class="meta">No trusted data</div></div>
        <div id="tyre_LR" class="tyre"><h4>LR</h4><div class="meta">No trusted data</div></div>
        <div id="tyre_RR" class="tyre"><h4>RR</h4><div class="meta">No trusted data</div></div>
      </div>

      <div class="section-title" style="margin-top:18px;">Race Regime Override</div>
      <div class="small">Manual override is a dashboard aid in this export. It changes the displayed regime flag but does not yet round-trip back to the publisher.</div>
      <div class="controls">
        <button class="btn" onclick="setOverride('auto')">Auto</button>
        <button class="btn" onclick="setOverride('green')">Force Green</button>
        <button class="btn" onclick="setOverride('caution')">Force Safety Car</button>
        <button class="btn" onclick="setOverride('wet')">Force Wet</button>
      </div>
    </div>
  </div>

  <div class="subgrid">
    <div class="card">
      <div class="section-title">Fuel Trust</div>
      <div class="rows">
        <div class="row"><div class="name">Publisher Client</div><div class="value" id="publisherClient">-</div></div>
        <div class="row"><div class="name">Publisher Driver</div><div class="value" id="publisherDriver">-</div></div>
        <div class="row"><div class="name">Fuel Source</div><div class="value" id="fuelSource">-</div></div>
        <div class="row"><div class="name">Burn Source</div><div class="value" id="burnSource">-</div></div>
        <div class="row"><div class="name">Last Lap Burn</div><div class="value" id="lastLapBurn">-</div></div>
        <div class="row"><div class="name">Stint Avg Burn</div><div class="value" id="stintAvgBurn">-</div></div>
        <div class="row"><div class="name">Stint Laps</div><div class="value" id="stintLaps">-</div></div>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Projection Context</div>
      <div class="rows">
        <div class="row"><div class="name">Projected Lap Time</div><div class="value" id="projectedLapTime">-</div></div>
        <div class="row"><div class="name">Lap Time Source</div><div class="value" id="projectedLapTimeSource">-</div></div>
        <div class="row"><div class="name">Time Remaining</div><div class="value" id="timeRemaining">-</div></div>
        <div class="row"><div class="name">Initial Projection</div><div class="value" id="initialProjection">-</div></div>
        <div class="row"><div class="name">Highest Projection</div><div class="value" id="highestProjection">-</div></div>
      </div>
    </div>
  </div>

  <details>
    <summary>Raw State</summary>
    <pre id="raw"></pre>
  </details>
</div>

<script>
function fmtL(v){ return v == null ? "-" : `${Number(v).toFixed(1)} L`; }
function fmtS(v){ return v == null ? "-" : `${Number(v).toFixed(1)} s`; }
function fmtBurn(v){ return v == null ? "-" : `${Number(v).toFixed(3)} L/lap`; }
function tyreColor(temp, wear){
  if (wear == null) return "#182133";
  if (wear < 60) return "#7d2f2f";
  if (temp != null && temp > 105) return "#8d5a1c";
  if (temp != null && temp < 70) return "#294d81";
  return "#285f45";
}
function renderTyre(id, tyre){
  const el = document.getElementById(id);
  if (!tyre){
    el.style.background = "#182133";
    el.querySelector(".meta").innerHTML = "No trusted data";
    return;
  }
  el.style.background = tyreColor(tyre.temp, tyre.wear);
  el.querySelector(".meta").innerHTML = `Wear: ${tyre.wear == null ? "-" : Number(tyre.wear).toFixed(1)}%<br>Temp: ${tyre.temp == null ? "-" : Number(tyre.temp).toFixed(1)}°C`;
}
async function setOverride(mode){
  const parts = window.location.pathname.split("/");
  const sessionId = decodeURIComponent(parts[parts.length - 1]);
  const readToken = new URLSearchParams(window.location.search).get("token") || "";
  await fetch(`/api/session/${encodeURIComponent(sessionId)}/override`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({mode, token: readToken})
  });
  refresh();
}
async function refresh(){
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
    document.getElementById("fuelSourcePill").textContent = data.fuel?.source || "-";
    document.getElementById("burnSourcePill").textContent = data.fuel?.burn_source || "-";
    document.getElementById("regimePill").textContent = data.strategy?.display_regime || data.strategy?.current_regime || "-";

    document.getElementById("driverName").textContent = data.driver?.name || "-";
    document.getElementById("driverMeta").textContent = `Client ${data.publisher?.client_id || "-"} | ${data.publisher?.telemetry_status || "-"}`;
    document.getElementById("raceLap").textContent = data.race?.lap ?? "-";
    document.getElementById("raceMeta").textContent = `Track temp ${data.race?.track_temp_c ?? "-"}°C | Lap time ${data.race?.laptime_s ?? "-"}`;
    document.getElementById("fuelNow").textContent = fmtL(data.fuel?.current_l);
    document.getElementById("fuelMeta").textContent = `Burn ${fmtBurn(data.fuel?.burn_lpl)} | ${data.fuel?.laps_left ?? "-"} laps left`;
    document.getElementById("nextStop").textContent = data.strategy?.next_stop_lap ?? "-";
    document.getElementById("stopMeta").textContent = `Updated ${age.toFixed(1)} s ago`;
    document.getElementById("stopsRemainingHeader").textContent = data.strategy?.stops_required ?? "-";
    document.getElementById("pitCallHeader").textContent = data.strategy?.pit_recommendation || "-";
    document.getElementById("pitCallHeaderMeta").textContent = data.strategy?.pit_recommendation_reason || "-";

    document.getElementById("pitRecommendation").textContent = data.strategy?.pit_recommendation || "-";
    document.getElementById("pitRecommendationReason").textContent = data.strategy?.pit_recommendation_reason || "-";
    document.getElementById("lapsLeftTank").textContent = data.fuel?.laps_left ?? "-";
    document.getElementById("lapsRemaining").textContent = data.strategy?.projected_laps_remaining ?? data.strategy?.laps_remaining ?? "-";
    document.getElementById("fuelNextStop").textContent = fmtL(data.strategy?.fuel_next_stop_l);
    document.getElementById("fuelFinalStop").textContent = fmtL(data.strategy?.fuel_final_stop_l);
    document.getElementById("limitType").textContent = data.strategy?.limit_type || "-";
    document.getElementById("projectedTotalLaps").textContent = data.strategy?.projected_total_laps ?? "-";
    document.getElementById("projectionDelta").textContent = data.strategy?.projection_delta_from_initial ?? "-";
    document.getElementById("tankRange").textContent = data.strategy?.full_tank_laps_est ?? "-";
    document.getElementById("effectiveTank").textContent = fmtL(data.fuel?.effective_tank_capacity_l);
    document.getElementById("nominalTank").textContent = fmtL(data.fuel?.nominal_tank_capacity_l);
    document.getElementById("reportedFuelToAdd").textContent = fmtL(data.fuel?.reported_fuel_to_add_l ?? data.strategy?.reported_fuel_to_add_l);
    document.getElementById("fuelTimeNextStop").textContent = fmtS(data.strategy?.fuel_time_next_stop_s);
    document.getElementById("fourTyreDelta").textContent = fmtS(data.strategy?.four_tyre_delta_s);
    document.getElementById("tyresCovered").textContent = data.strategy?.four_tyres_covered_by_fuel ? "Yes" : "No";
    document.getElementById("pitLossAvg").textContent = fmtS(data.pit?.pit_loss_avg_s);
    document.getElementById("pitMarker").textContent = data.pit?.pit_marker || data.pit?.state || "-";

    const tyreMode = data.tyres?.mode || "no_trusted_data";
    const snapLap = data.tyres?.snapshot_lap;
    document.getElementById("tyreSnapshotMeta").textContent = tyreMode === "trusted_snapshot" ? `Trusted snapshot at lap ${snapLap}` : "Awaiting trusted pit snapshot";
    renderTyre("tyre_LF", data.tyres?.LF);
    renderTyre("tyre_RF", data.tyres?.RF);
    renderTyre("tyre_LR", data.tyres?.LR);
    renderTyre("tyre_RR", data.tyres?.RR);

    document.getElementById("publisherClient").textContent = data.publisher?.client_id || "-";
    document.getElementById("publisherDriver").textContent = data.publisher?.driver_name || "-";
    document.getElementById("fuelSource").textContent = data.fuel?.source || "-";
    document.getElementById("burnSource").textContent = data.fuel?.burn_source || "-";
    document.getElementById("lastLapBurn").textContent = fmtBurn(data.fuel?.last_lap_burn_l);
    document.getElementById("stintAvgBurn").textContent = fmtBurn(data.fuel?.stint_avg_burn_l);
    document.getElementById("stintLaps").textContent = data.driver?.stint_laps ?? "-";

    document.getElementById("projectedLapTime").textContent = fmtS(data.strategy?.projected_lap_time_s);
    document.getElementById("projectedLapTimeSource").textContent = data.strategy?.projected_lap_time_source || "-";
    document.getElementById("timeRemaining").textContent = fmtS(data.race?.time_remaining_s);
    document.getElementById("initialProjection").textContent = data.strategy?.initial_projection_laps ?? "-";
    document.getElementById("highestProjection").textContent = data.strategy?.highest_projection_laps ?? "-";

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

def check_read_token(req_token: str = "") -> bool:
    expected = get_required_env("READ_TOKEN", "")
    if not expected:
        return True
    return req_token == expected

def apply_override(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    override = OVERRIDES.get(session_id, {})
    out = json.loads(json.dumps(payload))
    display_regime = out.get("strategy", {}).get("current_regime")
    mode = override.get("mode", "auto")
    if mode and mode != "auto":
        display_regime = mode
    out.setdefault("strategy", {})
    out["strategy"]["display_regime"] = display_regime
    out["strategy"]["override_mode"] = mode
    return out

@APP.get("/")
def index() -> Response:
    return jsonify({"ok": True, "message": "Relay is running", "routes": ["/api/update", "/api/session/<session_id>", "/session/<session_id>"]})

@APP.post("/api/update")
def api_update() -> Response:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400
    if not check_write_token(payload):
        return jsonify({"ok": False, "error": "Invalid write token"}), 403
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        return jsonify({"ok": False, "error": "Missing session_id"}), 400
    payload["timestamp"] = float(payload.get("timestamp") or time.time())
    with SESSION_LOCK:
        SESSION_STORE[session_id] = {"state": payload}
        save_snapshot(session_id, payload)
    return jsonify({"ok": True, "session_id": session_id})

@APP.get("/api/session/<session_id>")
def api_session(session_id: str) -> Response:
    token = request.args.get("token", "")
    if not check_read_token(token):
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
    return jsonify(apply_override(session_id, payload))

@APP.post("/api/session/<session_id>/override")
def api_override(session_id: str) -> Response:
    body = request.get_json(silent=True) or {}
    token = body.get("token", "")
    if not check_read_token(token):
        return jsonify({"ok": False, "error": "Invalid read token"}), 403
    mode = str(body.get("mode", "auto")).strip().lower()
    if mode not in {"auto", "green", "caution", "wet"}:
        return jsonify({"ok": False, "error": "Invalid mode"}), 400
    OVERRIDES[session_id] = {"mode": mode, "updated_at": time.time()}
    return jsonify({"ok": True, "session_id": session_id, "mode": mode})

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
