# Task: build `demo_app/` — "SkyOps", a drone-operations console used as Argus's test target

Owner: you own ONLY `demo_app/**` and `tests/test_demo_app.py`. Read `AGENTS.md` first.

SkyOps must look like a real, polished SaaS console (dark "mission control" theme, like a drone fleet
platform). Its purpose: let us "deploy" versions 1.0 → 1.3 that contain (a) cosmetic refactors, (b) intended
flow changes, (c) real bugs, plus a server-side **chaos engine** — so we can prove the tester self-heals, adapts
and tells bugs from intended changes.

## Stack
* FastAPI + Jinja2 server-rendered pages + small vanilla JS (no build step, no CDN needed, inline CSS in one
  `static/app.css`, JS in `static/app.js`). Run: `.venv/Scripts/python.exe -m demo_app.server --port 8000`.
* In-memory state with seed data; `POST /__admin/reset` restores seed data (keeps version/flags).
* All data mutations go through JSON APIs called with `fetch` (so the tester sees XHR: method, path, status).
* The app's own JS must locate elements ONLY via `data-js="..."` hooks (never ids/classes), because ids,
  classes, texts, order and tags change between versions / chaos. `data-js` values never change.

## Pages (v1.0 baseline)
Top nav (v1.0: horizontal top bar): Dashboard · Missions · Flight logs · Settings · user menu with "Log out".
1. `/login` — title "SkyOps — Drone Operations Console". Email, Password, button **"Log in"**.
   Demo creds `pilot@skyops.io` / `flysafe123`. `POST /api/login` → 200 + session cookie → redirect `/dashboard`;
   bad creds → 401 and a `role="alert"` "Invalid email or password". All other pages redirect to /login
   when not authenticated (R5).
2. `/dashboard` — heading "Fleet overview"; KPI cards (Drones online, Active missions, Open alerts); fleet table
   (Drone, Model, Battery %, Status Idle/In mission/Charging/Maintenance, Dock) with a "View" button per row
   (opens `/drones/{id}`: detail page with heading = drone name, battery, status, last 3 flights).
   Seed 6 drones, e.g. Falcon-1 (DJI M350, 92%, Idle), Falcon-2 (88%, Idle), Hawk-7 (12%, Charging),
   Osprey-3 (64%, In mission), Kite-5 (45%, Idle), Raven-9 (100%, Maintenance).
3. `/missions` — heading "Missions"; table (Name, Drone, Altitude (m), Status, Created); search box
   (placeholder "Search missions", filters via `GET /api/missions?q=`); button **"New mission"** → `/missions/new`.
   Seed 3 missions. Rows link to `/missions/{id}`.
4. `/missions/new` — heading "Plan a mission"; 4-step wizard on ONE url (JS panels, `location.hash` #step-…),
   stepper shows step titles. Each step validates client-side AND server-side (`POST /api/missions/validate`
   with `{step, data}` → 200 or 422 `{errors:{field: msg}}`; show messages in `role="alert"` under the field).
   * Step "Mission details": Mission name (required, unique — R3), Mission type (select: Survey, Inspection,
     Delivery, Patrol), Site (select: Pune Depot, Mumbai Port, Bengaluru Solar Farm). Button **"Next"**.
   * Step "Select drone": radio cards for each drone; drones not Idle or with battery < 30% are disabled with
     reason text ("Battery 12% — below 30%" / "Currently in mission") (R2). Buttons "Back", **"Next"**.
   * Step "Flight parameters": Altitude (m) number input (10–120, R1: error "Altitude must be at most 120 m
     (DGCA limit)"), Speed (m/s) 1–15, checkbox "Return to home on low battery" (checked). Buttons "Back", **"Next"**.
   * Step "Review & launch": summary list of all values; button **"Launch mission"** → `POST /api/missions`
     → 201 `{id}` → navigate to `/missions/{id}`, which shows a `role="status"` toast "Mission launched".
     Server re-validates everything (R1, R2, R3) and returns 422 on violation.
5. `/missions/{id}` — heading = mission name; fields (Drone, Type, Site, Altitude, Speed, Status "Scheduled");
   button "Abort mission" → `confirm()` dialog → `POST /api/missions/{id}/abort` → status "Aborted" (R6).
6. `/logs` — heading "Flight logs"; table of 10 seeded flights (Date, Drone, Duration, Distance km, Incidents);
   filter select "Drone" (All + names) via `GET /api/logs?drone=`; button "Export CSV" (downloads a CSV).
7. `/settings` — heading "Settings"; Display name (text), Email (readonly), Units radio (Metric/Imperial),
   checkbox "Email me flight reports"; button **"Save settings"** → `PUT /api/settings` → 200 → toast
   "Settings saved"; values persist after reload (R7).
8. "Log out" → `POST /api/logout` → redirect `/login`.

## Versions (switch at runtime, cumulative: 1.2 includes 1.1's refactor, 1.3 includes 1.2)
`POST /__admin/version {"version": "1.0"|"1.1"|"1.2"|"1.3"}`; `GET /__admin/state` → `{version, bugs, chaos}`.
Also `python -m demo_app.deploy 1.2` (calls the endpoint, resets data, and rewrites
`demo_app/context/CHANGELOG.md` to contain the release notes of all versions ≤ the deployed one, newest first).

* **1.1 "Aurora design system" (cosmetic only)** — every element `id` renamed (e.g. `login-email` →
  `auth-email-input`), classes become CSS-module style hashed names (`Button_primary__x7f2a`), `data-testid`
  attributes removed, nav moves to a LEFT SIDEBAR with order Missions · Dashboard · Flight logs · Settings,
  form fields wrapped in extra divs, "Site" field placed before "Mission type", some buttons become
  `<a role="button" href="#">`. Label polish: "Log in"→"Sign in", "New mission"→"Create mission",
  "Next"→"Continue", "Launch mission"→"Launch", "Save settings"→"Save changes". NO behaviour change.
* **1.2 "Mission planner v2" (intended behaviour changes)** — wizard order becomes Mission details → Flight
  parameters → Select drone → Review; NEW required field "Pilot in command" (text) on Mission details;
  `/logs` page and its nav item REMOVED (404); NEW page `/analytics` (nav "Analytics": heading "Analytics",
  totals cards + flight history table with drone filter).
* **1.3 "Performance improvements"** — same UI as 1.2 plus these bug flags ON by default (NOT mentioned in the
  changelog):
  * `altitude_limit` — altitude > 120 accepted client- and server-side (violates R1).
  * `low_battery_assignable` — drones with battery < 30% selectable and accepted by the server (violates R2).
  * `missing_mission` — `GET /api/missions` / the list page omits the newest mission (violates R4).
  * `settings_500` — `PUT /api/settings` returns 500 and the UI shows toast "Something went wrong".
  * `logout_crash` — clicking "Log out" throws an uncaught `TypeError` in JS and nothing happens.
* `POST /__admin/bugs {"bugs": [...]}` sets the bug flags explicitly for ANY version (bug-injection benchmark).

## Chaos engine (server-side, for the robustness benchmark)
`POST /__admin/chaos {"seed": 7, "mutations": ["ids","classes","testids","wrappers","order","text","tags","layout"]}`
(`{"seed": null}` disables). Mutations are deterministic for a seed and must NEVER change behaviour:
ids (random rename, keep `label[for]` consistent) · classes (hashed renames; keep styling via a data-js-free
approach e.g. keep one stable style hook `data-ui`) · testids (drop) · wrappers (wrap controls in 1–2 extra
div/span) · order (shuffle nav items, KPI cards, and independent form fields within a step) · text (swap button
labels for synonyms from a small table: Next↔Continue/Proceed, Save settings↔Save changes/Apply, Log in↔Sign in,
Launch mission↔Start mission/Launch, New mission↔Create mission/Add mission, Back↔Previous, View↔Open/Details)
· tags (`button` ↔ `a role=button` ↔ `div role=button tabindex=0` with keyboard support) · layout (sidebar vs
topbar). Implement with a Jinja helper object `ui` used everywhere in templates: `ui.id('login-email')`,
`ui.cls('btn btn-primary')`, `ui.text('Next')`, `ui.testid('login-submit')`, `ui.tag('button')`,
`ui.order([...])` — it resolves by (version profile ⊕ chaos seed).

## Context docs (for the tester's business understanding)
* `demo_app/context/PRODUCT.md` — what SkyOps is, personas (pilot, ops manager), key journeys, and business
  rules with ids: R1 max altitude 120 m AGL (DGCA) · R2 drones < 30% battery cannot be assigned · R3 mission names
  unique · R4 a launched mission appears in Missions with status "Scheduled" · R5 auth required · R6 abort needs
  confirmation and sets "Aborted" · R7 settings persist.
* `demo_app/context/releases/1.0.md … 1.3.md` — release notes. 1.1: "Visual refresh (Aurora design system).
  Navigation moved to a sidebar; button labels polished. No functional changes." 1.2: explains the wizard reorder
  ("so SkyOps can recommend drones that can fly the plan"), the new required "Pilot in command" field (DGCA
  compliance), retirement of Flight logs ("flight history now lives in the new Analytics page") and the new
  Analytics page. 1.3: "Faster mission list rendering; smaller JS bundle." (nothing else).
* `CHANGELOG.md` is generated by `deploy` from those files.

## Quality bar
* Looks professional (spacing, typography, status pills, battery bars). Accessible labels on every control.
* `tests/test_demo_app.py` (pytest + FastAPI TestClient, no browser): login, auth redirect, create mission happy
  path via API, R1/R2/R3 rejections in 1.0, each 1.3 bug flag observable via API, version switch, chaos seed makes
  ids differ but `data-js` hooks identical, `/logs` 404 in 1.2.
* Also verify manually with Playwright (headless) that the full wizard works in 1.0, 1.1, 1.2 (reordered) and under
  chaos seed 1 with all mutations. Fix anything broken.
* Finish with a short summary of endpoints, versions and how to run.
