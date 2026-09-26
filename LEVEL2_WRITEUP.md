# Argus Live — Level 2: Live Data, Map, Video & Performance

**Team:** solo · **Product under test:** the FlytBase cockpit (starter kit) — a live drone‑operations dashboard
with a Cesium 3D map, telemetry panel and WebRTC video, driven by a simulator and a control API.

**One‑line:** Level 2 goes past “does the screen look right” to “is what the screen shows actually *true*, over a
whole workflow.” Argus reads real values from the live product — a drone's latitude/longitude off the 3D map, the
video's decoded frame rate, the movement trail's geometry — and compares them against the simulator's ground
truth, so it catches wrong or stale live data with hard evidence and near‑zero false positives.

**Video (all Level‑2 scenarios, real time):** _[paste your Google Drive link here]_
One recording introduces each scenario with a title card and **voice narration**, then runs it in real time. A HUD
overlay shows, at every moment, what the UI claims next to the ground truth and the verdict; for the stale‑map bug
the true‑vs‑shown position gap is drawn straight onto the live map. Turn your sound on.

---

## 1. System design (Level‑2 focus)

The full system is described in the Level‑1 write‑up. Level 2 exercises three deeper probes, each read from the
*live running product* and checked against independent ground truth:

- **Geospatial probe.** Argus reaches the running Cesium viewer through the page's React fiber and reads each
  drone marker's real position, converting it to latitude/longitude with the map's own WGS‑84 ellipsoid. It does
  the same for the movement‑trail polyline. Ground truth is the simulator's true position from the control API;
  the gap is measured in metres by the haversine formula. This is exact geometry, not a screenshot guess.
- **Video probe.** Liveness comes from the `<video>` element's decoded‑frame counter and a perceptual frame hash,
  so a frozen or wrong‑source stream is caught even when the label says “live”.
- **Performance probe.** Chrome DevTools timing — the Long Tasks API, click‑to‑paint latency and JS‑heap size —
  measured at rest and under a sustained multi‑drone load.

Verdicts are deterministic; symptoms of one root cause are grouped; and a scenario that behaves correctly is
reported as **PASS**, which is how the system demonstrates precision.

### 1.1 Evidence layer (new)

A finding is only useful if a human can trust it in seconds. Argus now makes every judgement visible *on the
product itself*, not just in a log:

- **On‑map gap overlay.** For the stale‑map bug, Argus draws the drone's *true* position (green ring) and the
  position the map is actually *showing* (amber) directly onto the live Cesium canvas, joined by a dashed line
  labelled with the live metre gap. The discrepancy is visible on the real map, in real time — the overlay is
  computed from the same coordinates the verdict uses, so what you see is the evidence.
- **Burned‑in evidence HUD.** Every recording carries an overlay that shows, tick by tick, the measurement, what
  the UI *claims*, the independent *ground truth*, and the running *verdict*. A reviewer can audit the decision
  frame by frame without trusting a summary.
- **Narrated walkthrough.** The combined recording opens each scenario with a title card and a voice track that
  states the oracle and the finding, so the evidence reads on its own, without a live presenter.

None of this touches the verdict logic — it is a presentation layer over the same deterministic measurements.

---

## 2. Scenarios

Five scenarios: two genuine issues and three deliberate passes that prove the system reads the live product
correctly and does not raise false alarms. Each is evaluated on its own.

### 1. The map shows an old drone position as current  ·  **BUG**  ·  map & geospatial
**Video:** combined recording, Scenario 1
**Description.** An operator reads a drone's location from the 3D map to direct ground crews. When telemetry is
delayed, the map must not present a stale position as if it were live.
**Approach.** Argus flies Drone 1, reads the marker's real latitude/longitude off the live Cesium map and confirms
it matches the simulator's true position to within a metre (baseline). It then delays telemetry by 8 seconds and
measures, every 2 seconds, how far the displayed marker falls behind the drone's real position — and whether the
UI warns of the delay.
**Result.** The marker drifted to **110 m** behind the true position while still shown as current, with no
stale/delayed indicator anywhere. An operator sending a crew to the displayed location would be 110 m off.
Maps to the brief's example *“an old position looks current.”* High impact, exact evidence (GPS coordinates read
from the map vs the simulator).

### 2. Frozen drone video still labelled “live”  ·  **BUG**  ·  video & media
**Video:** combined recording, Scenario 2
**Description.** When a drone's stream freezes, the operator must not be told the picture is live.
**Approach.** Argus measures decoded frames per second on the real `<video>` element and reads the tile's label
every second after freezing Drone 1's stream, and records how long a frozen picture keeps the “live” label.
**Result.** The tile reported **0 frames/second for 5 seconds while still labelled “live.”** Maps to *“a dropped
stream still looks live.”*

### 3. The drone movement trail matches the real flight path  ·  **PASS**  ·  map · bonus: movement trails
**Video:** combined recording, Scenario 3
**Description.** The cockpit draws each drone's movement trail (a bonus incident‑response capability). The trail is
only useful if it records where the drone actually flew.
**Approach.** Argus samples the drone's true positions throughout a flight, reads the drawn trail polyline from the
live Cesium map, and checks that every true position lies on the trail and the trail ends at the drone.
**Result.** 49 trail points, **0 m** worst deviation from the real path, endpoint 10 m from the drone — **PASS.**
This shows Argus can verify a bonus capability's correctness, not just find faults.

### 4. Selecting a drone switches map, video and telemetry together  ·  **PASS**  ·  functional (cross‑part)
**Video:** combined recording, Scenario 4
**Description.** Selecting a drone must show *that* drone's state everywhere at once. The user expects the map
focus, the video feed and the telemetry to all refer to the selected drone.
**Approach.** Argus selects Drone 2 and checks three things together: the video frame changes to a distinct feed
(perceptual hash), the telemetry battery matches Drone 2's true battery, and the label follows the selection.
**Result.** All three switched consistently — **PASS.** Demonstrates connecting behaviour across related parts of
the product (a scored criterion) with no false positive.

### 5. Responsiveness with 16 flying drones over ~10 simulated minutes  ·  **PASS**  ·  performance / long‑running
**Video:** combined recording, Scenario 5
**Description.** The cockpit must stay usable when many drones fly and data streams for a long time.
**Approach.** Argus measures frame timing, Long‑Task blocking, click‑to‑paint latency and JS heap at rest, then
adds 12 drones, flies all 16 at 5× simulator speed and re‑measures across the run.
**Result.** Click‑to‑paint stayed 28–123 ms (well under 250 ms), no long‑task stalls, and the JS heap sawtoothed
(garbage‑collected, no leak) — **PASS.** A precision result that proves the product scales, measured, not assumed.

---

## 3. Bonus‑capability coverage

The incident‑response product may add bonus capabilities. Argus's ground‑truth approach extends to them directly,
and one is already demonstrated above:

| Bonus capability | How Argus tests it |
|---|---|
| **Movement trails** (shown, Scenario 3) | Read the drawn trail polyline from the live map; verify it matches the drone's true flown path within tolerance. |
| **Alerts & notifications** | Drive a real condition (low battery, disconnect) via the control API and assert the correct alert appears — and clears — instead of firing spuriously. |
| **Shared map drawings, markers & observations** | Read placed markers/shapes from the Cesium viewer; verify their coordinates and that they replicate to a second operator. |
| **Synchronized replay** | Compare the replay's positions/timeline against the recorded ground‑truth trajectory, frame for frame. |
| **Multiple video layouts** | Verify each tile shows a distinct, correctly‑labelled feed (perceptual hash per drone) across layouts. |
| **Search & filtering within history** | Assert filtered results match the ground‑truth event log (no missing, duplicated or misattributed events). |

Each reuses the same principle: read what the user sees, compare with independent truth, group by root cause.

---

## 4. Toward Level 3 — the JEV cost‑aware reasoning mesh

Levels 1–2 keep every verdict **deterministic**: a hard oracle (coordinates, decoded frame rate, click‑to‑paint
latency) decides pass or fail, which is exactly what gives Argus its near‑zero false‑positive rate. Level 3 —
autonomous, open‑ended, self‑directed testing — needs a layer that can *decide what to test* and *reason where no
numeric oracle exists*. That is the job of the **JEV mesh**, already wired into the codebase and deliberately kept
**out of the Level‑2 verdict path** so it cannot introduce false alarms.

**What the mesh is.** A cost‑aware *ladder* of models rather than one expensive model — cheap first, escalate only
when needed:

| Tier | Role | Cost |
|---|---|---|
| **JEV** (primary free key) | first pass on every reasoning call | free · quota‑limited |
| **JEV2** (second free key) | overflow once JEV's daily quota is spent | free · quota‑limited |
| **Groq / open‑weight** | high‑throughput fallback | free / very low |
| **Premium model** | escalation *only* when the cheap tiers are uncertain | paid · rarely hit |

A **circuit breaker** trips on quota exhaustion or repeated errors and routes down the ladder automatically, so the
agent never stalls — the “tireless” property. In practice the free tiers absorb the large majority of calls, and a
premium model is touched only when a cheap tier signals low confidence. This is what makes a *continuously‑running*
Level‑3 agent economically viable instead of prohibitively expensive.

**What the mesh unlocks at Level 3** — each capability still *grounded on the Level‑2 probes*:

- **Autonomous exploration & scenario synthesis.** The agent crawls the running product; the LLM proposes
  product‑specific scenarios (“there is a geofence editor — verify a drone crossing the fence raises an alert”)
  instead of running a fixed list. The deterministic probes still decide the verdict; the LLM only chooses *what*
  to try, so autonomy never costs precision.
- **Self‑healing steps.** When a selector or step breaks after a UI change, the mesh re‑derives the target from the
  current DOM, the screenshot and the step's stated intent — tests survive refactors instead of going red.
- **Semantic assertions where truth is fuzzy.** For questions with no numeric oracle (“is this error message
  actually helpful?”, “does this layout look broken?”) the LLM acts as a judge — but its opinion is cross‑checked
  against the hard probes and is never allowed to override them.
- **Triage & root‑cause narratives.** The mesh clusters many raw findings into a few root causes and writes the
  human‑readable explanation, cutting noise for the reviewer.
- **Natural‑language authoring.** A developer writes “check that logging out clears the session everywhere” and the
  mesh compiles it into a runnable scenario over the existing probes.

**The invariant that carries across all three levels.** The LLM decides *what* to test and *explains* the result;
a deterministic oracle decides *whether* it passed. Level 3 adds open‑ended autonomy on top of Levels 1–2 **without
giving up their evidence‑first, near‑zero‑false‑positive guarantee.**

---

## How to reproduce
1. Start the starter kit: `docker compose up --build` (cockpit `http://localhost:4010`, control API `:4000/api`).
2. Run a scenario: `argus live run --only S11` (map stale), `--only S2` (video), `--only S12` (trail),
   `--only S8` (selection), `--only S10` (performance); or `argus live run` for all. Each run records
   `runs/live/<id>/video.mp4`, screenshots, timed samples and a machine‑readable `result.json`.
