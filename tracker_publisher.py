
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
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
    publisher: Dict[str, Any]
    race: Dict[str, Any]
    driver: Dict[str, Any]
    fuel: Dict[str, Any]
    pit: Dict[str, Any]
    strategy: Dict[str, Any]

class PublisherV2:
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
        self.burn_window = []
        self.last_burn_reference_fuel: Optional[float] = None
        self.last_burn_reference_lap: Optional[int] = None

        self.mock_state = {
            "lap": int(config.get("mock", {}).get("start_lap", 4)),
            "fuel_l": float(config.get("mock", {}).get("start_fuel_l", 96.0)),
            "burn_lpl": float(config.get("mock", {}).get("burn_lpl", 2.6)),
            "pit_every_laps": int(config.get("mock", {}).get("pit_every_laps", 28)),
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

    def active_driver_confidence(self, fuel_source: str, telemetry_status: str) -> bool:
        return telemetry_status == "live" and fuel_source == "live"

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

    def rolling_burn(self, lap_now: int, fuel_now: Optional[float], on_pit_road: bool) -> float:
        fallback = float(self.config["fuel"].get("fallback_burn_lpl", 2.6))
        if fuel_now is None:
            return fallback
        if self.last_burn_reference_fuel is None or self.last_burn_reference_lap is None:
            self.last_burn_reference_fuel = fuel_now
            self.last_burn_reference_lap = lap_now
            return fallback
        if on_pit_road:
            return round(sum(self.burn_window) / len(self.burn_window), 3) if self.burn_window else fallback
        lap_delta = lap_now - self.last_burn_reference_lap
        fuel_delta = self.last_burn_reference_fuel - fuel_now
        if lap_delta > 0 and 0 < fuel_delta < 20:
            burn = fuel_delta / lap_delta
            if 0.5 <= burn <= 6.0:
                self.burn_window.append(burn)
                if len(self.burn_window) > 8:
                    self.burn_window.pop(0)
        self.last_burn_reference_fuel = fuel_now
        self.last_burn_reference_lap = lap_now
        return round(sum(self.burn_window) / len(self.burn_window), 3) if self.burn_window else fallback

    def build_strategy(self, lap_now: int, fuel_now: float, burn_lpl: float, pit_loss_avg_s: float) -> Dict[str, Any]:
        race_cfg = self.config["race"]
        fuel_cfg = self.config["fuel"]
        pit_cfg = self.config["pit"]

        laps_total_est = int(race_cfg.get("laps_total_est", 291))
        laps_remaining = max(0, laps_total_est - lap_now)
        tank_capacity_l = float(fuel_cfg.get("tank_capacity_l", 110.0))
        reserve_l = float(fuel_cfg.get("reserve_l", 3.0))
        usable_fuel_now = max(0.0, fuel_now - reserve_l)
        laps_left_now = round(usable_fuel_now / burn_lpl, 2) if burn_lpl > 0 else 0.0
        full_tank_laps_est = int(max(1, math.floor(max(0.0, tank_capacity_l - reserve_l) / burn_lpl)))
        additional_laps_needed_after_current = max(0.0, laps_remaining - laps_left_now)

        stops_required = 0 if additional_laps_needed_after_current <= 0 else math.ceil(additional_laps_needed_after_current / full_tank_laps_est)
        next_stop_lap = lap_now + int(laps_left_now) if laps_left_now > 2 else None

        fuel_next_stop_l = None
        fuel_final_stop_l = None
        if stops_required >= 1:
            next_stint_laps = min(full_tank_laps_est, max(0, laps_remaining - max(0, int(laps_left_now))))
            fuel_next_stop_l = min(tank_capacity_l, max(0.0, next_stint_laps * burn_lpl + reserve_l))
            if stops_required > 1:
                final_stint_laps = max(0, laps_remaining - max(0, int(laps_left_now)) - full_tank_laps_est * (stops_required - 1))
                fuel_final_stop_l = min(tank_capacity_l, max(0.0, final_stint_laps * burn_lpl + reserve_l))
            else:
                final_stint_laps = max(0, laps_remaining - max(0, int(laps_left_now)))
                fuel_final_stop_l = min(tank_capacity_l, max(0.0, final_stint_laps * burn_lpl + reserve_l))

        fuel_fill_rate_lps = float(pit_cfg.get("fuel_fill_rate_lps", 2.7))
        four_tyre_service_s = float(pit_cfg.get("four_tyre_service_s", 24.0))
        fuel_time_next_stop = (fuel_next_stop_l / fuel_fill_rate_lps) if fuel_next_stop_l else 0.0
        four_tyre_delta_s = round(max(0.0, four_tyre_service_s - fuel_time_next_stop), 2)
        four_tyres_covered_by_fuel = fuel_time_next_stop >= four_tyre_service_s if fuel_next_stop_l else False

        return {
            "laps_remaining": laps_remaining,
            "stops_required": stops_required,
            "next_stop_lap": next_stop_lap,
            "full_tank_laps_est": full_tank_laps_est,
            "fuel_next_stop_l": round(fuel_next_stop_l, 2) if fuel_next_stop_l is not None else None,
            "fuel_final_stop_l": round(fuel_final_stop_l, 2) if fuel_final_stop_l is not None else None,
            "four_tyre_delta_s": four_tyre_delta_s,
            "four_tyres_covered_by_fuel": four_tyres_covered_by_fuel,
            "pit_loss_avg_s": pit_loss_avg_s,
        }

    def build_iracing_state(self) -> StrategyState:
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

        fuel_level = self.get_ir("FuelLevel", None)
        current_lap_completed = None
        on_pit_road = False
        if team_car_idx is not None:
            current_lap_completed = self.get_ir("CarIdxLapCompleted", [0])[team_car_idx]
            on_pit_road = bool(self.get_ir("CarIdxOnPitRoad", [False])[team_car_idx])

        lap_now = int(current_lap_completed or 0)
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

        self.update_pit_tracking(lap_now, on_pit_road, fuel_now)
        burn_lpl = self.rolling_burn(lap_now, fuel_now, on_pit_road)
        pit_loss_avg_s = round(sum(self.pit_loss_samples) / len(self.pit_loss_samples), 2) if self.pit_loss_samples else float(self.config["pit"].get("pit_loss_avg_s", 65.0))
        strategy = self.build_strategy(lap_now, fuel_now, burn_lpl, pit_loss_avg_s)
        laps_left = round(max(0.0, fuel_now - self.config["fuel"].get("reserve_l", 3.0)) / burn_lpl, 2) if burn_lpl > 0 else None

        return StrategyState(
            session_id=self.config["session_id"],
            timestamp=time.time(),
            publisher={
                "client_id": self.client_id,
                "client_label": self.client_label,
                "driver_name": current_driver_name,
                "telemetry_status": "live",
                "active_source": self.active_driver_confidence(fuel_source, "live"),
            },
            race={"lap": lap_now, "laps_total_est": int(self.config["race"].get("laps_total_est", 291)), "green_flag_lap": int(self.config["race"].get("green_flag_start_lap", 4))},
            driver={"name": current_driver_name, "stint_laps": max(0, lap_now - int(self.config["race"].get("green_flag_start_lap", 4)) + 1)},
            fuel={"current_l": round(fuel_now, 2), "burn_lpl": burn_lpl, "laps_left": laps_left, "source": fuel_source, "last_fill_added_l": self.last_fill_added_l},
            pit={"state": "pit" if on_pit_road else "track", "last_stop_lap": self.last_stop_lap, "pit_loss_avg_s": pit_loss_avg_s},
            strategy=strategy,
        )

    def build_mock_state(self) -> StrategyState:
        self.mock_state["lap"] += 1
        lap_now = self.mock_state["lap"]
        is_pit_lap = (lap_now % self.mock_state["pit_every_laps"] == 0)
        if is_pit_lap:
            self.mock_state["fuel_l"] = float(self.config["fuel"].get("tank_capacity_l", 110.0))
            self.last_stop_lap = lap_now
            self.last_fill_added_l = 80.0
            pit_state = "pit"
        else:
            self.mock_state["fuel_l"] = max(0.0, self.mock_state["fuel_l"] - self.mock_state["burn_lpl"])
            pit_state = "track"

        fuel_now = self.mock_state["fuel_l"]
        burn_lpl = self.mock_state["burn_lpl"]
        pit_loss_avg_s = float(self.config["pit"].get("pit_loss_avg_s", 65.0))
        strategy = self.build_strategy(lap_now, fuel_now, burn_lpl, pit_loss_avg_s)

        return StrategyState(
            session_id=self.config["session_id"],
            timestamp=time.time(),
            publisher={"client_id": self.client_id, "client_label": self.client_label, "driver_name": self.driver_name, "telemetry_status": "live", "active_source": True},
            race={"lap": lap_now, "laps_total_est": int(self.config["race"].get("laps_total_est", 291)), "green_flag_lap": int(self.config["race"].get("green_flag_start_lap", 4))},
            driver={"name": self.driver_name, "stint_laps": max(0, lap_now - int(self.config["race"].get("green_flag_start_lap", 4)) + 1)},
            fuel={"current_l": round(fuel_now, 2), "burn_lpl": burn_lpl, "laps_left": round(max(0.0, fuel_now - self.config["fuel"].get("reserve_l", 3.0)) / burn_lpl, 2), "source": "live", "last_fill_added_l": self.last_fill_added_l},
            pit={"state": pit_state, "last_stop_lap": self.last_stop_lap, "pit_loss_avg_s": pit_loss_avg_s},
            strategy=strategy,
        )

    def publish(self, state: StrategyState) -> None:
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
                state = self.build_mock_state() if self.mode == "mock" else self.build_iracing_state()
                self.publish(state)
                self.log(f"Published lap {state.race['lap']} | fuel {state.fuel['current_l']} L | next stop {state.strategy.get('next_stop_lap')} | source {state.fuel['source']}")
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
    ap.add_argument("--config", default="config.v2.example.json")
    args = ap.parse_args()
    app = PublisherV2(load_config(args.config))
    try:
        return app.run()
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
