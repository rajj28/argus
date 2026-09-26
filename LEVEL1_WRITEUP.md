# Argus Live — Level 1: Static UI & Basic Security Testing

**Team:** solo · **Product under test:** the FlytBase cockpit (starter kit) — a live drone‑operations dashboard
with a Cesium map, telemetry panel and WebRTC video, driven by a simulator and a control API.

**One‑line:** Argus drives the real cockpit like an operator and checks every claim on the screen against the
drone simulator's actual state — so it catches mutations by *meaning*, not by matching pixels or selectors, with
near‑zero false positives.

**Video (all Level‑1 scenarios, real time):** _[paste your Google Drive link here]_
One recording runs each scenario in real time, introduced by a title card. A HUD overlay in the video shows, at
every moment, what the UI claims next to the ground truth and the verdict. Per‑scenario start times:
**0:03** Scenario 1 · **0:20** Scenario 2 · **1:30** Scenario 3 · **1:47** Scenario 4 · **2:00** Scenario 5.

---

## 1. System design

Argus Live has five parts that work together:

**Operators.** Real Chrome (via Playwright, with Chrome DevTools access) at desktop, tablet and phone viewports,
with touch and mobile emulation. A scenario can open several independent operators at once for multi‑user tests.

**Scenario engine.** Each scenario declares a starting state and steps, then changes conditions through the
starter kit's own control API — inject faults (take a data source offline, freeze video, drop or delay
telemetry), emulate a phone, or open a second user.

**Probes — what the user sees.** Read only the operator's view, by meaning: the UI text anchored to test ids;
the `<video>` element's decoded‑frame counter and a perceptual frame hash; the live Cesium map entities (found
by walking the page's React fiber to the running viewer) for exact marker positions; and layout geometry
(bounding boxes, `elementFromPoint` occlusion, scrollability) for “is this control actually reachable?”.

**Ground truth.** The true drone status and position, the backend health, and the active fault list come from the
control API and a socket feed that subscribes exactly like the cockpit — never from the UI under test.

**Oracles & triage.** Deterministic checks compare the two sides (UI vs truth, is the “live” claim honest, is a
control reachable, do labels overlap, is a privileged action reachable without sign‑in). Verdicts are
deterministic; symptoms of one root cause are grouped into a single issue; and precision guards prevent false
positives — for example a control is scrolled into its own container before it can be called “unreachable.”

**Evidence.** Every run records each operator in real time with the HUD narration, plus screenshots, timed
samples and a machine‑readable `result.json`. The recording *is* the reproducible evidence.

```
Operators (real Chrome: desktop/tablet/phone, multi-user)
   └─ Scenario engine ── control API ─▶ changing conditions (faults, devices)
        └─ Probes (UI by meaning · video frames+hash · Cesium via React fiber · layout geometry)
             ▲                    │
             │  ground truth      ▼
        Simulator state · backend health · active faults · socket feed
             └─ Oracles (UI vs truth · freshness honesty · reachability · occlusion · overlap · auth)
                  └─ Triage (deterministic verdicts · root-cause grouping · precision guards)
                       └─ Evidence (HUD-narrated video · screenshots · samples · result.json)
```

---

## 2. Scenarios

Five scenarios: four genuine issues and one deliberate PASS that shows the system does not raise false alarms
(precision is a scored criterion). Each is evaluated on its own.

### 1. A signed‑out visitor can command drones — no authentication  ·  **BUG**  ·  security
**Video:** combined recording at **0:00**
**Description.** Only authorised incident staff should be able to fly or land drones. The user expects any
flight command to require sign‑in.
**Approach.** Argus opens a fresh browser with no session, follows the cockpit's own “Control panel” link, and
presses **Take off** on Drone 3. The simulator confirms the drone actually took off. Argus then reads the
control API's CORS policy from a normal response header (read‑only, no cross‑site action) and reports that flight
commands require no auth token and the API trusts any origin (`Access‑Control‑Allow‑Origin: *`).
**Result.** A visitor with no sign‑in commanded a real drone. Two symptoms — no sign‑in, and an open
cross‑origin policy — are grouped into one issue. Maps to the brief's example *“let a signed‑out user open a page
that should need sign‑in.”*

### 2. A drone is shown active while its data source is offline  ·  **BUG**  ·  telemetry / freshness
**Video:** combined recording at **0:14**
**Description.** When the telemetry feed is lost, delayed, or its source goes offline, the cockpit must say so —
not keep presenting old data as current. The user expects to know whether what they see is live.
**Approach.** Argus flies Drone 1 and proves the cockpit updates live, then injects three conditions —
telemetry dropped, telemetry delayed 6 s, and the simulator taken offline. Every two seconds it compares the
on‑screen distance, status and link badge with the simulator's true position and the backend health.
**Result.** With the feed cut, the distance froze while the drone actually moved +105 m; with the simulator
offline, Drone 1 was still shown “in_flight” under a “socket connected” badge — no stale or offline indication.
Three symptoms, one root cause (no per‑device freshness state). Maps to *“show a drone as online when it is
offline.”*

### 3. Drone and dock labels overlap on the map  ·  **BUG**  ·  map / visual
**Video:** combined recording at **1:22**
**Description.** The operator reads each drone's status from its label on the map. Labels must be legible.
**Approach.** Argus reads the map's real label entities from the running Cesium viewer (text, on‑screen position,
font) and measures the overlap between label boxes.
**Result.** Each drone's status label overlaps its dock label (same position), and the altitude labels overlap —
e.g. `‘560 m ASL’` sits on top of `‘0 m’` (100% overlap of the smaller label), and `‘Dock 1’` overlaps
`‘Drone 1 standby’`. The status text is unreadable at the default view. This is a visual‑quality failure detected
from real geometry, not a screenshot guess.

### 4. On a phone, the video tile covers the map's 2D/3D switch  ·  **BUG**  ·  responsive UI
**Video:** combined recording at **1:36**
**Description.** An operator on a phone switches the map to 2D. The control must be usable at phone width.
**Approach.** Argus opens the cockpit at 390×844 with touch, finds the 2D button, checks which element is
actually on top at the button's centre, taps it with a real touch, and verifies whether the map mode changed.
**Result.** The 2D button's centre is covered by the video tile; a real tap lands on the video and the map mode
does not change. The primary control is present but non‑functional on a phone. Maps to *“push the main action
off screen at phone width.”*

### 5. On a phone, telemetry is reachable — no false alarm  ·  **PASS**  ·  responsive UI (precision)
**Video:** combined recording at **1:47**
**Description.** A field operator on a phone needs the selected drone's battery, altitude and speed. These are
below the fold on a phone.
**Approach.** Argus opens the cockpit at phone width, locates every telemetry field by meaning, scrolls each into
its own scroll container, and checks whether it becomes visible and is not covered.
**Result.** Every field is reachable by scrolling the panel — so Argus reports **PASS**, not a bug. This shows the
system distinguishes “below the fold but reachable” from “truly unreachable,” avoiding a false positive.

---

## How to reproduce
1. Start the starter kit: `docker compose up --build` (cockpit on `http://localhost:4010`, control API on
   `http://localhost:4000/api`).
2. Run a scenario: `argus live run --only S9` (or `--only S1 / S5 / S4 / S3`), or the whole set with
   `argus live run`. A real Chrome opens; each run records `runs/live/<id>/video.mp4`, screenshots and
   `result.json`.
