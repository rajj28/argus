# Argus Live — testing the FlytBase cockpit (hackathon build spec)

Target: the starter-kit cockpit (`C:\Users\Acer\flytbase-ahc-swe-qa-hackathon`, running in Docker):
cockpit UI http://localhost:4010, backend + control API http://localhost:4000/api, control panel
http://localhost:4000/dashboard, WHEP video :8889. Read that repo's `AGENTS.md`, `README.md`, `docs/reference.md`
(control API, fault kinds, socket protocol) before coding. NEVER modify the starter-kit repo (we test it, we don't
change it) except where a task explicitly says "mutant".

Deliverable: our own scenarios (title, description, approach, video) proving Argus finds genuine user-facing issues.
Judged on validity, reproducibility & evidence, product understanding, breadth, depth, precision (no false positives,
duplicate symptoms grouped). Levels: L1 static UI + basic security, L2 live data/map/video/telemetry/state,
L3 multi-user/real-time, network recovery, performance, long-running.

## New package `argus/live/`
- `truth.py` — `TruthClient(base="http://localhost:4000")`: control API (health, devices, state, faults get/set/clear,
  command takeoff/land, sim start/stop/reset, video start/stop, add/remove drone) + `TelemetryFeed`: a python-socketio
  client that subscribes exactly like the cockpit does (see docs/reference.md "Socket protocol" and
  frontend/src/socket/socket-client.ts) and keeps, per device/attribute, the last payload + local receive time + count.
  `freshness(device, attr, now)` -> seconds since last message. All ground truth, never the UI.
- `ui_probe.py` — reads what the USER sees, generically: socket badge text, device rows (name + badge texts),
  selected-device telemetry fields (label -> text), video label + playback liveness (via `video.getVideoPlaybackQuality()`
  totalVideoFrames delta over 2 s and `currentTime` delta) + a perceptual hash of a video frame drawn to a canvas;
  map probe: find the element `[data-testid=map-canvas]`, walk its React fiber (`__reactFiber$*`) up to the component
  whose hook state holds a ref with `.current.scene && .current.entities`, then list entities: id/name, label text, ECEF
  position -> lat/lon/height (compute WGS84 yourself), and screen coords via `viewer.scene.cartesianToCanvasCoordinates`.
  Degrade gracefully (return None + reason) when a probe cannot find its target.
- `checks.py` — oracle library, each returns `Finding(kind, level, category, entity, title, detail, evidence)` or None:
  L1: `occluded_controls` (interactive element whose center `elementFromPoint` is another non-descendant element),
  `unreachable_content` (element outside viewport with no scrollable ancestor that can reveal it), `horizontal_overflow`,
  `clipped_text` (scrollWidth>clientWidth with overflow hidden and text), `blank_region` (large uniform-colour block over
  content, from a screenshot with Pillow), `overlapping_text_labels` (map labels whose screen boxes overlap - from the map
  probe; font ~12px, estimate width 7px/char), `status_conflict` (same entity shows contradictory states),
  `unauthenticated_privileged_surface` (control panel reachable & functional from the cockpit without auth).
  L2: `status_vs_truth` (UI badges/telemetry vs TruthClient within tolerance), `stale_looks_live` (truth feed silent or
  delayed > N s while UI shows live/connected and no stale marker), `video_frozen_but_live` (frames not advancing while label
  says live), `video_wrong_source` (after selecting drone N the frame hash matches another drone's clip), `map_position_vs_truth`
  (marker lat/lon vs truth > 25 m), `selection_consistency` (telemetry, video label, map focus all refer to the selected drone).
  L3: `propagation_delay` (change seen by user B > N s after truth), `recovery_correct` (after offline/kick: reconnects,
  no duplicate tracks, values resume), `responsiveness` (click-to-paint latency, long tasks via PerformanceObserver),
  `memory_growth` (CDP Performance.getMetrics JSHeapUsedSize trend over time).
- `scenario.py` — `Scenario` (id, level, category, title, description, approach, async run(ctx)) and `ScenarioContext`:
  `truth`, `new_user(label, device="desktop"|"phone"|"tablet")` -> Playwright page in its own context with
  `record_video_dir`, viewport/is_mobile/has_touch per device, trace on; `hud(page, text, state)` updates a fixed overlay
  (top-right, pointer-events:none, data-argus-hud, z-index max) so the recording narrates each step/check live;
  `step(text)`, `check(name, finding_or_ok)`; `fault(kind, **kw)` / `clear_faults()`; reset sim at start; teardown
  always clears faults. Results: `runs/live/<scenario_id>/{video.mp4, shots/*.png, evidence.json, verdict.json}`; for
  multi-user scenarios compose videos side by side (imageio-ffmpeg). Root-cause grouping: findings sharing
  (entity, underlying condition, time window) merge into one issue with symptoms listed.
- `report.py` — writes `EVALUATION.md` (system design section + numbered scenarios: title, description, approach,
  result, evidence summary, video path) and `runs/live/index.html`.
- CLI (`argus/cli.py`, add a `live` sub-app): `argus live scan --url`, `argus live run [--id ...] [--headed]`,
  `argus live report`.
The HUD overlay must be excluded from Argus's own snapshots and checks (skip `[data-argus-hud]`).
