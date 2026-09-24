"""SkyOps drone operations console — FastAPI server."""
from __future__ import annotations

import csv
import io
import pathlib
from typing import Any, Optional

from fastapi import FastAPI, Request, Response, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from demo_app.state import app_state, ALL_BUGS
from demo_app.ui_helper import UIHelper

BASE_DIR = pathlib.Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="SkyOps")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Demo credentials
DEMO_EMAIL = "pilot@skyops.io"
DEMO_PASSWORD = "flysafe123"
SESSION_TOKEN = "skyops-session-v1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ui() -> UIHelper:
    return UIHelper(app_state)


def _is_auth(session: Optional[str]) -> bool:
    return session == SESSION_TOKEN


def _tmpl(request: Request, name: str, ctx: dict) -> HTMLResponse:
    """Render a Jinja2 template with common context."""
    ctx["request"] = request
    ctx["ui"] = _ui()
    ctx["state"] = app_state
    ctx["version"] = app_state.version
    ctx["bugs"] = app_state.bugs
    return templates.TemplateResponse(request, name, ctx)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: Optional[str] = Cookie(default=None)):
    if _is_auth(session):
        return RedirectResponse("/dashboard", status_code=302)
    return _tmpl(request, "login.html", {"error": None})


@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    email = body.get("email", "")
    password = body.get("password", "")
    if email == DEMO_EMAIL and password == DEMO_PASSWORD:
        resp = JSONResponse({"ok": True})
        resp.set_cookie("session", SESSION_TOKEN, httponly=True, samesite="lax")
        return resp
    return JSONResponse({"error": "Invalid email or password"}, status_code=401)


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, session: Optional[str] = Cookie(default=None)):
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    drones = app_state.get_drones()
    online = sum(1 for d in drones if d["status"] in ("Idle", "In mission"))
    active_missions = sum(1 for m in app_state.get_missions() if m["status"] in ("Scheduled", "In mission"))
    alerts = sum(1 for d in drones if d["battery"] < 20)
    kpi = [
        {"label": "Drones online", "value": online, "js": "kpi-online"},
        {"label": "Active missions", "value": active_missions, "js": "kpi-active"},
        {"label": "Open alerts", "value": alerts, "js": "kpi-alerts"},
    ]
    return _tmpl(request, "dashboard.html", {"drones": drones, "kpi": kpi})


@app.get("/drones/{drone_id}", response_class=HTMLResponse)
async def drone_detail(drone_id: str, request: Request, session: Optional[str] = Cookie(default=None)):
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    drone = app_state.get_drone(drone_id)
    if not drone:
        return HTMLResponse("Drone not found", status_code=404)
    flights = app_state.get_drone_flights(drone_id)
    return _tmpl(request, "drone_detail.html", {"drone": drone, "flights": flights})


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------


@app.get("/missions", response_class=HTMLResponse)
async def missions_page(request: Request, session: Optional[str] = Cookie(default=None)):
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    q = request.query_params.get("q", "")
    missions = app_state.get_missions(q)
    return _tmpl(request, "missions.html", {"missions": missions, "q": q})


@app.get("/missions/new", response_class=HTMLResponse)
async def new_mission_page(request: Request, session: Optional[str] = Cookie(default=None)):
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    drones = app_state.get_drones()
    return _tmpl(request, "mission_wizard.html", {"drones": drones})


@app.get("/missions/{mission_id}", response_class=HTMLResponse)
async def mission_detail(mission_id: str, request: Request, session: Optional[str] = Cookie(default=None)):
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    mission = app_state.get_mission(mission_id)
    if not mission:
        return HTMLResponse("Mission not found", status_code=404)
    launched = request.query_params.get("launched") == "1"
    return _tmpl(request, "mission_detail.html", {"mission": mission, "launched": launched})


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, session: Optional[str] = Cookie(default=None)):
    if app_state.version >= "1.2":
        return HTMLResponse("Not Found", status_code=404)
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    drone_filter = request.query_params.get("drone", "")
    logs = app_state.get_logs(drone_filter)
    drones = app_state.get_drones()
    return _tmpl(request, "logs.html", {"logs": logs, "drones": drones, "drone_filter": drone_filter})


# ---------------------------------------------------------------------------
# Analytics (v1.2+)
# ---------------------------------------------------------------------------


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, session: Optional[str] = Cookie(default=None)):
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    drone_filter = request.query_params.get("drone", "")
    logs = app_state.get_logs(drone_filter)
    drones = app_state.get_drones()
    missions = app_state.get_missions()
    total_flights = len(app_state.get_logs())
    total_distance = sum(lg["distance_km"] for lg in app_state.get_logs())
    total_incidents = sum(lg["incidents"] for lg in app_state.get_logs())
    return _tmpl(request, "analytics.html", {
        "logs": logs,
        "drones": drones,
        "drone_filter": drone_filter,
        "total_flights": total_flights,
        "total_distance": round(total_distance, 1),
        "total_incidents": total_incidents,
        "total_missions": len(missions),
    })


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Optional[str] = Cookie(default=None)):
    if not _is_auth(session):
        return RedirectResponse("/login", status_code=302)
    settings = app_state.get_settings()
    return _tmpl(request, "settings.html", {"settings": settings})


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/missions")
async def api_get_missions(request: Request):
    q = request.query_params.get("q", "")
    return app_state.get_missions(q)


@app.post("/api/missions/validate")
async def api_validate_mission(request: Request):
    body = await request.json()
    step = body.get("step", "")
    data = body.get("data", {})
    errors = app_state.validate_mission_step(step, data)
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)
    return JSONResponse({"ok": True})


@app.post("/api/missions")
async def api_create_mission(request: Request):
    data = await request.json()
    errors = app_state.validate_create_mission(data)
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)
    mission = app_state.create_mission(data)
    return JSONResponse({"id": mission["id"]}, status_code=201)


@app.post("/api/missions/{mission_id}/abort")
async def api_abort_mission(mission_id: str):
    mission = app_state.abort_mission(mission_id)
    if not mission:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"status": mission["status"]})


@app.get("/api/logs")
async def api_get_logs(request: Request):
    drone = request.query_params.get("drone", "")
    return app_state.get_logs(drone)


@app.get("/api/logs/export")
async def api_export_logs(request: Request):
    drone = request.query_params.get("drone", "")
    logs = app_state.get_logs(drone)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "date", "drone", "duration", "distance_km", "incidents"])
    writer.writeheader()
    for row in logs:
        writer.writerow({k: row.get(k, "") for k in ["id", "date", "drone", "duration", "distance_km", "incidents"]})
    buf.seek(0)
    return StreamingResponse(
        io.BytesIO(buf.read().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=flight_logs.csv"},
    )


@app.put("/api/settings")
async def api_save_settings(request: Request):
    if "settings_500" in app_state.bugs:
        return JSONResponse({"error": "Internal Server Error"}, status_code=500)
    data = await request.json()
    app_state.save_settings(data)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@app.post("/__admin/reset")
async def admin_reset():
    app_state.reset()
    return JSONResponse({"ok": True})


@app.post("/__admin/version")
async def admin_set_version(request: Request):
    body = await request.json()
    version = body.get("version", "1.0")
    if version not in ("1.0", "1.1", "1.2", "1.3"):
        return JSONResponse({"error": "Invalid version"}, status_code=400)
    app_state.version = version
    # v1.3 enables bug flags by default
    if version == "1.3":
        app_state.bugs = list(ALL_BUGS)
    else:
        app_state.bugs = []
    return JSONResponse({"ok": True, "version": version})


@app.post("/__admin/bugs")
async def admin_set_bugs(request: Request):
    body = await request.json()
    bugs = body.get("bugs", [])
    app_state.bugs = [b for b in bugs if b in ALL_BUGS]
    return JSONResponse({"ok": True, "bugs": app_state.bugs})


@app.post("/__admin/chaos")
async def admin_set_chaos(request: Request):
    body = await request.json()
    seed = body.get("seed")  # None disables
    mutations = body.get("mutations", [
        "ids", "classes", "testids", "wrappers", "order", "text", "tags", "layout"
    ])
    app_state.chaos_seed = seed
    app_state.chaos_mutations = mutations if seed is not None else []
    return JSONResponse({"ok": True, "seed": seed, "mutations": app_state.chaos_mutations})


@app.get("/__admin/state")
async def admin_get_state():
    return JSONResponse({
        "version": app_state.version,
        "bugs": app_state.bugs,
        "chaos": {
            "seed": app_state.chaos_seed,
            "mutations": app_state.chaos_mutations,
        },
    })


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    return RedirectResponse("/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="SkyOps server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run("demo_app.server:app", host=args.host, port=args.port, reload=False)
