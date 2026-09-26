"""Ground truth for the FlytBase cockpit: the simulator and control API, never the UI.

Every product built on the starter kit shares this backend, so these oracles carry over to any cockpit or
incident dashboard on top of it.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import Any, Optional


class Truth:
    def __init__(self, api: str = "http://localhost:4000/api"):
        self.api = api.rstrip("/")
        self.homes: dict[str, tuple[float, float]] = {}   # drone -> dock position, recorded at reset

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}

    # --- reads -----------------------------------------------------------------------------------
    def health(self) -> dict:
        return self._call("GET", "/health")

    def state(self) -> dict:
        return self._call("GET", "/control/state")

    def drone(self, drone_id: str) -> dict:
        return self.state().get("drones", {}).get(drone_id, {})

    def faults(self) -> Any:
        return self._call("GET", "/control/fault")

    def home_distance(self, drone_id: str) -> float:
        """Metres from the drone's dock (the cockpit's 'Dist. from home'), computed from truth positions."""
        d = self.drone(drone_id)
        home = self.homes.get(drone_id)
        if not d or not home:
            return float("nan")
        return haversine(d["latitude"], d["longitude"], home[0], home[1])

    # --- control -----------------------------------------------------------------------------------
    def reset(self, start: bool = True, base: int = 4) -> None:
        self.clear_faults()
        # remove any drones a previous scenario added, so every run starts from the same 4-drone world
        for did in list(self.state().get("drones", {})):
            try:
                if int(did.split("-")[-1]) > base:
                    self.remove_drone(did)
            except (ValueError, IndexError):
                pass
        self._call("POST", "/control/sim", {"action": "reset"})
        time.sleep(0.5)
        for did, d in self.state().get("drones", {}).items():   # after reset every drone sits on its dock
            self.homes[did] = (d["latitude"], d["longitude"])
        if start:
            self._call("POST", "/control/sim", {"action": "start"})

    def takeoff(self, drone_id: str) -> None:
        self._call("POST", "/control/command", {"deviceId": drone_id, "type": "takeoff"})

    def land(self, drone_id: str) -> None:
        self._call("POST", "/control/command", {"deviceId": drone_id, "type": "land"})

    def add_drone(self, name: str) -> dict:
        return self._call("POST", "/control/drones", {"name": name})

    def remove_drone(self, drone_id: str) -> None:
        try:
            self._call("DELETE", f"/control/drones/{drone_id}")
        except urllib.error.URLError:
            pass

    def speed(self, factor: float) -> None:
        self._call("POST", "/control/sim", {"action": "start", "speed": factor})

    def fault(self, kind: str, **kw) -> dict:
        return self._call("POST", "/control/fault", {"kind": kind, **kw})

    def clear_faults(self) -> None:
        try:
            self._call("DELETE", "/control/fault")
        except urllib.error.URLError:
            pass

    def wait_for(self, pred, timeout: float = 30, every: float = 0.5) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if pred():
                    return True
            except Exception:
                pass
            time.sleep(every)
        return False


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
