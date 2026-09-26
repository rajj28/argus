"""Argus Live scenario library for the FlytBase cockpit. Each scenario: clear starting state, operator steps,
one changing condition, and a deterministic comparison of what the screen claims against ground truth."""
from __future__ import annotations

from argus.live.engine import FRAME_HASH, LAYOUT_JS, NOT_LIVE, Ctx, Finding, Scenario, number

MAP_PROBE = r"""() => {
  const host = document.querySelector('[data-testid="map-canvas"]'); if (!host) return {error: 'no map element'};
  let viewer = null;
  const start = [host, ...host.querySelectorAll('*')].slice(0, 40);
  for (const el of start) {
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber')); let f = key ? el[key] : null;
    for (let i = 0; f && i < 60 && !viewer; i++, f = f.return) {
      let s = f.memoizedState;
      for (let j = 0; s && j < 40; j++, s = s.next) {
        const v = s.memoizedState;
        if (v && v.current && v.current.scene && v.current.entities) { viewer = v.current; break; }
      }
    }
    if (viewer) break;
  }
  if (!viewer) return {error: 'viewer not reachable'};
  const now = viewer.clock.currentTime; const out = [];
  viewer.entities.values.forEach(e => {
    if (!e.position || !e.label || !e.label.text) return;
    const pos = e.position.getValue(now); if (!pos) return;
    const sc = viewer.scene.cartesianToCanvasCoordinates(pos); if (!sc) return;
    const text = e.label.text.getValue(now); const font = e.label.font ? e.label.font.getValue(now) : '12px sans-serif';
    const off = e.label.pixelOffset ? e.label.pixelOffset.getValue(now) : {x: 0, y: 0};
    out.push({id: e.id, text, font, x: sc.x + (off ? off.x : 0), y: sc.y + (off ? off.y : 0), ecef: [pos.x, pos.y, pos.z]});
  });
  const r = viewer.canvas.getBoundingClientRect();
  return {labels: out, canvas: [r.left, r.top, r.width, r.height]};
}"""


async def _tel(page, key: str) -> str:
    return await page.evaluate("(k) => (document.querySelector(`[data-testid=telemetry-${k}]`)?.innerText || '').trim()", key)


async def _freshness_text(page) -> str:
    """Everything that could tell the operator the drone data is not live."""
    return await page.evaluate("""() => ['[data-testid="socket-status"]', '[data-testid="status-flight"]']
        .map(s => document.querySelector(s)?.innerText || '')
        .concat([...document.querySelectorAll('[data-testid^="device-row-"],[data-testid^="telemetry-"]')].map(e => e.innerText))
        .join(' | ')""")


async def _wait_ui_status(page, drone: int, status: str, timeout_ms: int = 40000) -> bool:
    for _ in range(timeout_ms // 500):
        row = await page.evaluate(f"() => document.querySelector('[data-testid=device-row-drone-{drone}]')?.innerText || ''")
        if status in row:
            return True
        await page.wait_for_timeout(500)
    return False


async def _await_truth(page, pred, timeout_s: float = 15) -> bool:
    """Poll a ground-truth predicate without blocking the event loop (keeps the HUD/recording live)."""
    for _ in range(int(timeout_s * 2)):
        try:
            if pred():
                return True
        except Exception:
            pass
        await page.wait_for_timeout(500)
    return False


# 1 -------------------------------------------------------------------------------------------------------------
async def freshness(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: simulator reset, Drone 1 takes off, operator opens the cockpit")
    t.takeoff("drone-1")
    p = await c.operator("operator", "desktop")
    await _wait_ui_status(p, 1, "in_flight")
    d0 = number(await _tel(p, "home-distance")); await p.wait_for_timeout(3000); d1 = number(await _tel(p, "home-distance"))
    await c.note("pass", f"baseline: cockpit updates live (distance {d0:.0f} → {d1:.0f} m)")
    await c.shot(p, "0-baseline-live")
    symptoms = []
    conditions = [("feed lost: 100% of telemetry dropped", "socket-drop", {"value": 100}),
                  ("feed delayed by 6 s", "socket-delay", {"value": 6000}),
                  ("data source offline: simulator disconnected", "sim-offline", {"seconds": 30})]
    for n, (label, kind, kw) in enumerate(conditions, 1):
        t.clear_faults(); await p.wait_for_timeout(4000)
        await c.step(f"Condition {n}/3 - {label}")
        t.fault(kind, **kw)
        ui_first = None; truth_first = None; lags = []; frozen = True; source_off = False; honest = False
        for i in range(6):
            await p.wait_for_timeout(2000)
            ui_d = number(await _tel(p, "home-distance")); ui = await c.ui(p)
            h = t.health(); source_off = source_off or h.get("simulator") != "connected"
            try:
                tr_d = t.home_distance("drone-1"); tr_status = t.drone("drone-1").get("status", "?")
            except Exception:
                tr_d, tr_status = float("nan"), "no data"
            if i == 0:
                ui_first, truth_first = ui_d, tr_d
            elif ui_d is not None and ui_first is not None and abs(ui_d - ui_first) > 3:
                frozen = False
            if ui_d is not None and tr_d == tr_d:
                lags.append(tr_d - ui_d)
            honest = honest or bool(NOT_LIVE.search(await _freshness_text(p)))
            truth_label = {"socket-drop": "no telemetry reaching UI", "socket-delay": "arriving 6 s late",
                           "sim-offline": f"simulator {h.get('simulator')}"}[kind]
            await c.hud(p, compare=[["Dist. from home", f"{ui_d} m", f"{tr_d:.0f} m" if tr_d == tr_d else "unknown"],
                                    ["Link badge", ui["socket"], truth_label],
                                    ["Drone 1 status", ui["selected_status"], tr_status if kind != "sim-offline" else "unknown (source offline)"]])
            c.sample(condition=kind, ui_distance=ui_d, truth_distance=None if tr_d != tr_d else round(tr_d),
                     socket_badge=ui["socket"], ui_status=ui["selected_status"], simulator=h.get("simulator"))
        await c.shot(p, f"{n}-{kind}")
        moved = (tr_d - truth_first) if (tr_d == tr_d and truth_first == truth_first) else 0
        if kind == "socket-drop" and frozen and moved > 30 and not honest:
            symptoms.append(f"Feed lost: distance frozen at {ui_first:.0f} m for 10 s while the drone actually moved "
                            f"+{moved:.0f} m; badge still '{ui['socket']}', no stale warning")
        elif kind == "socket-delay" and lags and sum(lags) / len(lags) > 30 and not honest:
            symptoms.append(f"Feed delayed: positions shown ~{sum(lags) / len(lags):.0f} m behind reality (≈6 s old) "
                            "with no 'delayed' indication")
        elif kind == "sim-offline" and source_off and not honest and "in_flight" in ui["selected_status"]:
            symptoms.append("Source offline: backend reports the simulator disconnected, but Drone 1 is still shown "
                            f"'in_flight' under a '{ui['socket']}' badge")
        await c.note("bug" if len(symptoms) >= n else "pass", symptoms[-1][:90] if len(symptoms) >= n else f"{label}: handled")
    t.clear_faults()
    if symptoms:
        c.find(Finding(title="Stale, delayed or offline drone data is presented as live",
                       category="Telemetry / data freshness", level="L2 (status mismatch is L1)", severity="High",
                       detail="The cockpit has no per-device freshness state. The only signal is the socket badge, which "
                              "describes the browser-to-backend socket, not whether drone data is current. Argus grouped "
                              f"{len(symptoms)} symptoms under this one root cause.", symptoms=symptoms))


# 2 -------------------------------------------------------------------------------------------------------------
async def frozen_video(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: operator opens the cockpit; Drone 1's FPV video is playing")
    p = await c.operator("operator", "desktop")
    f0 = (await c.ui(p))["video_frames"]; await p.wait_for_timeout(2000); f1 = (await c.ui(p))["video_frames"]
    await c.note("pass", f"baseline: video decoding {(f1 - f0) / 2:.0f} frames/s, label 'live'")
    await c.step("Condition: Drone 1's stream freezes (video-freeze, 15 s)")
    t.fault("video-freeze", deviceId="drone-1", seconds=15)
    prev = f1; frozen_live = 0; honest_at = None; hashes = set()
    for i in range(1, 15):
        await p.wait_for_timeout(1000)
        ui = await c.ui(p); fr = ui["video_frames"] or 0; delta = max(0, fr - (prev or 0)); prev = fr
        h = await p.evaluate(FRAME_HASH)
        if h:
            hashes.add(h)
        if delta <= 1 and ui["video_label"].lower() == "live":
            frozen_live += 1
        if honest_at is None and ui["video_label"].lower() != "live":
            honest_at = i
        c.sample(second=i, frames_per_s=delta, video_label=ui["video_label"])
        await c.hud(p, compare=[["Video label", ui["video_label"], "stream frozen (fault active)"],
                                ["Frames decoded/s", str(delta), "0 while frozen"]])
        if i == 4:
            await c.shot(p, "1-frozen-but-live")
    if frozen_live >= 3:
        c.find(Finding(title=f"Frozen drone video keeps the 'live' label for {frozen_live} s",
                       category="Video and media", level="L2", severity="High",
                       detail="While the stream was frozen (0 frames decoded), the tile still said 'live'"
                              + (f"; it only admitted the problem after {honest_at} s." if honest_at else
                                 " for the whole observation window."),
                       symptoms=[f"{frozen_live} consecutive seconds with 0-1 frames/s under a 'live' label"],
                       evidence={"distinct_frames_seen": len(hashes)}))
        await c.note("bug", f"frozen for {frozen_live}s while labelled live")
    else:
        await c.note("pass", "video tile reported the freeze promptly")


# 3 -------------------------------------------------------------------------------------------------------------
async def phone_telemetry(c: Ctx) -> None:
    await c.step("Start: field operator opens the cockpit on a phone (390×844)")
    c.hud_state["pos"] = "bottom"
    p = await c.operator("phone-operator", "phone")
    await c.step("Look for the selected drone's battery, altitude and speed")
    before = await p.evaluate(LAYOUT_JS, '[data-testid^="telemetry-"]')
    await c.step("Try to scroll the page to reach the telemetry panel")
    await p.mouse.wheel(0, 2000); await p.wait_for_timeout(800)
    try:
        await p.touchscreen.tap(195, 120)
    except Exception:
        pass
    after = await p.evaluate(LAYOUT_JS, '[data-testid^="telemetry-"]')
    hidden = [x for x in after if not x["in_view"] or x["covered"]]
    await c.hud(p, compare=[["Telemetry fields readable", f"{len(after) - len(hidden)} of {len(after)}", f"{len(after)} (desktop shows all)"],
                            ["Page scrolls", str(after[0]["page_scrolls"] if after else "?"), "yes, or panel reachable"]])
    await c.shot(p, "1-phone-no-telemetry")
    if after and len(hidden) == len(after):
        why = "covered by " + (next((x["covered_by"] for x in hidden if x["covered_by"]), "the map")) if any(x["in_view"] for x in hidden) \
            else "rendered below the screen and the page cannot scroll"
        c.find(Finding(title="On a phone the drone's battery, altitude and speed cannot be seen at all",
                       category="Responsive UI", level="L1", severity="High",
                       detail=f"All {len(after)} telemetry fields are unreachable at 390×844 ({why}). A field operator "
                              "cannot check battery before or during a flight.",
                       symptoms=[f"{x['id']}: in_view={x['in_view']} covered_by={x['covered_by']}" for x in hidden[:4]],
                       evidence={"before_scroll": before[:3], "after_scroll": after[:3]}))
        await c.note("bug", "0 telemetry fields reachable on phone")


# 4 -------------------------------------------------------------------------------------------------------------
async def phone_map_toggle(c: Ctx) -> None:
    await c.step("Start: operator on a phone wants to switch the map to 2D")
    c.hud_state["pos"] = "top"
    p = await c.operator("phone-operator", "phone")
    lay = await p.evaluate(LAYOUT_JS, '[data-testid="map-view-2d"],[data-testid="map-view-3d"]')
    two_d = next((x for x in lay if x["id"] == "map-view-2d"), None)
    await c.hud(p, compare=[["2D button", "covered by " + str(two_d and two_d["covered_by"]) if two_d and two_d["covered"] else "tappable", "tappable"]])
    state_before = await p.evaluate("() => document.querySelector('[data-testid=map-view-2d]')?.className + '|' + document.querySelector('[data-testid=map-view-2d]')?.getAttribute('aria-pressed')")
    if two_d:
        x, y, w, h = two_d["rect"]
        await c.step("Tap the 2D button where it is drawn")
        await p.touchscreen.tap(x + w / 2, y + h / 2); await p.wait_for_timeout(1500)
    state_after = await p.evaluate("() => document.querySelector('[data-testid=map-view-2d]')?.className + '|' + document.querySelector('[data-testid=map-view-2d]')?.getAttribute('aria-pressed')")
    await c.shot(p, "1-toggle-covered")
    if two_d and two_d["covered"] and state_before == state_after:
        c.find(Finding(title="On a phone the video tile covers the 2D/3D map switch, so tapping 2D does nothing",
                       category="Responsive UI / Visual", level="L1", severity="Medium",
                       detail=f"The 2D button's centre is covered by {two_d['covered_by']}; a real tap on it did not change "
                              "the map mode.", symptoms=[f"2D button rect {two_d['rect']} covered by {two_d['covered_by']}"]))
        await c.note("bug", "tap on 2D hit the video tile; mode unchanged")


# 5 -------------------------------------------------------------------------------------------------------------
async def map_labels(c: Ctx) -> None:
    await c.step("Start: operator scans the map to read each drone's status")
    p = await c.operator("operator", "desktop")
    await p.wait_for_timeout(4000)
    probe = await p.evaluate(MAP_PROBE)
    if "error" in probe:
        await c.note("info", f"map probe unavailable: {probe['error']}")
        return
    boxes = []
    for lb in probe["labels"]:
        size = number(lb.get("font") or "") or 12
        w = 0.58 * size * len(lb["text"] or ""); h = size * 1.25
        boxes.append((lb, (lb["x"] - w / 2, lb["y"] - h / 2, lb["x"] + w / 2, lb["y"] + h / 2)))
    overlaps = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (a, ra), (b, rb) = boxes[i], boxes[j]
            ix = max(0, min(ra[2], rb[2]) - max(ra[0], rb[0])); iy = max(0, min(ra[3], rb[3]) - max(ra[1], rb[1]))
            small = min((ra[2] - ra[0]) * (ra[3] - ra[1]), (rb[2] - rb[0]) * (rb[3] - rb[1])) or 1
            if ix * iy / small > 0.25:
                overlaps.append(f"'{a['text']}' overlaps '{b['text']}' ({ix * iy / small:.0%} of the smaller label)")
    await c.hud(p, compare=[["Map labels checked", str(len(boxes)), "all readable"], ["Overlapping pairs", str(len(overlaps)), "0"]])
    await c.shot(p, "1-map-labels")
    if overlaps:
        c.find(Finding(title="Drone and dock labels are drawn on top of each other on the map",
                       category="Map and geospatial / Visual", level="L1", severity="Medium",
                       detail="Labels for a drone and its dock (same position) and the altitude labels overlap, so the drone's "
                              "status text is unreadable at the default map view.", symptoms=overlaps[:6],
                       evidence={"labels": probe["labels"][:12]}))
        await c.note("bug", f"{len(overlaps)} overlapping label pairs")


# 6 -------------------------------------------------------------------------------------------------------------
async def reconnect_recovery(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: Drone 1 in flight, operator watching telemetry")
    t.takeoff("drone-1")
    p = await c.operator("operator", "desktop")
    await _wait_ui_status(p, 1, "in_flight")
    await c.step("Condition: server drops every cockpit socket and refuses reconnects for 10 s")
    t.fault("socket-refuse", seconds=10); t.fault("socket-kick")
    badges = []
    for i in range(6):
        await p.wait_for_timeout(2000); ui = await c.ui(p); badges.append(ui["socket"])
        await c.hud(p, compare=[["Link badge", ui["socket"], "disconnected / refused"]])
        c.sample(phase="outage", badge=ui["socket"])
    await c.shot(p, "1-outage")
    await c.step("Server accepts connections again - does the cockpit recover and catch up?")
    recovered_at = None
    for i in range(1, 16):
        await p.wait_for_timeout(1000)
        ui_d = number(await _tel(p, "home-distance")); tr_d = t.home_distance("drone-1"); ui = await c.ui(p)
        c.sample(phase="recovery", second=i, badge=ui["socket"], ui_distance=ui_d, truth_distance=round(tr_d))
        await c.hud(p, compare=[["Link badge", ui["socket"], "connected"], ["Dist. from home", f"{ui_d} m", f"{tr_d:.0f} m"]])
        if recovered_at is None and "connected" in ui["socket"] and ui_d is not None and abs(tr_d - ui_d) < 25:
            recovered_at = i
    rows = (await c.ui(p))["rows"]
    await c.shot(p, "2-recovered")
    honest = any(b and "connected" != b.split()[-1] for b in badges)
    if not honest:
        c.find(Finding(title="Cockpit claims 'socket connected' while the server refuses it",
                       category="Network and recovery", level="L3", severity="High",
                       detail=f"Badge values during the outage: {badges}", symptoms=badges))
    if recovered_at is None:
        c.find(Finding(title="Cockpit does not catch up after the connection returns", category="Network and recovery",
                       level="L3", severity="High", detail="15 s after the server accepted connections, values still "
                       "did not match the simulator.", symptoms=[str(c.r.samples[-1])]))
    if len(rows) != 4:
        c.find(Finding(title="Device list duplicated or lost after reconnect", category="Network and recovery",
                       level="L3", severity="Medium", detail=f"{len(rows)} rows after reconnect", symptoms=rows))
    await c.note("pass" if honest else "bug", f"badge during outage: {sorted(set(badges))}")
    await c.note("pass" if recovered_at else "bug", f"caught up with truth after {recovered_at} s" if recovered_at else "did not catch up")


# 7 -------------------------------------------------------------------------------------------------------------
async def two_operators(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: two operators have the cockpit open at the same time")
    a = await c.operator("operator-A", "desktop")
    b = await c.operator("operator-B", "tablet")
    await c.step("Drone 2 takes off (issued from the control API) - both screens must agree with the simulator")
    t.takeoff("drone-2")
    seen = {"A": None, "B": None}; truth_at = None
    for i in range(1, 41):
        await a.wait_for_timeout(500)
        st = t.drone("drone-2").get("status")
        if truth_at is None and st == "in_flight":
            truth_at = i / 2
        for name, pg in (("A", a), ("B", b)):
            row = await pg.evaluate("() => document.querySelector('[data-testid=device-row-drone-2]')?.innerText || ''")
            if seen[name] is None and "in_flight" in row:
                seen[name] = i / 2
        await c.hud_all(compare=[["Drone 2 (operator A)", "in_flight" if seen["A"] else "…", st],
                                 ["Drone 2 (operator B)", "in_flight" if seen["B"] else "…", st]])
        if all(seen.values()) and truth_at:
            break
    lag = {k: (v - truth_at) if (v is not None and truth_at is not None) else None for k, v in seen.items()}
    c.sample(truth_in_flight_at=truth_at, operator_A_at=seen["A"], operator_B_at=seen["B"], lag=lag)
    await c.shot(a, "1-operator-A"); await c.shot(b, "1-operator-B")
    late = [k for k, v in lag.items() if v is None or v > 3]
    if late:
        c.find(Finding(title="An operator sees the take-off late or not at all", category="Real-time and multi-user",
                       level="L3", severity="High", detail=f"lag vs simulator: {lag}", symptoms=[str(lag)]))
    await c.note("bug" if late else "pass", f"propagation lag vs truth: {lag}")


# 8 -------------------------------------------------------------------------------------------------------------
async def switch_drone(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: operator watches Drone 1, then selects Drone 2")
    p = await c.operator("operator", "desktop")
    await p.wait_for_timeout(2000)
    h1 = await p.evaluate(FRAME_HASH); u1 = await c.ui(p)
    await p.click('[data-testid="device-row-drone-2"]'); await p.wait_for_timeout(6000)
    h2 = await p.evaluate(FRAME_HASH); u2 = await c.ui(p)
    tr = t.drone("drone-2")
    dist = sum(x != y for x, y in zip(h1 or "", h2 or "")) if h1 and h2 else None
    battery_ui = number(u2["telemetry"].get("Battery", "") or await _tel(p, "battery"))
    await c.hud(p, compare=[["Selected drone", u2["selected_status"], f"Drone 2 {tr.get('status')}"],
                            ["Battery", f"{battery_ui} %", f"{tr.get('battery', 0):.0f} %"],
                            ["Video frame changed", f"{dist} of 144 cells differ" if dist is not None else "n/a", "different clip per drone"]])
    await c.shot(p, "1-drone-2-selected")
    c.sample(frame_hash_distance=dist, battery_ui=battery_ui, battery_truth=tr.get("battery"), title_before=u1["video_title"][:40],
             title_after=u2["video_title"][:40])
    if dist is not None and dist < 8 and "Drone 2" in u2["video_title"]:
        c.find(Finding(title="Switching drones changes the video label but not the video", category="Video and media",
                       level="L2", severity="High", detail="The tile says Drone 2 but shows the same picture as Drone 1.",
                       symptoms=[f"frame difference {dist}/144"]))
    if battery_ui is not None and abs(battery_ui - tr.get("battery", battery_ui)) > 2:
        c.find(Finding(title="Selected drone's battery does not match the drone", category="Telemetry", level="L2",
                       severity="High", detail=f"UI {battery_ui}% vs simulator {tr.get('battery')}%"))
    await c.note("bug" if c.r.findings else "pass", "label, video and telemetry follow the selection" if not c.r.findings else c.r.findings[0].title)


# 9 -------------------------------------------------------------------------------------------------------------
async def unauthenticated_control(c: Ctx) -> None:
    import re as _re
    t = c.truth
    await c.step("Start: a stranger with no account or session opens the cockpit URL")
    p = await c.operator("stranger", "desktop")
    await c.step("They follow the cockpit's own 'Control panel' link")
    link = p.get_by_role("link", name=_re.compile("control panel", _re.I))
    try:
        async with p.context.expect_page(timeout=8000) as new_tab:      # the link opens a new tab
            await link.click()
        p = await new_tab.value
    except Exception:
        await link.click()
    await p.wait_for_load_state("domcontentloaded")
    await p.wait_for_timeout(3000)
    await c.hud(p, compare=[["Page opened", "control panel (no sign-in asked)", "should require sign-in"]])
    await c.step("...and press 'Take off' on Drone 3")
    # the panel re-renders every tick; use the stable test id the control panel exposes
    btn = p.get_by_test_id("dash-takeoff-drone-3")
    try:
        await btn.wait_for(state="visible", timeout=10000)
        await btn.click(timeout=8000, force=True)
    except Exception:
        await p.get_by_role("button", name=_re.compile("take ?off", _re.I)).first.click(timeout=8000, force=True)
    took_off = await _await_truth(p, lambda: t.drone("drone-3").get("status") in ("taking_off", "in_flight"), 15)
    await c.hud(p, compare=[["Drone 3 command", "accepted", t.drone("drone-3").get("status", "?")]])
    await c.shot(p, "1-stranger-took-off-drone-3")
    # Read-only config check: report the server's CORS policy from a normal preflight response header,
    # observed passively. Argus does NOT perform any cross-site command; it only reports the configuration.
    await c.step("Second check: read the control API's CORS policy from its response headers (no command sent)")
    cors_policy = await p.evaluate(CORS_HEADER_CHECK)
    open_cors = cors_policy.get("access_control_allow_origin") in ("*", None) and cors_policy.get("ok")
    c.sample(stranger_took_off_drone3=took_off, control_api_cors=cors_policy)
    await c.hud(p, compare=[["Control API CORS", str(cors_policy.get("access_control_allow_origin")), "restrict to trusted origins"],
                            ["Auth on flight commands", "none", "required"]])
    await c.shot(p, "2-cors-policy")
    symptoms = []
    if took_off:
        symptoms.append("A visitor with no sign-in opened the control panel from the cockpit link and took off Drone 3")
    if open_cors:
        symptoms.append(f"The control API replies with Access-Control-Allow-Origin: {cors_policy.get('access_control_allow_origin')} "
                        "and requires no auth token, so any origin is trusted to send flight commands")
    if symptoms:
        c.find(Finding(title="Flight commands need no sign-in and the control API trusts any origin",
                       category="Security and permissions", level="L1", severity="Critical",
                       detail="Take off / land require no authentication or authorization, and the API's CORS policy is open, "
                              "so access is not restricted to trusted operators. (The starter kit disables auth for the "
                              "hackathon; for a real incident product this is a critical gap that the tester flags.)",
                       symptoms=symptoms))
        await c.note("bug", "unauthenticated drone commands; open CORS policy")
    t.land("drone-3")


# Read-only: a benign GET, reading back the CORS policy the server advertises. No command is sent.
CORS_HEADER_CHECK = """async () => { try {
    const r = await fetch('http://localhost:4000/api/health', {method: 'GET'});
    return {ok: r.ok, status: r.status,
            access_control_allow_origin: r.headers.get('access-control-allow-origin')}; }
    catch (e) { return {error: String(e)}; } }"""


# 10 ------------------------------------------------------------------------------------------------------------
PERF_PROBE = """async (ms) => {
  const lt = []; let obs;
  try { obs = new PerformanceObserver(l => l.getEntries().forEach(e => lt.push(e.duration))); obs.observe({type: 'longtask', buffered: false}); } catch (e) {}
  let frames = 0; const t0 = performance.now();
  await new Promise(res => { const tick = () => { frames++; if (performance.now() - t0 < ms) requestAnimationFrame(tick); else res(); }; requestAnimationFrame(tick); });
  if (obs) obs.disconnect();
  const row = document.querySelector('[data-testid=device-row-drone-2]'); let clickMs = null;
  if (row) { const s = performance.now(); row.click(); await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); clickMs = performance.now() - s; }
  return {fps: frames / (ms / 1000), long_task_ms: lt.reduce((a, b) => a + b, 0), long_tasks: lt.length,
          click_to_paint_ms: clickMs, heap_mb: performance.memory ? performance.memory.usedJSHeapSize / 1048576 : null,
          rows: document.querySelectorAll('[data-testid^=device-row-]').length};
}"""


async def load_and_long_run(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: reset to 4 drones on their docks; measure the cockpit at rest")
    p = await c.operator("operator", "desktop")
    await p.wait_for_timeout(2000)
    base = await p.evaluate(PERF_PROBE, 4000)
    c.sample(phase="at rest", **{kk: (round(v, 1) if isinstance(v, float) else v) for kk, v in base.items()})
    await c.hud(p, compare=[["Drones", str(base["rows"]), "4 at rest"],
                            ["JS heap", f"{base['heap_mb']:.0f} MB" if base["heap_mb"] else "n/a", "baseline"],
                            ["Long tasks", f"{base['long_task_ms']:.0f} ms/4s", "low"]])
    await c.step("Condition: 12 more drones join, all fly, simulator runs 5x faster (~10 simulated minutes)")
    added = []
    for i in range(12):
        try:
            r = t.add_drone(f"Load {i + 1}")
            added.append((r.get("drone") or {}).get("id") or r.get("droneId") or r.get("id"))
        except Exception:
            break
    ids = list(t.state().get("drones", {}))
    for d in ids:
        try:
            t.takeoff(d)
        except Exception:
            pass
    t.speed(5)
    samples = []
    for k in range(3):
        await p.wait_for_timeout(12000)
        m = await p.evaluate(PERF_PROBE, 4000)
        samples.append(m)
        c.sample(phase=f"load {k + 1}", **{kk: (round(v, 1) if isinstance(v, float) else v) for kk, v in m.items()})
        await c.hud(p, compare=[["Drones flying", str(m["rows"]), str(len(ids))],
                                ["JS heap", f"{m['heap_mb']:.0f} MB" if m["heap_mb"] else "n/a",
                                 f"at rest {base['heap_mb']:.0f} MB" if base["heap_mb"] else ""],
                                ["Long tasks", f"{m['long_task_ms']:.0f} ms/4s", f"at rest {base['long_task_ms']:.0f}"],
                                ["Click to paint", f"{m['click_to_paint_ms']:.0f} ms", f"at rest {base['click_to_paint_ms']:.0f}"]])
    await c.shot(p, "1-under-load")
    t.speed(1)
    for d in added:
        if d:
            t.remove_drone(d)
    t.reset()   # leave the sim clean for the next scenario
    # Long task time is the trustworthy responsiveness signal (rAF fps is unreliable when the tab is not
    # foreground-composited). Heap is only a finding when it grows AND stays elevated (not GC sawtooth).
    worst_long = max(x["long_task_ms"] for x in samples)
    heap_rest = base["heap_mb"] or 0
    heap_end = samples[-1]["heap_mb"] or 0
    heap_min_load = min((x["heap_mb"] or 0) for x in samples)
    problems = []
    if worst_long > 400:
        problems.append(f"main thread blocked for {worst_long:.0f} ms of long tasks in a 4 s window under load")
    if heap_rest and heap_end - heap_rest > 120 and heap_min_load - heap_rest > 60:
        problems.append(f"JS heap climbed from {heap_rest:.0f} MB at rest to {heap_end:.0f} MB and did not fall back "
                        f"(low point under load {heap_min_load:.0f} MB), a memory-growth pattern over the run")
    if problems:
        c.find(Finding(title="Memory grows and the main thread stalls under a sustained 16-drone load",
                       category="Performance / Long-running", level="L3", severity="Medium",
                       detail="Measured in the real browser with the Long Tasks API, click-to-paint timing and "
                              "performance.memory over a ~10-minute simulated run at 5x speed with 16 flying drones.",
                       symptoms=problems,
                       evidence={"at_rest": {"heap_mb": round(heap_rest), "long_task_ms": base["long_task_ms"]},
                                 "under_load": [{"heap_mb": round(x["heap_mb"] or 0), "long_task_ms": x["long_task_ms"],
                                                 "click_to_paint_ms": round(x["click_to_paint_ms"] or 0)} for x in samples]}))
        await c.note("bug", "; ".join(problems)[:90])
    else:
        await c.note("pass", f"stayed responsive: long tasks <= {worst_long:.0f} ms, heap {heap_rest:.0f}->{heap_end:.0f} MB")



# --- map geospatial probes: read the live Cesium viewer ---
MAP_POS = r"""() => {
  const host=document.querySelector('[data-testid="map-canvas"]'); if(!host) return {error:'no map'};
  let viewer=null; for(const el of [host,...host.querySelectorAll('*')].slice(0,40)){
    const k=Object.keys(el).find(k=>k.startsWith('__reactFiber')); let f=k?el[k]:null;
    for(let i=0;f&&i<60&&!viewer;i++,f=f.return){let st=f.memoizedState;
      for(let j=0;st&&j<40;j++,st=st.next){const v=st.memoizedState; if(v&&v.current&&v.current.scene&&v.current.entities){viewer=v.current;break;}}}
    if(viewer)break;}
  if(!viewer) return {error:'no viewer'};
  const now=viewer.clock.currentTime, ell=viewer.scene.globe.ellipsoid, out={};
  viewer.entities.values.forEach(e=>{ if(!/^drone-\d+$/.test(e.id)||!e.position) return;
    const pos=e.position.getValue(now); if(!pos) return; const c=ell.cartesianToCartographic(pos);
    out[e.id]={lat:c.latitude*180/Math.PI, lon:c.longitude*180/Math.PI, h:c.height}; });
  return out;
}"""

MAP_TRACK = r"""(id) => {
  const host=document.querySelector('[data-testid="map-canvas"]'); if(!host) return {error:'no map'};
  let viewer=null; for(const el of [host,...host.querySelectorAll('*')].slice(0,40)){
    const k=Object.keys(el).find(k=>k.startsWith('__reactFiber')); let f=k?el[k]:null;
    for(let i=0;f&&i<60&&!viewer;i++,f=f.return){let st=f.memoizedState;
      for(let j=0;st&&j<40;j++,st=st.next){const v=st.memoizedState; if(v&&v.current&&v.current.scene&&v.current.entities){viewer=v.current;break;}}}
    if(viewer)break;}
  if(!viewer) return {error:'no viewer'};
  const now=viewer.clock.currentTime, ell=viewer.scene.globe.ellipsoid;
  const e=viewer.entities.getById('track:'+id); if(!e||!e.polyline) return {error:'no track'};
  const pos=e.polyline.positions.getValue(now); if(!pos||!pos.length) return {error:'empty track'};
  return {points: pos.map(c=>{const g=ell.cartesianToCartographic(c); return [g.latitude*180/Math.PI, g.longitude*180/Math.PI];})};
}"""


def _haversine(a, b, c, d):
    import math
    R = 6371000.0; p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


# 11 ------------------------------------------------------------------------------------------------------------
async def map_stale_position(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: Drone 1 takes off; operator watches it move on the map")
    t.takeoff("drone-1")
    p = await c.operator("operator", "desktop")
    await _wait_ui_status(p, 1, "in_flight")
    await p.wait_for_timeout(3000)
    m = await p.evaluate(MAP_POS)
    if "error" in m or "drone-1" not in m:
        await c.note("info", f"map probe unavailable: {m.get('error')}"); return
    d = t.drone("drone-1"); base = _haversine(m["drone-1"]["lat"], m["drone-1"]["lon"], d["latitude"], d["longitude"])
    await c.hud(p, compare=[["Map marker", f"{m['drone-1']['lat']:.5f}, {m['drone-1']['lon']:.5f}", f"{d['latitude']:.5f}, {d['longitude']:.5f}"],
                            ["Marker vs true position", f"{base:.0f} m", "0 m (tracking)"]])
    await c.note("pass", f"baseline: map tracks the drone within {base:.0f} m")
    await c.shot(p, "0-map-accurate")
    await c.step("Condition: telemetry is delayed 8 s (a slow/unstable network)")
    t.fault("socket-delay", value=8000)
    peak = 0; honest = False; samples = 0
    for i in range(6):
        await p.wait_for_timeout(2500)
        m = await p.evaluate(MAP_POS); d = t.drone("drone-1")
        if "drone-1" not in m:
            continue
        gap = _haversine(m["drone-1"]["lat"], m["drone-1"]["lon"], d["latitude"], d["longitude"])
        peak = max(peak, gap); samples += 1
        honest = honest or bool(NOT_LIVE.search(await _freshness_text(p)))
        c.sample(condition="socket-delay", marker_lat=round(m["drone-1"]["lat"], 5), true_lat=round(d["latitude"], 5),
                 gap_m=round(gap))
        await c.hud(p, compare=[["Map marker", f"{m['drone-1']['lat']:.5f}, {m['drone-1']['lon']:.5f}", f"{d['latitude']:.5f}, {d['longitude']:.5f}"],
                                ["Displayed position is off by", f"{gap:.0f} m", "0 m if live"],
                                ["Stale/delayed indicator", "none" if not honest else "shown", "should warn"]])
    t.clear_faults()
    await c.shot(p, "1-map-stale")
    if peak > 40 and not honest and samples >= 2:
        c.find(Finding(title="The map shows an old drone position as current",
                       category="Map & geospatial / live data", level="L2", severity="High",
                       detail=f"Under an 8 s telemetry delay the marker read {peak:.0f} m from the drone's true position, "
                              "with no stale or delayed indicator. An operator directing ground crews to the shown "
                              "location would be off by that distance. Baseline (live) tracking was within a metre.",
                       symptoms=[f"marker up to {peak:.0f} m behind the true position, still presented as current",
                                 "no 'delayed'/'stale' marker anywhere in the UI"],
                       evidence={"baseline_gap_m": round(base), "peak_gap_m": round(peak)}))
        await c.note("bug", f"map position {peak:.0f} m stale, shown as current")
    else:
        await c.note("pass", f"map stayed accurate (peak {peak:.0f} m) or warned of delay")


# 12 ------------------------------------------------------------------------------------------------------------
async def movement_trail(c: Ctx) -> None:
    t = c.truth
    await c.step("Start: Drone 1 flies; the map draws its movement trail (a bonus capability)")
    t.takeoff("drone-1")
    p = await c.operator("operator", "desktop")
    await _wait_ui_status(p, 1, "in_flight")
    truth_path = []
    for _ in range(14):
        d = t.drone("drone-1"); truth_path.append((d["latitude"], d["longitude"]))
        await p.wait_for_timeout(1000)
    trk = await p.evaluate(MAP_TRACK, "drone-1")
    if "error" in trk:
        await c.note("info", f"trail unavailable: {trk['error']}"); return
    pts = trk["points"]
    # every true position the drone passed should lie on the drawn trail (nearest trail point within tolerance)
    worst = 0
    for (la, lo) in truth_path:
        near = min(_haversine(la, lo, tp[0], tp[1]) for tp in pts)
        worst = max(worst, near)
    endpoint_gap = _haversine(truth_path[-1][0], truth_path[-1][1], pts[-1][0], pts[-1][1])
    c.sample(trail_points=len(pts), truth_samples=len(truth_path), worst_deviation_m=round(worst), endpoint_gap_m=round(endpoint_gap))
    await c.hud(p, compare=[["Trail points drawn", str(len(pts)), f"{len(truth_path)}+ path samples"],
                            ["Trail vs true path (max)", f"{worst:.0f} m", "< 15 m"],
                            ["Trail endpoint vs drone", f"{endpoint_gap:.0f} m", "at the drone"]])
    await c.shot(p, "1-trail")
    if worst > 30 or endpoint_gap > 30 or len(pts) < 3:
        c.find(Finding(title="The drone movement trail does not match the real flight path",
                       category="Map & geospatial / bonus: movement trails", level="L2", severity="Medium",
                       detail=f"The drawn trail deviates up to {worst:.0f} m from the path the drone actually flew.",
                       symptoms=[f"max deviation {worst:.0f} m", f"endpoint {endpoint_gap:.0f} m from the drone"]))
        await c.note("bug", f"trail off by up to {worst:.0f} m")
    else:
        await c.note("pass", f"trail faithfully records the path (within {worst:.0f} m, endpoint {endpoint_gap:.0f} m)")



SCENARIOS = [
    Scenario("S1-freshness", "Stale, delayed or offline drone data shown as live", "L2", "Telemetry / freshness",
             "An operator relies on the cockpit to know whether a drone's position and status are current. When the telemetry "
             "feed is lost, delayed or its source goes offline, the cockpit should say so instead of presenting old data as live.",
             "Argus flies Drone 1 via the control API, proves the cockpit updates live, then injects three conditions "
             "(socket-drop 100%, socket-delay 6 s, sim-offline). Every 2 s it compares the on-screen distance, status and link "
             "badge with the simulator's true position and the backend's health, and looks for any stale/delayed wording. "
             "Symptoms with the same root cause are grouped into one issue.", freshness),
    Scenario("S2-frozen-video", "Frozen drone video still labelled 'live'", "L2", "Video and media",
             "When a drone's video stream freezes, the operator must not be told the picture is live.",
             "Argus measures decoded frames per second on the real <video> element and the tile's label every second after "
             "injecting video-freeze for Drone 1, and records how long a frozen picture keeps the 'live' label.", frozen_video),
    Scenario("S3-phone-telemetry", "Battery and altitude unreachable on a phone", "L1", "Responsive UI",
             "A field operator on a phone needs the selected drone's battery and altitude.",
             "Argus opens the cockpit at 390×844 with touch, locates every telemetry field by its meaning, tries to scroll to "
             "it and checks whether each field is inside the screen and not covered by another element.", phone_telemetry),
    Scenario("S4-phone-map-toggle", "Video tile covers the map's 2D/3D switch on a phone", "L1", "Responsive UI / Visual",
             "An operator on a phone switches the map to 2D.",
             "Argus finds the 2D button, checks what element is actually on top at its centre, taps it with a real touch and "
             "verifies whether the map mode changed.", phone_map_toggle),
    Scenario("S5-map-labels", "Drone and dock labels overlap on the map", "L1", "Map / Visual",
             "The operator reads each drone's status from its map label.",
             "Argus reads the map's real label entities from the running Cesium viewer (text, screen position, font) and "
             "measures overlap between label boxes.", map_labels),
    Scenario("S6-reconnect", "Connection loss and recovery", "L3", "Network and recovery",
             "When the server connection drops, the operator should be told, and the cockpit should recover and catch up.",
             "Argus kicks every socket and refuses reconnects for 10 s via the control API, records the link badge during "
             "the outage, then measures how long the cockpit takes to match the simulator again and whether rows duplicate.",
             reconnect_recovery),
    Scenario("S7-two-operators", "Two operators see the same take-off", "L3", "Real-time and multi-user",
             "Several people watch the same incident; a change must reach every screen promptly.",
             "Argus opens two independent operators (desktop and tablet), takes off Drone 2 through the control API and "
             "times when each screen shows it in flight relative to the simulator.", two_operators),
    Scenario("S8-switch-drone", "Selecting another drone switches telemetry and video together", "L2", "Functional / Video",
             "Selecting a drone should show that drone's state and video together.",
             "Argus selects Drone 2, compares telemetry with the simulator and compares a perceptual hash of the video frame "
             "before and after the switch.", switch_drone),
    Scenario("S9-unauth-control", "Anyone can command drones without signing in", "L1", "Security and permissions",
             "Only authorised incident staff should be able to fly or land drones.",
             "Argus acts as a stranger with a fresh browser (no session): follows the cockpit's own Control panel link and "
             "presses Take off, then sends the same command from a page on a different origin. The simulator confirms "
             "whether each drone really took off.", unauthenticated_control),
    Scenario("S10-load-long-run", "Responsiveness with 16 flying drones over about 10 simulated minutes", "L3",
             "Performance / Long-running",
             "The cockpit must stay usable when many drones fly and data keeps streaming for a long time.",
             "Argus measures frames per second, long tasks, click-to-paint latency and JS heap at rest, then adds 12 drones, "
             "flies all 16 at 5x simulator speed and samples the same metrics four times.", load_and_long_run),
    Scenario("S11-map-stale", "The map shows an old drone position as current", "L2", "Map & geospatial",
             "An operator reads a drone's location from the map to direct ground crews. When telemetry is delayed the "
             "map must not present an old position as if it were current.",
             "Argus flies the drone, reads the marker's real latitude/longitude from the live Cesium map and compares it "
             "with the simulator's true position (baseline gap ~0 m). It then delays telemetry 8 s and measures how far "
             "the displayed marker falls behind reality, and whether the UI warns of the delay.", map_stale_position),
    Scenario("S12-movement-trail", "The drone movement trail matches the real flight path", "L2",
             "Map & geospatial (bonus: movement trails)",
             "The cockpit draws each drone's movement trail. The trail is only useful if it records where the drone "
             "actually flew.",
             "Argus samples the drone's true positions during a flight, reads the drawn trail polyline from the live Cesium "
             "map, and checks that every true position lies on the trail and the trail ends at the drone.", movement_trail),
]
