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
  <title>iRacing Team Tracker v3</title>
  <style>
    :root {
      --bg:#0b0f16;
      --card:#121824;
      --card-2:#0f1520;
      --muted:#93a0b5;
      --text:#eef3fa;
      --accent:#6aa9ff;
      --good:#35c76f;
      --warn:#ffb020;
      --bad:#ff5c5c;
      --border:#202a3a;
      --soft:#171e2c;
    }
    * { box-sizing:border-box; }
    body {
      margin:0; padding:20px;
      background:var(--bg); color:var(--text);
      font-family:Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    }
    .wrap { max-width: 1500px; margin: 0 auto; }
    .topbar {
      display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:18px;
    }
    .title { font-size:22px; font-weight:800; margin:0; }
    .subtitle { color:var(--muted); font-size:13px; margin-top:6px; }
    .status-row { display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end; }
    .pill {
      display:inline-flex; align-items:center; gap:8px;
      border:1px solid var(--border);
      border-radius:999px;
      padding:8px 12px;
      background:rgba(255,255,255,0.02);
      font-size:13px;
      color:var(--text);
    }
    .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    .good { background:var(--good); }
    .warn { background:var(--warn); }
    .bad { background:var(--bad); }

    .header-grid {
      display:grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap:12px;
      margin-bottom:18px;
    }
    .grid {
      display:grid;
      grid-template-columns: 1.25fr 0.75fr;
      gap:16px;
    }
    .subgrid {
      display:grid;
      grid-template-columns: 1fr 1fr;
      gap:16px;
      margin-top:16px;
    }
    .card {
      background:linear-gradient(180deg, var(--card), var(--card-2));
      border:1px solid var(--border);
      border-radius:18px;
      padding:16px;
      box-shadow:0 10px 30px rgba(0,0,0,0.22);
    }
    .label {
      color:var(--muted);
      font-size:12px;
      text-transform:uppercase;
      letter-spacing:0.08em;
      margin-bottom:8px;
    }
    .big {
      font-size:28px;
      font-weight:800;
      line-height:1.05;
    }
    .med {
      font-size:16px;
      font-weight:700;
    }
    .small { font-size:12px; color:var(--muted); }
    .kpi-grid {
      display:grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap:12px;
      margin-top:12px;
    }
    .kpi {
      background:rgba(255,255,255,0.02);
      border:1px solid var(--border);
      border-radius:14px;
      padding:12px;
    }
    .kpi .value {
      font-size:24px;
      font-weight:800;
      margin-top:4px;
    }
    .section-title {
      font-size:14px;
      font-weight:800;
      color:var(--text);
      margin-bottom:12px;
      text-transform:uppercase;
      letter-spacing:0.06em;
    }
    .rows { display:grid; gap:10px; }
    .row {
      display:flex; justify-content:space-between; align-items:baseline; gap:16px;
      border-bottom:1px solid rgba(255,255,255,0.05);
      padding-bottom:10px;
    }
    .row:last-child { border-bottom:none; padding-bottom:0; }
    .row .name { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.06em; }
    .row .value { font-size:20px; font-weight:750; }
    .map-placeholder {
      min-height:280px;
      display:flex;
      flex-direction:column;
      justify-content:space-between;
      gap:14px;
    }
    .placeholder-box {
      flex:1;
      border:1px dashed rgba(106,169,255,0.35);
      background:rgba(106,169,255,0.04);
      border-radius:16px;
      display:flex;
      align-items:center;
      justify-content:center;
      color:var(--muted);
      text-align:center;
      padding:20px;
    }
    .context-list, .client-list { display:grid; gap:10px; }
    .context-item, .client-item {
      border:1px solid var(--border);
      background:rgba(255,255,255,0.02);
      border-radius:14px;
      padding:12px;
    }
    .context-head, .client-head {
      display:flex; justify-content:space-between; gap:12px; align-items:center;
      margin-bottom:6px;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    details { margin-top:16px; }
    summary {
      cursor:pointer;
      color:var(--muted);
      font-size:12px;
      text-transform:uppercase;
      letter-spacing:0.08em;
    }
    pre {
      white-space:pre-wrap;
      font-size:12px;
      margin-top:12px;
      color:#d9e4f5;
      background:#0b111a;
      border:1px solid var(--border);
      border-radius:14px;
      padding:12px;
      overflow:auto;
    }
    @media (max-width: 1100px) {
      .header-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid { grid-template-columns: 1fr; }
      .subgrid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div>
      <h1 class="title" id="title">iRacing Team Tracker</h1>
      <div class="subtitle">Race engineer view — strategy first, context second</div>
    </div>
    <div class="status-row">
      <div class="pill"><span class="dot good" id="statusDot"></span><span id="statusText">Waiting</span></div>
      <div class="pill">Fuel source: <strong id="fuelSourcePill">-</strong></div>
      <div class="pill">Publisher: <strong id="publisherPill">-</strong></div>
    </div>
  </div>

  <div class="header-grid">
    <div class="card">
      <div class="label">Current Driver</div>
      <div class="big" id="driverName">-</div>
      <div class="small" id="driverMeta">-</div>
    </div>
    <div class="card">
      <div class="label">Current Lap</div>
      <div class="big" id="raceLap">-</div>
      <div class="small" id="raceMeta">-</div>
    </div>
    <div class="card">
      <div class="label">Fuel</div>
      <div class="big" id="fuelNow">-</div>
      <div class="small" id="fuelMeta">-</div>
    </div>
    <div class="card">
      <div class="label">Next Stop</div>
      <div class="big" id="nextStop">-</div>
      <div class="small" id="stopMeta">-</div>
    </div>
    <div class="card">
      <div class="label">Stops Remaining</div>
      <div class="big" id="stopsRemainingHeader">-</div>
      <div class="small">Calculated from current tank range</div>
    </div>
    <div class="card">
      <div class="label">Tyre Call</div>
      <div class="big" id="tyreCall">-</div>
      <div class="small" id="tyreCallMeta">-</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="section-title">Strategy</div>

      <div class="kpi-grid">
        <div class="kpi">
          <div class="label">Laps Left In Tank</div>
          <div class="value" id="lapsLeftTank">-</div>
        </div>
        <div class="kpi">
          <div class="label">Laps Remaining</div>
          <div class="value" id="lapsRemaining">-</div>
        </div>
        <div class="kpi">
          <div class="label">Fuel Next Stop</div>
          <div class="value" id="fuelNextStop">-</div>
        </div>
        <div class="kpi">
          <div class="label">Fuel Final Stop</div>
          <div class="value" id="fuelFinalStop">-</div>
        </div>
      </div>

      <div class="rows" style="margin-top:16px;">
        <div class="row"><div class="name">Session ID</div><div class="value mono" id="sessionIdCell">-</div></div>
        <div class="row"><div class="name">Current Tank Range</div><div class="value" id="tankRange">-</div></div>
        <div class="row"><div class="name">4-Tyre Delta</div><div class="value" id="fourTyreDelta">-</div></div>
        <div class="row"><div class="name">Tyres Covered By Fuel</div><div class="value" id="tyresCovered">-</div></div>
        <div class="row"><div class="name">Pit Loss Average</div><div class="value" id="pitLossAvg">-</div></div>
        <div class="row"><div class="name">Last Stop Lap</div><div class="value" id="lastStopLap">-</div></div>
        <div class="row"><div class="name">Last Fill Added</div><div class="value" id="lastFillAdded">-</div></div>
      </div>
    </div>

    <div class="card map-placeholder">
      <div>
        <div class="section-title">Track Context</div>
        <div class="placeholder-box">
          <div>
            <div class="med" style="margin-bottom:8px;">Track map / nearby-car context</div>
            <div class="small">Reserved panel for live map, nearby rivals and pit rejoin window.</div>
          </div>
        </div>
      </div>
      <div class="rows">
        <div class="row"><div class="name">Next Stop</div><div class="value" id="mapNextStop">-</div></div>
        <div class="row"><div class="name">4-Tyre Delta</div><div class="value" id="mapTyreDelta">-</div></div>
        <div class="row"><div class="name">Fuel Source</div><div class="value" id="mapFuelSource">-</div></div>
      </div>
    </div>
  </div>

  <div class="subgrid">
    <div class="card">
      <div class="section-title">Nearby Race Context</div>
      <div class="context-list" id="contextList">
        <div class="context-item">
          <div class="context-head"><strong>Current Car</strong><span class="small">Placeholder</span></div>
          <div class="small">Position, class position, gap ahead and gap behind will be shown here once the richer standings feed is connected.</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Stint / Fuel Trust</div>
      <div class="rows">
        <div class="row"><div class="name">Publisher Client</div><div class="value" id="publisherClient">-</div></div>
        <div class="row"><div class="name">Publisher Driver</div><div class="value" id="publisherDriver">-</div></div>
        <div class="row"><div class="name">Fuel Source</div><div class="value" id="fuelSource">-</div></div>
        <div class="row"><div class="name">Stint Laps</div><div class="value" id="stintLaps">-</div></div>
        <div class="row"><div class="name">Burn Rate</div><div class="value" id="burnRate">-</div></div>
        <div class="row"><div class="name">Connected Clients</div><div class="value" id="clientCount">-</div></div>
      </div>

      <div class="section-title" style="margin-top:18px;">Connected Clients</div>
      <div class="client-list" id="clients"></div>
    </div>
  </div>

  <details>
    <summary>Raw State</summary>
    <pre id="raw"></pre>
  </details>
</div>

<script>
  function fmtL(value) {
    return value == null ? "-" : `${Number(value).toFixed(1)} L`;
  }
  function fmtS(value) {
    return value == null ? "-" : `+${Number(value).toFixed(1)} s`;
  }
  function tyreCall(delta, covered) {
    if (covered) return ["Take 4 tyres", "Covered by fuelling time"];
    if (delta == null) return ["Unknown", "Insufficient data"];
    return [`+${Number(delta).toFixed(1)} s`, "4-tyre penalty vs fuel only"];
  }

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
      document.getElementById("fuelSourcePill").textContent = data.fuel?.source || "-";
      document.getElementById("publisherPill").textContent = data.publisher?.client_id || "-";

      document.getElementById("driverName").textContent = data.driver?.name || "-";
      document.getElementById("driverMeta").textContent = `Stint laps ${data.driver?.stint_laps ?? "-"} | Client ${data.publisher?.client_id || "-"}`;
      document.getElementById("raceLap").textContent = data.race?.lap ?? "-";
      document.getElementById("raceMeta").textContent = `Green flag ${data.race?.green_flag_lap ?? "-"} | Total est ${data.race?.laps_total_est ?? "-"}`;
      document.getElementById("fuelNow").textContent = fmtL(data.fuel?.current_l);
      document.getElementById("fuelMeta").textContent = `Burn ${data.fuel?.burn_lpl ?? "-"} L/lap | ${data.fuel?.laps_left ?? "-"} laps left`;
      document.getElementById("nextStop").textContent = data.strategy?.next_stop_lap ?? "-";
      document.getElementById("stopMeta").textContent = `Updated ${age.toFixed(1)} s ago`;
      document.getElementById("stopsRemainingHeader").textContent = data.strategy?.stops_required ?? "-";

      const call = tyreCall(data.strategy?.four_tyre_delta_s, data.strategy?.four_tyres_covered_by_fuel);
      document.getElementById("tyreCall").textContent = call[0];
      document.getElementById("tyreCallMeta").textContent = call[1];

      document.getElementById("sessionIdCell").textContent = data.session_id || "-";
      document.getElementById("lapsLeftTank").textContent = data.fuel?.laps_left ?? "-";
      document.getElementById("lapsRemaining").textContent = data.strategy?.laps_remaining ?? "-";
      document.getElementById("fuelNextStop").textContent = fmtL(data.strategy?.fuel_next_stop_l);
      document.getElementById("fuelFinalStop").textContent = fmtL(data.strategy?.fuel_final_stop_l);
      document.getElementById("tankRange").textContent = data.strategy?.full_tank_laps_est ?? "-";
      document.getElementById("fourTyreDelta").textContent = fmtS(data.strategy?.four_tyre_delta_s);
      document.getElementById("tyresCovered").textContent = data.strategy?.four_tyres_covered_by_fuel ? "Yes" : "No";
      document.getElementById("pitLossAvg").textContent = data.pit?.pit_loss_avg_s == null ? "-" : `${Number(data.pit.pit_loss_avg_s).toFixed(1)} s`;
      document.getElementById("lastStopLap").textContent = data.pit?.last_stop_lap ?? "-";
      document.getElementById("lastFillAdded").textContent = fmtL(data.fuel?.last_fill_added_l);

      document.getElementById("mapNextStop").textContent = data.strategy?.next_stop_lap ?? "-";
      document.getElementById("mapTyreDelta").textContent = fmtS(data.strategy?.four_tyre_delta_s);
      document.getElementById("mapFuelSource").textContent = data.fuel?.source || "-";

      document.getElementById("publisherClient").textContent = data.publisher?.client_id || "-";
      document.getElementById("publisherDriver").textContent = data.publisher?.driver_name || "-";
      document.getElementById("fuelSource").textContent = data.fuel?.source || "-";
      document.getElementById("stintLaps").textContent = data.driver?.stint_laps ?? "-";
      document.getElementById("burnRate").textContent = data.fuel?.burn_lpl == null ? "-" : `${Number(data.fuel.burn_lpl).toFixed(3)} L/lap`;
      document.getElementById("clientCount").textContent = (data.connected_clients || []).length;

      const clientsWrap = document.getElementById("clients");
      clientsWrap.innerHTML = "";
      (data.connected_clients || []).forEach(c => {
        const div = document.createElement("div");
        div.className = "client-item";
        div.innerHTML = `
          <div class="client-head"><strong>${c.client_id}</strong><span class="small">${c.is_active ? "ACTIVE" : "STANDBY"}</span></div>
          <div class="small">Driver: ${c.driver_name || "-"}</div>
          <div class="small">Telemetry: ${c.telemetry_status || "-"}</div>
          <div class="small">Fuel source: ${c.fuel_source || "-"}</div>
          <div class="small">Updated ${typeof c.age_s === "number" ? c.age_s.toFixed(1) : c.age_s} s ago</div>
        `;
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
