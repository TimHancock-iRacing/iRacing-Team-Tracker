#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

import requests

try:
    import irsdk
except Exception:
    irsdk = None


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


class Publisher:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.mode = config.get("mode", "mock")
        self.relay_url = config["relay"]["update_url"]
        self.write_token = config["relay"].get("write_token", "")
        self.local_state_path = config.get("local_state_path", "publisher_state.json")
        self.interval = float(config.get("publish_interval_s", 1.0))
        self.timeout = float(config.get("request_timeout_s", 0.75))

        self.client_id = config["client"]["client_id"]
        self.client_label = config["client"].get("client_label", self.client_id)
        self.driver_name = config["client"].get("driver_name", self.client_id)

        self.ir = irsdk.IRSDK() if irsdk and self.mode == "iracing" else None

        self.last_known_fuel: Optional[float] = None
        self.last_on_pit_road: Optional[bool] = None
        self.pit_entry_ts: Optional[float] = None
        self.pit_loss_samples = []
        self.last_stop_lap: Optional[int] = None
        self.last_fuel_snapshot_before_stop: Optional[float] = None
        self.last_fill_added_l: Optional[float] = None

        self.last_lap_number: Optional[int] = None
        self.last_lap_fuel: Optional[float] = None
        self.lap_samples = deque(maxlen=80)

        self.mock_state = {
            "lap": int(config.get("mock", {}).get("start_lap", 4)),
            "fuel_l": float(config.get("mock", {}).get("start_fuel_l", 96.0)),
            "burn_lpl": float(config.get("mock", {}).get("burn_lpl", 2.6)),
            "pit_every_laps": int(config.get("mock", {}).get("pit_every_laps", 28)),
            "driver_name": config.get("mock", {}).get("driver_name", self.driver_name),
            "stint_laps": int(config.get("mock", {}).get("stint_laps", 0)),
            "tyres": {
                "LF": {"wear": 96.0, "temp": 84.0},
                "RF": {"wear": 95.0, "temp": 86.0},
                "LR": {"wear": 97.0, "temp": 81.0},
                "RR": {"wear": 96.0, "temp": 82.0},
            }
        }

    def log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def get_ir(self, name: str, default: Any = None) -> Any:
        try:
            return self.ir[name]
        except Exception:
            return default

    def connect_iracing(self) -> bool:
        if not self.ir:
            return False
        try:
            if self.ir.is_initialized and self.ir.is_connected:
                return True
            return self.ir.startup()
        except Exception:
            return False

    def update_pit_tracking(self, lap_now: int, on_pit_road: bool, fuel_now: Optional[float]) -> None:
        now = time.time()
        if self.last_on_pit_road is None:
            self.last_on_pit_road = on_pit_road
            return

        if on_pit_road and not self.last_on_pit_road:
            self.pit_entry_ts = now
            self.last_fuel_snapshot_before_stop = fuel_now
            self.last_stop_lap = lap_now

        elif (not on_pit_road) and self.last_on_pit_road:
            if self.pit_entry_ts is not None:
                self.pit_loss_samples.append(now - self.pit_entry_ts)
            if fuel_now is not None and self.last_fuel_snapshot_before_stop is not None:
                delta = fuel_now - self.last_fuel_snapshot_before_stop
                self.last_fill_added_l = round(delta, 2) if delta > 0.5 else 0.0
            self.pit_entry_ts = None

        self.last_on_pit_road = on_pit_road

    def capture_lap_sample(self, lap_now: int, fuel_now: Optional[float], on_pit_road: bool) -> None:
        if fuel_now is None:
            return
        if self.last_lap_number is None:
            self.last_lap_number = lap_now
            self.last_lap_fuel = fuel_now
            return
        if lap_now > self.last_lap_number:
            burn = (self.last_lap_fuel - fuel_now) if self.last_lap_fuel is not None else None
            valid = False
            if burn is not None:
                valid = (not on_pit_road) and 0.5 <= burn <= 6.0
            self.lap_samples.append({
                "lap": lap_now,
                "burn": round(burn, 4) if burn is not None else None,
                "valid": valid,
                "fuel_end": round(fuel_now, 3),
            })
            self.last_lap_number = lap_now
            self.last_lap_fuel = fuel_now

    def get_burn_model(self) -> tuple[float, str, Optional[float], Optional[float]]:
        fallback = float(self.config["fuel"].get("fallback_burn_lpl", 2.6))
        valid = [s["burn"] for s in self.lap_samples if s.get("valid") and s.get("burn") is not None]
        last_lap = valid[-1] if valid else None
        if len(valid) >= 3:
            rolling = sum(valid[-5:]) / min(5, len(valid))
            return round(rolling, 3), "rolling", round(last_lap, 3) if last_lap else None, round(sum(valid) / len(valid), 3)
        if len(valid) > 0:
            avg = sum(valid) / len(valid)
            return round(avg, 3), "stint", round(last_lap, 3) if last_lap else None, round(avg, 3)
        return fallback, "fallback", None, None

    def tyre_call(self, tyres: Dict[str, Dict[str, float]], fuel_time_s: float) -> tuple[bool, str]:
        wear_values = [tyres[k]["wear"] for k in tyres if tyres[k].get("wear") is not None]
        temp_values = [tyres[k]["temp"] for k in tyres if tyres[k].get("temp") is not None]
        four_tyre_service_s = float(self.config["pit"].get("four_tyre_service_s", 24.0))
        covered = fuel_time_s >= four_tyre_service_s

        if covered:
            return True, "Take 4 tyres — covered by fuelling time"

        worst_wear = min(wear_values) if wear_values else 100.0
        hottest = max(temp_values) if temp_values else 0.0

        if worst_wear <= 70:
            return True, "Take 4 tyres — wear threshold reached"
        if hottest >= 105:
            return True, "Take 4 tyres — overheating risk"
        return False, "Fuel only baseline"

    def build_strategy(self, lap_now: int, fuel_now: float, burn_lpl: float, tyres: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        race_cfg = self.config["race"]
        fuel_cfg = self.config["fuel"]
        pit_cfg = self.config["pit"]

        laps_total_est = int(race_cfg.get("laps_total_est", 291))
        laps_remaining = max(0, laps_total_est - lap_now)
        tank_capacity_l = float(fuel_cfg.get("tank_capacity_l", 110.0))
        reserve_l = float(fuel_cfg.get("reserve_l", 3.0))
        usable_fuel = max(0.0, fuel_now - reserve_l)
        laps_left = round(usable_fuel / burn_lpl, 2) if burn_lpl > 0 else 0.0
        full_tank_laps_est = max(1, int(math.floor(max(0.0, tank_capacity_l - reserve_l) / burn_lpl)))

        additional_laps_needed = max(0.0, laps_remaining - laps_left)
        stops_required = 0 if additional_laps_needed <= 0 else math.ceil(additional_laps_needed / full_tank_laps_est)

        next_stop_lap = lap_now + int(laps_left) if laps_left > 2 else None

        fuel_next_stop_l = None
        fuel_final_stop_l = None

        if stops_required > 0:
            ideal_stint_laps = laps_remaining / (stops_required + 1)
            target_fuel = ideal_stint_laps * burn_lpl + reserve_l
            fuel_next_stop_l = min(tank_capacity_l, max(0.0, target_fuel))
            remaining_after_next = max(0.0, laps_remaining - ideal_stint_laps)
            fuel_final_stop_l = min(tank_capacity_l, max(0.0, remaining_after_next * burn_lpl + reserve_l))

        fuel_fill_rate_lps = float(pit_cfg.get("fuel_fill_rate_lps", 2.7))
        four_tyre_service_s = float(pit_cfg.get("four_tyre_service_s", 24.0))
        pit_loss_avg_s = round(sum(self.pit_loss_samples) / len(self.pit_loss_samples), 2) if self.pit_loss_samples else float(pit_cfg.get("pit_loss_avg_s", 65.0))

        fuel_time_next_stop = (fuel_next_stop_l / fuel_fill_rate_lps) if fuel_next_stop_l else 0.0
        four_tyre_delta_s = round(max(0.0, four_tyre_service_s - fuel_time_next_stop), 2)
        tyres_covered = fuel_time_next_stop >= four_tyre_service_s if fuel_next_stop_l else False

        recommend_tyres, recommendation_reason = self.tyre_call(tyres, fuel_time_next_stop)

        if stops_required == 0:
            pit_recommendation = "No stop required"
        elif recommend_tyres:
            pit_recommendation = "Fuel + 4 tyres"
        else:
            pit_recommendation = "Fuel only"

        return {
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
        }

    def build_iracing_state(self) -> TrackerState:
        if not self.connect_iracing():
            raise RuntimeError("Waiting for iRacing")

        session_info = self.ir.session_info or {}
        driver_info = session_info.get("DriverInfo", {}) if isinstance(session_info, dict) else {}
        team_car_idx = driver_info.get("DriverCarIdx")
        drivers = driver_info.get("Drivers", []) or []

        current_driver_name = self.driver_name
        for d in drivers:
            if d.get("CarIdx") == team_car_idx:
                current_driver_name = d.get("UserName") or d.get("TeamName") or current_driver_name
                break

        current_lap_completed = 0
        on_pit_road = False
        if team_car_idx is not None:
            current_lap_completed = int(self.get_ir("CarIdxLapCompleted", [0])[team_car_idx] or 0)
            on_pit_road = bool(self.get_ir("CarIdxOnPitRoad", [False])[team_car_idx])

        fuel_level = self.get_ir("FuelLevel", None)
        if fuel_level is not None and float(fuel_level) > 0:
            fuel_now = float(fuel_level)
            fuel_source = "live"
            self.last_known_fuel = fuel_now
        elif self.last_known_fuel is not None:
            fuel_now = self.last_known_fuel
            fuel_source = "stale"
        else:
            fuel_now = float(self.config["fuel"].get("fallback_current_fuel_l", 80.0))
            fuel_source = "fallback"

        self.update_pit_tracking(current_lap_completed, on_pit_road, fuel_now)
        self.capture_lap_sample(current_lap_completed, fuel_now, on_pit_road)
        burn_lpl, burn_source, last_lap_burn, stint_avg_burn = self.get_burn_model()

        tyres = {
            "LF": {"wear": None, "temp": None},
            "RF": {"wear": None, "temp": None},
            "LR": {"wear": None, "temp": None},
            "RR": {"wear": None, "temp": None},
        }

        strategy = self.build_strategy(current_lap_completed, fuel_now, burn_lpl, tyres)
        laps_left = round(max(0.0, fuel_now - self.config["fuel"].get("reserve_l", 3.0)) / burn_lpl, 2) if burn_lpl > 0 else None

        return TrackerState(
            session_id=self.config["session_id"],
            timestamp=time.time(),
            publisher={
                "client_id": self.client_id,
                "client_label": self.client_label,
                "driver_name": current_driver_name,
                "telemetry_status": "live",
                "active_source": fuel_source == "live",
            },
            race={
                "lap": current_lap_completed,
                "laps_total_est": int(self.config["race"].get("laps_total_est", 291)),
                "green_flag_lap": int(self.config["race"].get("green_flag_start_lap", 4)),
            },
            driver={
                "name": current_driver_name,
                "stint_laps": max(0, current_lap_completed - int(self.config["race"].get("green_flag_start_lap", 4)) + 1),
            },
            fuel={
                "current_l": round(fuel_now, 2),
                "burn_lpl": burn_lpl,
                "burn_source": burn_source,
                "last_lap_burn_l": last_lap_burn,
                "stint_avg_burn_l": stint_avg_burn,
                "laps_left": laps_left,
                "source": fuel_source,
                "last_fill_added_l": self.last_fill_added_l,
                "tank_capacity_l": float(self.config["fuel"].get("tank_capacity_l", 110.0)),
            },
            pit={
                "state": "pit" if on_pit_road else "track",
                "last_stop_lap": self.last_stop_lap,
                "pit_loss_avg_s": strategy["pit_loss_avg_s"],
            },
            strategy=strategy,
            tyres=tyres,
        )

    def build_mock_state(self) -> TrackerState:
        self.mock_state["lap"] += 1
        lap_now = self.mock_state["lap"]

        pit_every = self.mock_state["pit_every_laps"]
        is_pit_lap = (lap_now % pit_every == 0)

        if is_pit_lap:
            self.mock_state["fuel_l"] = float(self.config["fuel"].get("tank_capacity_l", 110.0))
            self.last_stop_lap = lap_now
            self.last_fill_added_l = 78.0
            pit_state = "pit"
        else:
            self.mock_state["fuel_l"] = max(0.0, self.mock_state["fuel_l"] - self.mock_state["burn_lpl"])
            pit_state = "track"

        # degrade tyres each lap, slightly more on RF/LF
        for key, wear_drop, temp_bias in [
            ("LF", 0.45, 0.5), ("RF", 0.6, 1.0), ("LR", 0.35, 0.2), ("RR", 0.4, 0.3)
        ]:
            tyre = self.mock_state["tyres"][key]
            if is_pit_lap:
                tyre["wear"] = 98.0
                tyre["temp"] = 80.0
            else:
                tyre["wear"] = max(45.0, tyre["wear"] - wear_drop)
                tyre["temp"] = min(112.0, max(72.0, tyre["temp"] + temp_bias))

        fuel_now = self.mock_state["fuel_l"]
        burn_lpl = self.mock_state["burn_lpl"]
        self.capture_lap_sample(lap_now, fuel_now, False)
        burn_lpl, burn_source, last_lap_burn, stint_avg_burn = self.get_burn_model()

        tyres = self.mock_state["tyres"]
        strategy = self.build_strategy(lap_now, fuel_now, burn_lpl, tyres)
        laps_left = round(max(0.0, fuel_now - self.config["fuel"].get("reserve_l", 3.0)) / burn_lpl, 2) if burn_lpl > 0 else None

        return TrackerState(
            session_id=self.config["session_id"],
            timestamp=time.time(),
            publisher={
                "client_id": self.client_id,
                "client_label": self.client_label,
                "driver_name": self.mock_state["driver_name"],
                "telemetry_status": "live",
                "active_source": True,
            },
            race={
                "lap": lap_now,
                "laps_total_est": int(self.config["race"].get("laps_total_est", 291)),
                "green_flag_lap": int(self.config["race"].get("green_flag_start_lap", 4)),
            },
            driver={
                "name": self.mock_state["driver_name"],
                "stint_laps": max(0, lap_now - int(self.config["race"].get("green_flag_start_lap", 4)) + 1),
            },
            fuel={
                "current_l": round(fuel_now, 2),
                "burn_lpl": burn_lpl,
                "burn_source": burn_source,
                "last_lap_burn_l": last_lap_burn,
                "stint_avg_burn_l": stint_avg_burn,
                "laps_left": laps_left,
                "source": "live",
                "last_fill_added_l": self.last_fill_added_l,
                "tank_capacity_l": float(self.config["fuel"].get("tank_capacity_l", 110.0)),
            },
            pit={
                "state": pit_state,
                "last_stop_lap": self.last_stop_lap,
                "pit_loss_avg_s": strategy["pit_loss_avg_s"],
            },
            strategy=strategy,
            tyres=tyres,
        )

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
        self.log(f"Starting pit logic v1 publisher in mode: {self.mode} as client {self.client_id}")
        while True:
            try:
                state = self.build_mock_state() if self.mode == "mock" else self.build_iracing_state()
                self.publish(state)
                self.log(
                    f"Lap {state.race['lap']} | Fuel {state.fuel['current_l']}L | Burn {state.fuel['burn_lpl']} | "
                    f"Next stop {state.strategy.get('next_stop_lap')} | {state.strategy.get('pit_recommendation')}"
                )
            except RuntimeError:
                self.log("Waiting for iRacing...")
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
