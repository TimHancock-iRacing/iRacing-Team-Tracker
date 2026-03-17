#!/usr/bin/env python3
"""
Driver-side publisher for the standalone iRacing tracker.

Features
- Waits for iRacing rather than quitting
- Reads local iRacing SDK telemetry when available
- Publishes a cleaned state payload to the remote relay every second
- Can run in mock mode for internet-stack testing without iRacing
- Persists a local JSON snapshot

Run:
    python tracker_publisher.py --config config.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

import requests

try:
    import irsdk
except Exception:
    irsdk = None


@dataclass
class StrategyState:
    session_id: str
    timestamp: float
    race: Dict[str, Any]
    driver: Dict[str, Any]
    fuel: Dict[str, Any]
    pit: Dict[str, Any]
    strategy: Dict[str, Any]


class PublisherApp:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.mode = config.get("mode", "iracing")
        self.server_update_url = config["relay"]["update_url"]
        self.write_token = config["relay"].get("write_token", "")
        self.local_state_path = config.get("local_state_path", "publisher_state.json")
        self.publish_interval_s = float(config.get("publish_interval_s", 1.0))
        self.request_timeout_s = float(config.get("request_timeout_s", 0.75))
        self.mock_state = {
            "lap": int(config.get("mock", {}).get("start_lap", 4)),
            "fuel_l": float(config.get("mock", {}).get("start_fuel_l", 95.0)),
            "burn_lpl": float(config.get("mock", {}).get("burn_lpl", 2.6)),
            "driver_name": config.get("mock", {}).get("driver_name", "Tim Hancock"),
            "stint_laps": int(config.get("mock", {}).get("stint_laps", 0)),
        }
        self.ir = irsdk.IRSDK() if irsdk and self.mode == "iracing" else None
        self.connected_once = False

        self.last_lap_completed: Optional[int] = None
        self.last_on_pit_road: Optional[bool] = None
        self.pit_loss_samples: list[float] = []
        self.pit_entry_ts: Optional[float] = None

    def log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def try_connect_iracing(self) -> bool:
        if not self.ir:
            return False
        try:
            if self.ir.is_initialized and self.ir.is_connected:
                return True
            ok = self.ir.startup()
            if ok and not self.connected_once:
                self.connected_once = True
                self.log("Connected to iRacing SDK.")
            return ok
        except Exception:
            return False

    def get_ir_var(self, name: str, default: Any = None) -> Any:
        try:
            return self.ir[name]
        except Exception:
            return default

    def build_iracing_state(self) -> StrategyState:
        if not self.try_connect_iracing():
            raise RuntimeError("Waiting for iRacing")

        session_info = self.ir.session_info or {}
        driver_info = session_info.get("DriverInfo", {}) if isinstance(session_info, dict) else {}
        team_car_idx = driver_info.get("DriverCarIdx")
        drivers = driver_info.get("Drivers", []) or []

        driver_name = "Unknown Driver"
        for d in drivers:
            if d.get("CarIdx") == team_car_idx:
                driver_name = d.get("UserName") or d.get("TeamName") or driver_name
                break

        current_lap_completed = None
        lap_dist_pct = None
        on_pit_road = None
        fuel_level = self.get_ir_var("FuelLevel", None)
        if team_car_idx is not None:
            current_lap_completed = self.get_ir_var("CarIdxLapCompleted", [None])[team_car_idx]
            lap_dist_pct = self.get_ir_var("CarIdxLapDistPct", [None])[team_car_idx]
            on_pit_road = self.get_ir_var("CarIdxOnPitRoad", [None])[team_car_idx]

        if self.last_lap_completed is None and current_lap_completed is not None:
            self.last_lap_completed = int(current_lap_completed)

        stint_laps = 0
        if current_lap_completed is not None and self.last_lap_completed is not None:
            stint_laps = max(0, int(current_lap_completed) - int(self.config["race"].get("green_flag_start_lap", 4)) + 1)

        now = time.time()
        if self.last_on_pit_road is None:
            self.last_on_pit_road = bool(on_pit_road) if on_pit_road is not None else False

        if bool(on_pit_road) and not self.last_on_pit_road:
            self.pit_entry_ts = now
        elif (not bool(on_pit_road)) and self.last_on_pit_road:
            if self.pit_entry_ts is not None:
                self.pit_loss_samples.append(now - self.pit_entry_ts)
            self.pit_entry_ts = None
        self.last_on_pit_road = bool(on_pit_road) if on_pit_road is not None else False

        burn_lpl = self.config["fuel"].get("fallback_burn_lpl", 2.6)
        if fuel_level is not None:
            fuel_current = float(fuel_level)
        else:
            fuel_current = self.config["fuel"].get("fallback_current_fuel_l", 80.0)

        laps_left = round(fuel_current / burn_lpl, 2) if burn_lpl > 0 else None
        laps_total_est = self.config["race"].get("laps_total_est", 291)
        lap_now = int(current_lap_completed or 0)
        laps_remaining = max(0, int(laps_total_est) - lap_now)
        stops_required = 0
        if laps_left is not None and laps_left < laps_remaining and laps_left > 0:
            stops_required = math.ceil((laps_remaining - laps_left) / max(self.config["fuel"].get("full_tank_laps_est", 28), 1))

        fuel_needed_to_finish = max(0.0, laps_remaining * burn_lpl - fuel_current)
        fuel_time = fuel_needed_to_finish / max(self.config["pit"].get("fuel_fill_rate_lps", 2.7), 0.01)
        four_tyre_time = float(self.config["pit"].get("four_tyre_service_s", 24.0))
        four_tyre_delta = max(0.0, four_tyre_time - fuel_time)

        avg_pit_loss = round(sum(self.pit_loss_samples) / len(self.pit_loss_samples), 2) if self.pit_loss_samples else self.config["pit"].get("pit_loss_avg_s", 65.0)

        return StrategyState(
            session_id=self.config["session_id"],
            timestamp=now,
            race={
                "lap": lap_now,
                "laps_total_est": laps_total_est,
                "green_flag_lap": self.config["race"].get("green_flag_start_lap", 4),
            },
            driver={
                "name": driver_name,
                "stint_laps": stint_laps,
            },
            fuel={
                "current_l": round(fuel_current, 2),
                "burn_lpl": round(burn_lpl, 3),
                "laps_left": laps_left,
            },
            pit={
                "state": "pit" if bool(on_pit_road) else "track",
                "last_stop_lap": None,
                "pit_loss_avg_s": avg_pit_loss,
            },
            strategy={
                "laps_remaining": laps_remaining,
                "stops_required": stops_required,
                "next_stop_lap": (lap_now + int(laps_left)) if laps_left is not None else None,
                "fuel_to_add_l": round(fuel_needed_to_finish, 2),
                "four_tyre_delta_s": round(four_tyre_delta, 2),
            },
        )

    def build_mock_state(self) -> StrategyState:
        now = time.time()
        self.mock_state["lap"] += 1
        self.mock_state["stint_laps"] += 1
        self.mock_state["fuel_l"] = max(0.0, self.mock_state["fuel_l"] - self.mock_state["burn_lpl"])
        laps_total_est = self.config["race"].get("laps_total_est", 291)
        laps_remaining = max(0, laps_total_est - self.mock_state["lap"])
        laps_left = round(self.mock_state["fuel_l"] / self.mock_state["burn_lpl"], 2) if self.mock_state["burn_lpl"] > 0 else None
        fuel_needed_to_finish = max(0.0, laps_remaining * self.mock_state["burn_lpl"] - self.mock_state["fuel_l"])
        fuel_time = fuel_needed_to_finish / max(self.config["pit"].get("fuel_fill_rate_lps", 2.7), 0.01)
        four_tyre_time = float(self.config["pit"].get("four_tyre_service_s", 24.0))
        four_tyre_delta = max(0.0, four_tyre_time - fuel_time)

        return StrategyState(
            session_id=self.config["session_id"],
            timestamp=now,
            race={
                "lap": self.mock_state["lap"],
                "laps_total_est": laps_total_est,
                "green_flag_lap": self.config["race"].get("green_flag_start_lap", 4),
            },
            driver={
                "name": self.mock_state["driver_name"],
                "stint_laps": self.mock_state["stint_laps"],
            },
            fuel={
                "current_l": round(self.mock_state["fuel_l"], 2),
                "burn_lpl": round(self.mock_state["burn_lpl"], 3),
                "laps_left": laps_left,
            },
            pit={
                "state": "track",
                "last_stop_lap": None,
                "pit_loss_avg_s": self.config["pit"].get("pit_loss_avg_s", 65.0),
            },
            strategy={
                "laps_remaining": laps_remaining,
                "stops_required": 1 if laps_left is not None and laps_left < laps_remaining else 0,
                "next_stop_lap": (self.mock_state["lap"] + int(laps_left)) if laps_left is not None else None,
                "fuel_to_add_l": round(fuel_needed_to_finish, 2),
                "four_tyre_delta_s": round(four_tyre_delta, 2),
            },
        )

    def publish(self, state: StrategyState) -> None:
        payload = asdict(state)
        headers = {"Content-Type": "application/json"}
        if self.write_token:
            headers["X-Write-Token"] = self.write_token

        try:
            requests.post(
                self.server_update_url,
                headers=headers,
                json=payload,
                timeout=self.request_timeout_s,
            ).raise_for_status()
            with open(self.local_state_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self.log(f"Published lap {payload['race']['lap']} for {payload['driver']['name']}")
        except Exception as exc:
            self.log(f"Publish failed: {exc}")

    def run(self) -> int:
        self.log(f"Starting in mode: {self.mode}")
        while True:
            try:
                if self.mode == "mock":
                    state = self.build_mock_state()
                else:
                    state = self.build_iracing_state()
                self.publish(state)
            except RuntimeError:
                self.log("Waiting for iRacing...")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.log(f"Loop error: {exc}")
            time.sleep(self.publish_interval_s)


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main() -> int:
    parser = argparse.ArgumentParser(description="Driver-side publisher for the standalone iRacing tracker")
    parser.add_argument("--config", default="config.example.json")
    args = parser.parse_args()

    config = load_config(args.config)
    app = PublisherApp(config)
    try:
        return app.run()
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
