#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

import requests


def parse_time_to_seconds(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s or s == "--":
        return None
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = parts
            return int(h) * 3600 + int(m) * 60 + float(sec)
        if len(parts) == 2:
            m, sec = parts
            return int(m) * 60 + float(sec)
        return float(s)
    except Exception:
        return None


def to_float(value: str) -> Optional[float]:
    if value is None:
        return None
    value = str(value).strip().replace(",", ".")
    if value in ("", "--"):
        return None
    value = value.replace(" °C", "").replace("C", "")
    try:
        return float(value)
    except ValueError:
        return None


@dataclass
class TrackerState:
    session_id: str
    timestamp: float
    publisher: Dict[str, Any]
    race: Dict[str, Any]
    driver: Dict[str, Any]
    fuel: Dict[str, Any]
    pit: Dict[str, Any]
    strategy: Dict[str, Any]
    tyres: Dict[str, Any]


class SessionCsvReplay:
    def __init__(self, path: str, loop: bool = False):
        self.path = path
        self.loop = loop
        self.rows = self._load(path)
        self.idx = 0
        self.finished = False

    def _load(self, path: str):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            rows = list(csv.reader(f, delimiter=";"))

        data = []
        for row in rows[3:]:
            if len(row) < 25:
                continue
            lap = (row[1] or "").strip()
            if not lap.isdigit():
                continue
            data.append({
                "lap": int(lap),
                "laptime_s": parse_time_to_seconds(row[2]),
                "stint": int(row[9]) if (row[9] or "").strip().isdigit() else None,
                "l_per_lap_reported": to_float(row[12]),
                "tank_l": to_float(row[13]),
                "avg_l_per_lap_reported": to_float(row[14]),
                "time_rem_s": parse_time_to_seconds(row[20]),
                "laps_rem_reported": to_float(row[21]),
                "fuel_to_add_reported": to_float(row[22]),
                "pit_marker": (row[23] or "").strip(),
                "track_temp_c": to_float(row[24]),
            })
        if not data:
            raise RuntimeError(f"No usable lap rows found in CSV: {path}")
        return data

    def next_row(self) -> Optional[Dict[str, Any]]:
        if self.finished:
            return None
        if self.idx >= len(self.rows):
            if self.loop:
                self.idx = 0
            else:
                self.finished = True
                return None
        row = self.rows[self.idx]
        self.idx += 1
        return row

    def progress(self) -> Dict[str, Any]:
        return {
            "current_index": self.idx,
            "total_rows": len(self.rows),
            "finished": self.finished,
            "loop": self.loop,
        }


class Publisher:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.mode = config.get("mode", "session_csv")
        self.relay_url = config["relay"]["update_url"]
        self.write_token = config["relay"].get("write_token", "")
        self.local_state_path = config.get("local_state_path", "publisher_state.json")
        self.interval = float(config.get("publish_interval_s", 1.0))
        self.timeout = float(config.get("request_timeout_s", 0.75))

        self.client_id = config["client"]["client_id"]
        self.client_label = config["client"].get("client_label", self.client_id)
        self.driver_name = config["client"].get("driver_name", self.client_id)

        session_csv_cfg = config.get("session_csv", {})
        self.replay = SessionCsvReplay(
            session_csv_cfg["path"],
            loop=bool(session_csv_cfg.get("loop", False))
        )

        self.last_known_fuel: Optional[float] = None
        self.last_on_pit_road: Optional[bool] = None
        self.pit_loss_samples = []
        self.last_stop_lap: Optional[int] = None
        self.last_fill_added_l: Optional[float] = None
        self.max_trusted_fuel_seen_l: Optional[float] = None
        self.effective_tank_capacity_l: Optional[float] = None

        self.last_lap_number: Optional[int] = None
        self.last_lap_fuel: Optional[float] = None
        self.lap_samples = deque(maxlen=80)
        self.green_pace_samples = deque(maxlen=20)
        self.caution_pace_samples = deque(maxlen=20)
        self.wet_pace_samples = deque(maxlen=20)
        self.initial_projection_laps: Optional[float] = None
        self.highest_projection_laps: Optional[float] = None
        self.last_state: Optional[TrackerState] = None

        self.last_trusted_tyre_snapshot: Dict[str, Any] = {
            "mode": "no_trusted_data",
            "snapshot_lap": None,
            "LF": None,
            "RF": None,
            "LR": None,
            "RR": None,
        }

    def log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def update_effective_tank_capacity(self, fuel_now: Optional[float], trusted_snapshot: bool) -> None:
        if not trusted_snapshot or fuel_now is None:
            return
        if self.max_trusted_fuel_seen_l is None or fuel_now > self.max_trusted_fuel_seen_l:
            self.max_trusted_fuel_seen_l = round(fuel_now, 3)
            self.effective_tank_capacity_l = round(self.max_trusted_fuel_seen_l, 3)

    def update_pit_tracking(self, lap_now: int, on_pit_road: bool, fuel_now: Optional[float], pit_marker: str) -> None:
        if self.last_on_pit_road is None:
            self.last_on_pit_road = on_pit_road
            return

        if on_pit_road and not self.last_on_pit_road:
            self.last_stop_lap = lap_now

        elif (not on_pit_road) and self.last_on_pit_road:
            if pit_marker.startswith("OUT"):
                try:
                    self.pit_loss_samples.append(float(pit_marker.split("/")[0].replace("OUT", "").strip()))
                except Exception:
                    pass
            if fuel_now is not None:
                self.update_effective_tank_capacity(fuel_now, trusted_snapshot=True)

        self.last_on_pit_road = on_pit_road

    def capture_lap_sample(self, lap_now: int, fuel_now: Optional[float], reported_burn: Optional[float], lap_time_s: Optional[float], regime_hint: str) -> None:
        if fuel_now is None:
            return

        if self.last_lap_number is None:
            self.last_lap_number = lap_now
            self.last_lap_fuel = fuel_now
            return

        if lap_now > self.last_lap_number:
            calc_burn = (self.last_lap_fuel - fuel_now) if self.last_lap_fuel is not None else None
            burn = reported_burn if reported_burn is not None else calc_burn
            valid = False
            if burn is not None:
                valid = 0.5 <= burn <= 6.0

            sample = {
                "lap": lap_now,
                "burn": round(burn, 4) if burn is not None else None,
                "calc_burn": round(calc_burn, 4) if calc_burn is not None else None,
                "reported_burn": round(reported_burn, 4) if reported_burn is not None else None,
                "lap_time_s": lap_time_s,
                "valid": valid,
                "regime_hint": regime_hint,
                "fuel_end": round(fuel_now, 3),
            }
            self.lap_samples.append(sample)

            if lap_time_s is not None:
                if regime_hint == "caution":
                    self.caution_pace_samples.append(lap_time_s)
                elif regime_hint == "wet":
                    self.wet_pace_samples.append(lap_time_s)
                else:
                    self.green_pace_samples.append(lap_time_s)

            self.last_lap_number = lap_now
            self.last_lap_fuel = fuel_now

    def get_burn_model(self, reported_avg: Optional[float] = None):
        fallback = float(self.config["fuel"].get("fallback_burn_lpl", 2.6))
        valid = [s["burn"] for s in self.lap_samples if s.get("valid") and s.get("burn") is not None]
        last_lap = valid[-1] if valid else None

        if len(valid) >= 3:
            rolling = sum(valid[-5:]) / min(5, len(valid))
            stint_avg = sum(valid) / len(valid)
            return round(rolling, 3), "rolling", round(last_lap, 3) if last_lap else None, round(stint_avg, 3)

        if len(valid) > 0:
            avg = sum(valid) / len(valid)
            return round(avg, 3), "stint", round(last_lap, 3) if last_lap else None, round(avg, 3)

        if reported_avg is not None:
            return round(reported_avg, 3), "reported_avg", None, round(reported_avg, 3)

        return fallback, "fallback", None, None

    def infer_regime(self, lap_time_s: Optional[float], pit_marker: str) -> str:
        if pit_marker == "PIT":
            return "caution"
        baseline = (sum(self.green_pace_samples) / len(self.green_pace_samples)) if self.green_pace_samples else None
        if baseline and lap_time_s and lap_time_s > baseline * 1.35:
            return "caution"
        return "green"

    def get_projected_lap_time(self, regime: str, lap_time_s: Optional[float]):
        if regime == "caution" and self.caution_pace_samples:
            return round(sum(self.caution_pace_samples) / len(self.caution_pace_samples), 3), "caution_bucket"
        if regime == "wet" and self.wet_pace_samples:
            return round(sum(self.wet_pace_samples) / len(self.wet_pace_samples), 3), "wet_bucket"
        if self.green_pace_samples:
            return round(sum(list(self.green_pace_samples)[-5:]) / min(5, len(self.green_pace_samples)), 3), "green_bucket"
        if lap_time_s:
            return round(lap_time_s, 3), "last_lap"
        return None, "none"

    def tyre_call(self, tyres: Dict[str, Any], fuel_time_s: float):
        if tyres.get("mode") != "trusted_snapshot":
            return False, "Fuel only baseline — no trusted tyre snapshot"
        return (fuel_time_s >= float(self.config["pit"].get("four_tyre_service_s", 24.0))), \
               ("Take 4 tyres — covered by fuelling time" if fuel_time_s >= float(self.config["pit"].get("four_tyre_service_s", 24.0)) else "Fuel only baseline")

    def build_strategy(self, lap_now: int, fuel_now: float, burn_lpl: float, tyres: Dict[str, Any], lap_time_s: Optional[float], time_rem_s: Optional[float], reported_laps_rem: Optional[float], reported_fuel_to_add: Optional[float], current_regime: str):
        race_cfg = self.config["race"]
        fuel_cfg = self.config["fuel"]
        pit_cfg = self.config["pit"]

        limit_type = race_cfg.get("limit_type", "time")
        nominal_tank_capacity_l = float(fuel_cfg.get("nominal_tank_capacity_l", 110.0))
        effective_tank_capacity_l = float(self.effective_tank_capacity_l or nominal_tank_capacity_l)
        reserve_l = float(fuel_cfg.get("reserve_l", 3.0))
        usable_fuel = max(0.0, fuel_now - reserve_l)
        laps_left = round(usable_fuel / burn_lpl, 2) if burn_lpl > 0 else 0.0
        full_tank_laps_est = max(1, int(math.floor(max(0.0, effective_tank_capacity_l - reserve_l) / burn_lpl)))

        projected_lap_time_s, projected_lap_time_source = self.get_projected_lap_time(current_regime, lap_time_s)
        projected_total_laps = None
        projected_laps_remaining = None

        if limit_type == "time":
            if time_rem_s is not None and projected_lap_time_s and projected_lap_time_s > 0:
                projected_laps_remaining = max(0.0, time_rem_s / projected_lap_time_s)
                projected_total_laps = lap_now + projected_laps_remaining
        else:
            projected_laps_remaining = reported_laps_rem if reported_laps_rem is not None else max(0, int(race_cfg.get("laps_total_est", 291)) - lap_now)
            projected_total_laps = lap_now + projected_laps_remaining

        if projected_total_laps is not None and self.initial_projection_laps is None and lap_now >= int(race_cfg.get("green_flag_start_lap", 4)):
            self.initial_projection_laps = projected_total_laps
        if projected_total_laps is not None:
            if self.highest_projection_laps is None or projected_total_laps > self.highest_projection_laps:
                self.highest_projection_laps = projected_total_laps

        laps_remaining = int(projected_laps_remaining) if projected_laps_remaining is not None else 0
        additional_laps_needed = max(0.0, laps_remaining - laps_left)
        stops_required = 0 if additional_laps_needed <= 0 else math.ceil(additional_laps_needed / full_tank_laps_est)
        next_stop_lap = lap_now + int(laps_left) if laps_left > 2 else None

        fuel_next_stop_l = None
        fuel_final_stop_l = None
        if stops_required > 0:
            ideal_stint_laps = laps_remaining / (stops_required + 1)
            target_fuel = ideal_stint_laps * burn_lpl + reserve_l
            fuel_next_stop_l = min(effective_tank_capacity_l, max(0.0, target_fuel))
            remaining_after_next = max(0.0, laps_remaining - ideal_stint_laps)
            fuel_final_stop_l = min(effective_tank_capacity_l, max(0.0, remaining_after_next * burn_lpl + reserve_l))

        fuel_fill_rate_lps = float(pit_cfg.get("fuel_fill_rate_lps", 2.7))
        four_tyre_service_s = float(pit_cfg.get("four_tyre_service_s", 24.0))
        pit_loss_avg_s = round(sum(self.pit_loss_samples) / len(self.pit_loss_samples), 2) if self.pit_loss_samples else float(pit_cfg.get("pit_loss_avg_s", 65.0))
        fuel_time_next_stop = (fuel_next_stop_l / fuel_fill_rate_lps) if fuel_next_stop_l else 0.0
        four_tyre_delta_s = round(max(0.0, four_tyre_service_s - fuel_time_next_stop), 2)
        tyres_covered, recommendation_reason = self.tyre_call(tyres, fuel_time_next_stop)

        if stops_required == 0:
            pit_recommendation = "No stop required"
        elif tyres_covered:
            pit_recommendation = "Fuel + 4 tyres"
        else:
            pit_recommendation = "Fuel only"

        return {
            "limit_type": limit_type,
            "current_regime": current_regime,
            "laps_remaining": laps_remaining,
            "stops_required": stops_required,
            "next_stop_lap": next_stop_lap,
            "full_tank_laps_est": full_tank_laps_est,
            "fuel_next_stop_l": round(fuel_next_stop_l, 2) if fuel_next_stop_l is not None else None,
            "fuel_final_stop_l": round(fuel_final_stop_l, 2) if fuel_final_stop_l is not None else None,
            "four_tyre_delta_s": four_tyre_delta_s,
            "four_tyres_covered_by_fuel": tyres_covered,
            "pit_loss_avg_s": pit_loss_avg_s,
            "pit_recommendation": pit_recommendation,
            "pit_recommendation_reason": recommendation_reason,
            "fuel_time_next_stop_s": round(fuel_time_next_stop, 2) if fuel_next_stop_l else None,
            "reported_fuel_to_add_l": reported_fuel_to_add,
            "projected_lap_time_s": projected_lap_time_s,
            "projected_lap_time_source": projected_lap_time_source,
            "projected_laps_remaining": round(projected_laps_remaining, 2) if projected_laps_remaining is not None else None,
            "projected_total_laps": round(projected_total_laps, 2) if projected_total_laps is not None else None,
            "initial_projection_laps": round(self.initial_projection_laps, 2) if self.initial_projection_laps is not None else None,
            "highest_projection_laps": round(self.highest_projection_laps, 2) if self.highest_projection_laps is not None else None,
            "projection_delta_from_initial": round(projected_total_laps - self.initial_projection_laps, 2) if projected_total_laps is not None and self.initial_projection_laps is not None else None,
        }

    def build_session_csv_state(self) -> Optional[TrackerState]:
        row = self.replay.next_row()
        if row is None:
            return None

        lap_now = row["lap"]
        pit_marker = row["pit_marker"] or ""
        on_pit_road = pit_marker == "PIT"
        fuel_now = row["tank_l"] if row["tank_l"] is not None else self.last_known_fuel
        if fuel_now is None:
            fuel_now = float(self.config["fuel"].get("fallback_current_fuel_l", 80.0))
            fuel_source = "fallback"
        else:
            fuel_source = "reported"

        self.last_known_fuel = fuel_now
        self.update_pit_tracking(lap_now, on_pit_road, fuel_now, pit_marker)
        current_regime = self.infer_regime(row["laptime_s"], pit_marker)
        self.capture_lap_sample(lap_now, fuel_now, row["l_per_lap_reported"], row["laptime_s"], current_regime)
        burn_lpl, burn_source, last_lap_burn, stint_avg_burn = self.get_burn_model(row["avg_l_per_lap_reported"])

        strategy = self.build_strategy(
            lap_now, fuel_now, burn_lpl, self.last_trusted_tyre_snapshot,
            row["laptime_s"], row["time_rem_s"], row["laps_rem_reported"], row["fuel_to_add_reported"], current_regime
        )

        laps_left = round(max(0.0, fuel_now - self.config["fuel"].get("reserve_l", 3.0)) / burn_lpl, 2) if burn_lpl > 0 else None

        state = TrackerState(
            session_id=self.config["session_id"],
            timestamp=time.time(),
            publisher={
                "client_id": self.client_id,
                "client_label": self.client_label,
                "driver_name": self.driver_name,
                "telemetry_status": "replay_csv",
                "active_source": True,
            },
            race={
                "lap": lap_now,
                "green_flag_lap": int(self.config["race"].get("green_flag_start_lap", 4)),
                "laptime_s": row["laptime_s"],
                "track_temp_c": row["track_temp_c"],
                "time_remaining_s": row["time_rem_s"],
                "csv_progress": self.replay.progress(),
            },
            driver={
                "name": self.driver_name,
                "stint_laps": row["stint"] or max(0, lap_now - int(self.config["race"].get("green_flag_start_lap", 4)) + 1),
            },
            fuel={
                "current_l": round(fuel_now, 2),
                "burn_lpl": burn_lpl,
                "burn_source": burn_source,
                "last_lap_burn_l": row["l_per_lap_reported"] if row["l_per_lap_reported"] is not None else last_lap_burn,
                "stint_avg_burn_l": row["avg_l_per_lap_reported"] if row["avg_l_per_lap_reported"] is not None else stint_avg_burn,
                "laps_left": laps_left,
                "source": fuel_source,
                "last_fill_added_l": self.last_fill_added_l,
                "nominal_tank_capacity_l": float(self.config["fuel"].get("nominal_tank_capacity_l", 110.0)),
                "effective_tank_capacity_l": float(self.effective_tank_capacity_l or self.config["fuel"].get("nominal_tank_capacity_l", 110.0)),
                "max_trusted_fuel_seen_l": self.max_trusted_fuel_seen_l,
                "reported_fuel_to_add_l": row["fuel_to_add_reported"],
            },
            pit={
                "state": "pit" if on_pit_road else ("out" if pit_marker.startswith("OUT") else "track"),
                "last_stop_lap": self.last_stop_lap,
                "pit_loss_avg_s": strategy["pit_loss_avg_s"],
                "pit_marker": pit_marker,
            },
            strategy=strategy,
            tyres=self.last_trusted_tyre_snapshot,
        )
        self.last_state = state
        return state

    def publish(self, state: TrackerState) -> None:
        payload = asdict(state)
        headers = {"Content-Type": "application/json"}
        if self.write_token:
            headers["X-Write-Token"] = self.write_token
        r = requests.post(self.relay_url, headers=headers, json=payload, timeout=self.timeout)
        r.raise_for_status()
        with open(self.local_state_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def run(self) -> int:
        self.log(f"Starting publisher in mode: {self.mode} as client {self.client_id}")
        while True:
            try:
                state = self.build_session_csv_state()
                if state is None:
                    self.log("CSV replay finished. No looping enabled, stopping publisher.")
                    return 0
                self.publish(state)
                self.log(
                    f"Lap {state.race['lap']} | Fuel {state.fuel['current_l']}L | Burn {state.fuel['burn_lpl']} ({state.fuel['burn_source']}) | "
                    f"Projected total {state.strategy.get('projected_total_laps')} | {state.strategy.get('pit_recommendation')}"
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.log(f"Publish failed: {exc}")
            time.sleep(self.interval)


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.example.json")
    args = ap.parse_args()
    app = Publisher(load_config(args.config))
    try:
        return app.run()
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
