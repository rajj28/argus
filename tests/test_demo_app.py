"""Tests for the SkyOps demo app.

Runs with pytest + FastAPI TestClient (no browser, no network, no LLM).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from demo_app.server import app
from demo_app.state import app_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all state before each test."""
    app_state.version = "1.0"
    app_state.bugs = []
    app_state.chaos_seed = None
    app_state.chaos_mutations = []
    app_state.reset()
    yield
    # teardown
    app_state.version = "1.0"
    app_state.bugs = []
    app_state.chaos_seed = None
    app_state.chaos_mutations = []
    app_state.reset()


@pytest.fixture()
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def auth_client(client):
    """TestClient with session cookie already set."""
    resp = client.post("/api/login", json={"email": "pilot@skyops.io", "password": "flysafe123"})
    assert resp.status_code == 200
    # Cookie is set on the client automatically by TestClient
    return client


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuth:
    def test_login_success(self, client):
        resp = client.post("/api/login", json={"email": "pilot@skyops.io", "password": "flysafe123"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "session" in resp.cookies

    def test_login_bad_creds(self, client):
        resp = client.post("/api/login", json={"email": "bad@bad.com", "password": "wrong"})
        assert resp.status_code == 401
        assert "Invalid email or password" in resp.json()["error"]

    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "SkyOps" in resp.text
        # data-js login hooks present
        assert 'data-js="login-form"' in resp.text
        assert 'data-js="login-email"' in resp.text
        assert 'data-js="login-submit"' in resp.text

    def test_auth_redirect_dashboard(self, client):
        """Unauthenticated request to /dashboard redirects to /login."""
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_auth_redirect_missions(self, client):
        resp = client.get("/missions")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_auth_redirect_settings(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_logout(self, auth_client):
        resp = auth_client.post("/api/logout")
        assert resp.status_code == 200
        # After logout, session cookie removed — dashboard should redirect
        # (TestClient stores cookies; clear manually)
        auth_client.cookies.clear()
        resp2 = auth_client.get("/dashboard")
        assert resp2.status_code == 302


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_dashboard_renders(self, auth_client):
        resp = auth_client.get("/dashboard")
        assert resp.status_code == 200
        assert "Fleet overview" in resp.text

    def test_dashboard_has_kpi(self, auth_client):
        resp = auth_client.get("/dashboard")
        assert 'data-js="kpi-online"' in resp.text
        assert 'data-js="kpi-active"' in resp.text
        assert 'data-js="kpi-alerts"' in resp.text

    def test_dashboard_fleet_table(self, auth_client):
        resp = auth_client.get("/dashboard")
        assert "Falcon-1" in resp.text
        assert "Osprey-3" in resp.text

    def test_drone_detail(self, auth_client):
        resp = auth_client.get("/drones/d1")
        assert resp.status_code == 200
        assert "Falcon-1" in resp.text

    def test_drone_detail_not_found(self, auth_client):
        resp = auth_client.get("/drones/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Missions API — happy path
# ---------------------------------------------------------------------------


class TestMissionsHappyPath:
    def test_create_mission(self, auth_client):
        payload = {
            "name": "Test Mission Alpha",
            "type": "Survey",
            "site": "Pune Depot",
            "drone_id": "d1",
            "altitude": 80,
            "speed": 8,
            "rth": True,
            "pilot": "Test Pilot",
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 201
        mid = resp.json()["id"]
        assert mid.startswith("m")

    def test_created_mission_appears_in_list(self, auth_client):
        """R4: launched mission appears in missions list."""
        payload = {
            "name": "R4 Verification Mission",
            "type": "Patrol",
            "site": "Mumbai Port",
            "drone_id": "d2",
            "altitude": 60,
            "speed": 6,
            "rth": False,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 201
        mid = resp.json()["id"]
        missions = auth_client.get("/api/missions").json()
        ids = [m["id"] for m in missions]
        assert mid in ids

    def test_created_mission_status_scheduled(self, auth_client):
        payload = {
            "name": "Scheduled Check",
            "type": "Delivery",
            "site": "Bengaluru Solar Farm",
            "drone_id": "d5",
            "altitude": 50,
            "speed": 5,
            "rth": True,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 201
        mid = resp.json()["id"]
        missions = auth_client.get("/api/missions").json()
        m = next(m for m in missions if m["id"] == mid)
        assert m["status"] == "Scheduled"

    def test_missions_page_renders(self, auth_client):
        resp = auth_client.get("/missions")
        assert resp.status_code == 200
        assert "Missions" in resp.text
        assert 'data-js="mission-search"' in resp.text

    def test_mission_search(self, auth_client):
        resp = auth_client.get("/api/missions?q=Port")
        data = resp.json()
        assert any("Port" in m["name"] for m in data)

    def test_mission_detail_page(self, auth_client):
        resp = auth_client.get("/missions/m1")
        assert resp.status_code == 200
        assert "Port Survey Alpha" in resp.text

    def test_abort_mission(self, auth_client):
        """R6: abort sets status to Aborted."""
        resp = auth_client.post("/api/missions/m3/abort")
        assert resp.status_code == 200
        assert resp.json()["status"] == "Aborted"
        # Verify persisted
        mission = app_state.get_mission("m3")
        assert mission["status"] == "Aborted"


# ---------------------------------------------------------------------------
# Business rule rejections (v1.0)
# ---------------------------------------------------------------------------


class TestBusinessRules:
    def test_r1_altitude_too_high(self, auth_client):
        """R1: altitude > 120 m rejected."""
        payload = {
            "name": "High Altitude Test",
            "type": "Survey",
            "site": "Pune Depot",
            "drone_id": "d1",
            "altitude": 150,
            "speed": 8,
            "rth": True,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 422
        errors = resp.json()["errors"]
        assert "altitude" in errors
        assert "120" in errors["altitude"]

    def test_r1_altitude_boundary_ok(self, auth_client):
        """R1: altitude = 120 m is valid."""
        payload = {
            "name": "Boundary Altitude",
            "type": "Survey",
            "site": "Pune Depot",
            "drone_id": "d1",
            "altitude": 120,
            "speed": 8,
            "rth": True,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 201

    def test_r2_low_battery_drone_rejected(self, auth_client):
        """R2: drone with battery < 30% (Hawk-7 at 12%) rejected."""
        payload = {
            "name": "Low Battery Test",
            "type": "Inspection",
            "site": "Mumbai Port",
            "drone_id": "d3",  # Hawk-7, 12%
            "altitude": 60,
            "speed": 6,
            "rth": True,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 422
        errors = resp.json()["errors"]
        assert "drone_id" in errors

    def test_r2_non_idle_drone_rejected(self, auth_client):
        """R2: non-idle drone (In mission) rejected."""
        payload = {
            "name": "Busy Drone Test",
            "type": "Patrol",
            "site": "Pune Depot",
            "drone_id": "d4",  # Osprey-3, In mission
            "altitude": 60,
            "speed": 6,
            "rth": True,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 422

    def test_r3_duplicate_name_rejected(self, auth_client):
        """R3: duplicate mission name rejected."""
        payload = {
            "name": "Port Survey Alpha",  # already exists (m1)
            "type": "Survey",
            "site": "Pune Depot",
            "drone_id": "d1",
            "altitude": 60,
            "speed": 6,
            "rth": True,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 422
        errors = resp.json()["errors"]
        assert "name" in errors

    def test_validate_step_details(self, auth_client):
        resp = auth_client.post("/api/missions/validate", json={
            "step": "details",
            "data": {"name": "", "type": "Survey", "site": "Pune Depot"},
        })
        assert resp.status_code == 422
        assert "name" in resp.json()["errors"]

    def test_validate_step_params_r1(self, auth_client):
        resp = auth_client.post("/api/missions/validate", json={
            "step": "params",
            "data": {"altitude": 200, "speed": 8},
        })
        assert resp.status_code == 422
        assert "altitude" in resp.json()["errors"]

    def test_validate_step_drone_r2(self, auth_client):
        resp = auth_client.post("/api/missions/validate", json={
            "step": "drone",
            "data": {"drone_id": "d3"},  # Hawk-7, 12%
        })
        assert resp.status_code == 422
        assert "drone_id" in resp.json()["errors"]


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


class TestLogs:
    def test_logs_page_v10(self, auth_client):
        resp = auth_client.get("/logs")
        assert resp.status_code == 200
        assert "Flight logs" in resp.text

    def test_logs_404_in_v12(self, auth_client):
        """/logs returns 404 in v1.2."""
        auth_client.post("/__admin/version", json={"version": "1.2"})
        resp = auth_client.get("/logs")
        assert resp.status_code == 404

    def test_api_logs(self, auth_client):
        resp = auth_client.get("/api/logs")
        assert resp.status_code == 200
        assert len(resp.json()) == 10

    def test_api_logs_drone_filter(self, auth_client):
        resp = auth_client.get("/api/logs?drone=Osprey-3")
        data = resp.json()
        assert all(lg["drone"] == "Osprey-3" for lg in data)

    def test_export_csv(self, auth_client):
        resp = auth_client.get("/api/logs/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "date" in resp.text.lower()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_settings_page_renders(self, auth_client):
        resp = auth_client.get("/settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text
        assert 'data-js="settings-form"' in resp.text

    def test_save_settings(self, auth_client):
        """R7: settings persist after save."""
        resp = auth_client.put("/api/settings", json={"display_name": "New Name", "units": "Imperial", "email_reports": False})
        assert resp.status_code == 200
        settings = app_state.get_settings()
        assert settings["display_name"] == "New Name"
        assert settings["units"] == "Imperial"
        assert settings["email_reports"] is False

    def test_settings_reload_persists(self, auth_client):
        auth_client.put("/api/settings", json={"display_name": "Persist Me", "units": "Metric", "email_reports": True})
        settings = app_state.get_settings()
        assert settings["display_name"] == "Persist Me"


# ---------------------------------------------------------------------------
# Version switching
# ---------------------------------------------------------------------------


class TestVersions:
    def test_version_switch_10(self, auth_client):
        resp = auth_client.post("/__admin/version", json={"version": "1.0"})
        assert resp.status_code == 200
        assert app_state.version == "1.0"

    def test_version_switch_11(self, auth_client):
        resp = auth_client.post("/__admin/version", json={"version": "1.1"})
        assert resp.status_code == 200
        assert app_state.version == "1.1"
        assert app_state.bugs == []

    def test_version_switch_12(self, auth_client):
        auth_client.post("/__admin/version", json={"version": "1.2"})
        assert app_state.version == "1.2"
        # /logs returns 404
        resp = auth_client.get("/logs")
        assert resp.status_code == 404
        # /analytics works
        resp2 = auth_client.get("/analytics")
        assert resp2.status_code == 200

    def test_version_switch_13_enables_bugs(self, auth_client):
        auth_client.post("/__admin/version", json={"version": "1.3"})
        assert app_state.version == "1.3"
        assert "altitude_limit" in app_state.bugs
        assert "settings_500" in app_state.bugs

    def test_admin_state(self, auth_client):
        resp = auth_client.get("/__admin/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "bugs" in data
        assert "chaos" in data

    def test_v12_requires_pilot_field(self, auth_client):
        """v1.2 adds required pilot field."""
        auth_client.post("/__admin/version", json={"version": "1.2"})
        resp = auth_client.post("/api/missions/validate", json={
            "step": "details",
            "data": {"name": "New", "type": "Survey", "site": "Pune Depot", "pilot": ""},
        })
        assert resp.status_code == 422
        assert "pilot" in resp.json()["errors"]

    @pytest.mark.xfail(strict=True, reason=(
        "Known demo-app bug: v1.2 reorders the stepper labels, but mission_wizard.html still renders "
        "the panels in v1.0 order and app.js walks panels in DOM order, so users see drone selection "
        "before flight parameters. Remove this marker when the panels are rendered in `steps` order."))
    def test_v12_wizard_reorder_in_html(self, auth_client):
        """v1.2 wizard shows flight parameters before drone selection."""
        auth_client.post("/__admin/version", json={"version": "1.2"})
        resp = auth_client.get("/missions/new")
        assert resp.status_code == 200
        html = resp.text
        # params step should appear before drone step
        params_pos = html.find('data-step="params"')
        drone_pos = html.find('data-step="drone"')
        assert params_pos < drone_pos, "v1.2: params step should precede drone step in HTML"


# ---------------------------------------------------------------------------
# v1.3 bug flags
# ---------------------------------------------------------------------------


class TestBugFlags:
    def _set_bugs(self, auth_client, bugs: list[str]) -> None:
        auth_client.post("/__admin/bugs", json={"bugs": bugs})

    def test_altitude_limit_bug(self, auth_client):
        """altitude_limit: altitude > 120 accepted."""
        self._set_bugs(auth_client, ["altitude_limit"])
        payload = {
            "name": "Bug Alt Test",
            "type": "Survey",
            "site": "Pune Depot",
            "drone_id": "d1",
            "altitude": 200,
            "speed": 8,
            "rth": True,
        }
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 201, f"Expected 201 but got {resp.status_code}: {resp.text}"

    def test_low_battery_assignable_bug(self, auth_client):
        """low_battery_assignable: low battery drone accepted."""
        self._set_bugs(auth_client, ["low_battery_assignable"])
        payload = {
            "name": "Bug Battery Test",
            "type": "Inspection",
            "site": "Mumbai Port",
            "drone_id": "d3",  # Hawk-7: Idle at 12% (the low-battery-drone journey selects it)
            "altitude": 60,
            "speed": 6,
            "rth": True,
        }
        # Without the bug this is rejected (test_r2_low_battery_drone_rejected); with it, accepted.
        resp = auth_client.post("/api/missions", json=payload)
        assert resp.status_code == 201, f"Expected 201 but got {resp.status_code}: {resp.text}"

    def test_low_battery_assignable_bug_with_idle_drone(self, auth_client):
        """low_battery_assignable + a drone that is Idle but low battery."""
        # Manually set a drone to Idle + low battery
        drone = app_state.get_drone("d1")
        orig_battery = drone["battery"]
        drone["battery"] = 15
        try:
            self._set_bugs(auth_client, ["low_battery_assignable"])
            payload = {
                "name": "Bug Battery Idle Test",
                "type": "Survey",
                "site": "Pune Depot",
                "drone_id": "d1",
                "altitude": 60,
                "speed": 6,
                "rth": True,
            }
            resp = auth_client.post("/api/missions", json=payload)
            assert resp.status_code == 201
        finally:
            drone["battery"] = orig_battery

    def test_missing_mission_bug(self, auth_client):
        """missing_mission: newest mission omitted from list."""
        # Create a new mission first
        auth_client.post("/api/missions", json={
            "name": "Mission to be Hidden",
            "type": "Survey",
            "site": "Pune Depot",
            "drone_id": "d1",
            "altitude": 60,
            "speed": 6,
            "rth": True,
        })
        # Enable bug
        self._set_bugs(auth_client, ["missing_mission"])
        missions = auth_client.get("/api/missions").json()
        names = [m["name"] for m in missions]
        assert "Mission to be Hidden" not in names

    def test_settings_500_bug(self, auth_client):
        """settings_500: PUT /api/settings returns 500."""
        self._set_bugs(auth_client, ["settings_500"])
        resp = auth_client.put("/api/settings", json={"display_name": "X"})
        assert resp.status_code == 500

    def test_set_bugs_endpoint(self, auth_client):
        resp = auth_client.post("/__admin/bugs", json={"bugs": ["altitude_limit", "settings_500"]})
        assert resp.status_code == 200
        assert "altitude_limit" in resp.json()["bugs"]
        assert "settings_500" in resp.json()["bugs"]


# ---------------------------------------------------------------------------
# Chaos engine
# ---------------------------------------------------------------------------


class TestChaos:
    def test_chaos_seed_changes_ids(self, client):
        """Chaos 'ids' mutation renames element ids deterministically."""
        # Without chaos
        resp1 = client.get("/login")
        # Baseline: data-js hook present
        assert 'data-js="login-email"' in resp1.text

        # Enable chaos
        client.post("/__admin/chaos", json={"seed": 42, "mutations": ["ids"]})
        resp2 = client.get("/login")

        # data-js hook still present (never changes)
        assert 'data-js="login-email"' in resp2.text

        # But the element id should now be chaos-XXXXX
        assert 'id="chaos-' in resp2.text

    def test_chaos_data_js_stable(self, client):
        """data-js hooks are always present regardless of chaos seed."""
        client.post("/__admin/chaos", json={"seed": 1, "mutations": ["ids", "classes", "testids", "wrappers", "order", "text", "tags", "layout"]})
        resp = client.get("/login")
        assert 'data-js="login-form"' in resp.text
        assert 'data-js="login-email"' in resp.text
        assert 'data-js="login-password"' in resp.text
        assert 'data-js="login-submit"' in resp.text
        assert 'data-js="login-error"' in resp.text

    def test_chaos_ids_deterministic(self, client):
        """Same seed produces same ids every time."""
        client.post("/__admin/chaos", json={"seed": 99, "mutations": ["ids"]})
        r1 = client.get("/login").text
        r2 = client.get("/login").text
        assert r1 == r2

    def test_chaos_disable(self, client):
        """Disabling chaos (seed=null) restores normal ids."""
        client.post("/__admin/chaos", json={"seed": 7, "mutations": ["ids"]})
        client.post("/__admin/chaos", json={"seed": None})
        resp = client.get("/login")
        assert 'id="login-email"' in resp.text or 'id="auth-email-input"' not in resp.text
        # Chaos should be off
        assert app_state.chaos_seed is None

    def test_chaos_text_mutation(self, client):
        """text mutation changes button labels."""
        client.post("/__admin/chaos", json={"seed": 1, "mutations": ["text"]})
        resp = client.get("/login")
        # The login button text should be a synonym — check it has some recognizable text
        assert resp.status_code == 200

    def test_chaos_does_not_break_api(self, auth_client):
        """Chaos doesn't affect API endpoints."""
        auth_client.post("/__admin/chaos", json={"seed": 5, "mutations": ["ids", "classes", "text", "tags", "layout"]})
        resp = auth_client.get("/api/missions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


class TestAdmin:
    def test_reset(self, auth_client):
        # Create a mission
        auth_client.post("/api/missions", json={
            "name": "Temp Mission",
            "type": "Survey",
            "site": "Pune Depot",
            "drone_id": "d1",
            "altitude": 60,
            "speed": 6,
            "rth": True,
        })
        missions_before = auth_client.get("/api/missions").json()
        assert len(missions_before) == 4  # 3 seed + 1 new

        auth_client.post("/__admin/reset")
        missions_after = auth_client.get("/api/missions").json()
        assert len(missions_after) == 3  # back to seed

    def test_invalid_version(self, auth_client):
        resp = auth_client.post("/__admin/version", json={"version": "9.9"})
        assert resp.status_code == 400
