"""In-memory application state for SkyOps demo app."""
from __future__ import annotations
import copy
import datetime as _dt
from typing import Optional

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_DRONES: list[dict] = [
    {"id": "d1", "name": "Falcon-1", "model": "DJI M350", "battery": 92, "status": "Idle", "dock": "Dock-A"},
    {"id": "d2", "name": "Falcon-2", "model": "DJI M350", "battery": 88, "status": "Idle", "dock": "Dock-A"},
    {"id": "d3", "name": "Hawk-7", "model": "Autel EVO II", "battery": 12, "status": "Charging", "dock": "Dock-B"},
    {"id": "d4", "name": "Osprey-3", "model": "DJI M300", "battery": 64, "status": "In mission", "dock": "Dock-C"},
    {"id": "d5", "name": "Kite-5", "model": "Parrot ANAFI", "battery": 45, "status": "Idle", "dock": "Dock-B"},
    {"id": "d6", "name": "Raven-9", "model": "DJI M350", "battery": 100, "status": "Maintenance", "dock": "Dock-D"},
]

SEED_MISSIONS: list[dict] = [
    {
        "id": "m1", "name": "Port Survey Alpha", "drone_id": "d4", "drone_name": "Osprey-3",
        "type": "Survey", "site": "Mumbai Port", "altitude": 80, "speed": 8,
        "rth": True, "pilot": "Capt. Rao", "status": "In mission",
        "created": "2026-09-20T06:00:00Z",
    },
    {
        "id": "m2", "name": "Solar Inspection B7", "drone_id": "d1", "drone_name": "Falcon-1",
        "type": "Inspection", "site": "Bengaluru Solar Farm", "altitude": 45, "speed": 5,
        "rth": True, "pilot": "Dr. Mehta", "status": "Completed",
        "created": "2026-09-21T09:30:00Z",
    },
    {
        "id": "m3", "name": "Depot Patrol Morning", "drone_id": "d2", "drone_name": "Falcon-2",
        "type": "Patrol", "site": "Pune Depot", "altitude": 60, "speed": 10,
        "rth": False, "pilot": "Lt. Sharma", "status": "Scheduled",
        "created": "2026-09-22T04:00:00Z",
    },
]

SEED_LOGS: list[dict] = [
    {"id": "l1", "date": "2026-09-20", "drone_id": "d4", "drone": "Osprey-3", "duration": "42 min", "distance_km": 18.4, "incidents": 0},
    {"id": "l2", "date": "2026-09-21", "drone_id": "d1", "drone": "Falcon-1", "duration": "28 min", "distance_km": 12.1, "incidents": 0},
    {"id": "l3", "date": "2026-09-19", "drone_id": "d2", "drone": "Falcon-2", "duration": "35 min", "distance_km": 15.7, "incidents": 1},
    {"id": "l4", "date": "2026-09-18", "drone_id": "d5", "drone": "Kite-5", "duration": "20 min", "distance_km": 8.3, "incidents": 0},
    {"id": "l5", "date": "2026-09-17", "drone_id": "d4", "drone": "Osprey-3", "duration": "55 min", "distance_km": 24.2, "incidents": 0},
    {"id": "l6", "date": "2026-09-16", "drone_id": "d1", "drone": "Falcon-1", "duration": "31 min", "distance_km": 13.8, "incidents": 0},
    {"id": "l7", "date": "2026-09-15", "drone_id": "d3", "drone": "Hawk-7", "duration": "18 min", "distance_km": 7.5, "incidents": 2},
    {"id": "l8", "date": "2026-09-14", "drone_id": "d6", "drone": "Raven-9", "duration": "40 min", "distance_km": 17.0, "incidents": 0},
    {"id": "l9", "date": "2026-09-13", "drone_id": "d5", "drone": "Kite-5", "duration": "22 min", "distance_km": 9.6, "incidents": 0},
    {"id": "l10", "date": "2026-09-12", "drone_id": "d2", "drone": "Falcon-2", "duration": "37 min", "distance_km": 16.2, "incidents": 1},
]

SEED_SETTINGS: dict = {
    "display_name": "Alex Pilot",
    "email": "pilot@skyops.io",
    "units": "Metric",
    "email_reports": True,
}

ALL_BUGS = [
    "altitude_limit",
    "low_battery_assignable",
    "missing_mission",
    "settings_500",
    "logout_crash",
]


class AppState:
    """Singleton in-memory state."""

    def __init__(self) -> None:
        self.version: str = "1.0"
        self.bugs: list[str] = []
        self.chaos_seed: Optional[int] = None
        self.chaos_mutations: list[str] = []
        self._drones: list[dict] = []
        self._missions: list[dict] = []
        self._logs: list[dict] = []
        self._settings: dict = {}
        self._mission_counter: int = 4
        self._reset_data()

    def _reset_data(self) -> None:
        self._drones = copy.deepcopy(SEED_DRONES)
        self._missions = copy.deepcopy(SEED_MISSIONS)
        self._logs = copy.deepcopy(SEED_LOGS)
        self._settings = copy.deepcopy(SEED_SETTINGS)
        self._mission_counter = 4

    def reset(self) -> None:
        """Reset data but keep version/bugs/chaos."""
        self._reset_data()

    # --- drones ---

    def get_drones(self) -> list[dict]:
        return list(self._drones)

    def get_drone(self, drone_id: str) -> Optional[dict]:
        for d in self._drones:
            if d["id"] == drone_id:
                return d
        return None

    def get_drone_flights(self, drone_id: str) -> list[dict]:
        return [lg for lg in self._logs if lg["drone_id"] == drone_id][:3]

    # --- missions ---

    def get_missions(self, q: str = "") -> list[dict]:
        missions = list(self._missions)
        if "missing_mission" in self.bugs and missions:
            missions = missions[:-1]
        if q:
            q_lower = q.lower()
            missions = [m for m in missions if q_lower in m["name"].lower()]
        return missions

    def get_mission(self, mission_id: str) -> Optional[dict]:
        for m in self._missions:
            if m["id"] == mission_id:
                return m
        return None

    def create_mission(self, data: dict) -> dict:
        mid = f"m{self._mission_counter}"
        self._mission_counter += 1
        drone_id = data["drone_id"]
        mission = {
            "id": mid,
            "name": data["name"],
            "drone_id": drone_id,
            "drone_name": self._drone_name(drone_id),
            "type": data["type"],
            "site": data["site"],
            "altitude": int(data["altitude"]),
            "speed": int(data["speed"]),
            "rth": bool(data.get("rth", False)),
            "pilot": data.get("pilot", ""),
            "status": "Scheduled",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }
        self._missions.append(mission)
        return mission

    def abort_mission(self, mission_id: str) -> Optional[dict]:
        for m in self._missions:
            if m["id"] == mission_id:
                m["status"] = "Aborted"
                return m
        return None

    def mission_name_exists(self, name: str, exclude_id: str = "") -> bool:
        for m in self._missions:
            if m["name"].strip().lower() == name.strip().lower() and m["id"] != exclude_id:
                return True
        return False

    def _drone_name(self, drone_id: str) -> str:
        d = self.get_drone(drone_id)
        return d["name"] if d else drone_id

    # --- logs ---

    def get_logs(self, drone: str = "") -> list[dict]:
        logs = list(self._logs)
        if drone and drone != "all":
            logs = [lg for lg in logs if lg["drone"] == drone]
        return logs

    # --- settings ---

    def get_settings(self) -> dict:
        return dict(self._settings)

    def save_settings(self, data: dict) -> None:
        allowed = {"display_name", "units", "email_reports"}
        for k, v in data.items():
            if k in allowed:
                self._settings[k] = v

    # --- validation ---

    def validate_mission_step(self, step: str, data: dict) -> dict[str, str]:
        """Returns field->error dict. Empty = valid."""
        errors: dict[str, str] = {}
        if step == "details":
            name = (data.get("name") or "").strip()
            if not name:
                errors["name"] = "Mission name is required"
            elif self.mission_name_exists(name):
                errors["name"] = "A mission with this name already exists"
            if not data.get("type"):
                errors["type"] = "Mission type is required"
            if not data.get("site"):
                errors["site"] = "Site is required"
            if self.version >= "1.2" and not (data.get("pilot") or "").strip():
                errors["pilot"] = "Pilot in command is required (DGCA)"
        elif step == "drone":
            drone_id = data.get("drone_id") or ""
            if not drone_id:
                errors["drone_id"] = "Please select a drone"
            else:
                drone = self.get_drone(drone_id)
                if drone is None:
                    errors["drone_id"] = "Drone not found"
                elif drone["status"] != "Idle":
                    errors["drone_id"] = f"Drone is {drone['status']}"
                elif drone["battery"] < 30 and "low_battery_assignable" not in self.bugs:
                    errors["drone_id"] = f"Battery {drone['battery']}% — below 30%"
        elif step == "params":
            try:
                alt = float(data.get("altitude", 0))
                if alt < 10:
                    errors["altitude"] = "Altitude must be at least 10 m"
                elif alt > 120 and "altitude_limit" not in self.bugs:
                    errors["altitude"] = "Altitude must be at most 120 m (DGCA limit)"
            except (ValueError, TypeError):
                errors["altitude"] = "Altitude must be a number"
            try:
                spd = float(data.get("speed", 0))
                if spd < 1:
                    errors["speed"] = "Speed must be at least 1 m/s"
                elif spd > 15:
                    errors["speed"] = "Speed must be at most 15 m/s"
            except (ValueError, TypeError):
                errors["speed"] = "Speed must be a number"
        return errors

    def validate_create_mission(self, data: dict) -> dict[str, str]:
        """Full server-side validation at create time."""
        errors: dict[str, str] = {}
        errors.update(self.validate_mission_step("details", data))
        errors.update(self.validate_mission_step("drone", data))
        errors.update(self.validate_mission_step("params", data))
        return errors


# Singleton
app_state = AppState()
