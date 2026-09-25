"""Argus runner: deterministic replay with falsifiable step contracts.

Per step: snapshot -> resolve (T0 replay / T1 similarity / T3 LLM) -> act -> verify effects.
If the target is gone: T2 out-of-order lookahead (no LLM) -> T4 bounded LLM replan.
After the test: business oracles -> triage -> verified-only memory updates.
"""
from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.async_api import Browser, async_playwright

from argus.browser.effects import EffectRecorder, wait_for_settle
from argus.browser.snapshot import compact_for_llm, element_handle, normalize_path, take_snapshot
from argus.config import Settings
from argus.healing.resolver import Resolution, describe, resolve
from argus.healing.similarity import DEFAULT_WEIGHTS, effective_weights, text_sim
from argus.llm import prompts as P
from argus.memory.store import Memory
from argus.models import (Assertion, Effects, ElementInfo, Fingerprint, NetCall, Observation, RunReport,
                          RunTotals, Step, StepExpect, StepResult, TestChange, TestResult, TestSpec, Verdict,
                          now_iso)
from argus.runner.assertions import check, interpolate, network_match, path_matches, status_class
from argus.triage.triage import all_observations, deviation_signature, triage

MAX_REPLANS = 4
LOOKAHEAD = 3
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_IGNORED_CONSOLE = ("failed to load resource", "favicon", "[vite]", "download the react devtools")


class RunContext:
    """Everything a test run needs; also the `ctx` handed to triage and assertions."""

    def __init__(self, settings: Settings, memory: Memory, llm: Any, run_id: str, run_dir: Path):
        self.settings, self.memory, self.llm = settings, memory, llm
        self.run_id, self.run_dir = run_id, run_dir
        self.product_context = settings.product_context()
        self.changelog = settings.changelog()
        self.vars: dict[str, str] = {}
        self.weights = effective_weights(dict(DEFAULT_WEIGHTS), memory.stability())
        self.naive_tokens = 0
        self.interrupts: list[Observation] = []   # pending "interrupt_dismissed" observations
        self._n = 0

    def memory_weights(self) -> dict[str, float]:
        return self.weights

    def url(self, path: str) -> str:
        return path if path.startswith("http") else self.settings.base_url.rstrip("/") + "/" + path.lstrip("/")

    def value_for(self, step: Step) -> str:
        v = step.value or ""
        while "${unique}" in v:
            self._n += 1
            v = v.replace("${unique}", f"{self.run_id[-4:]}{self._n}", 1)
        creds = self.settings.credentials
        v = v.replace("${creds.user}", creds.get("user", "")).replace("${creds.password}", creds.get("password", ""))
        return interpolate(v, self.vars)


# ----------------------------------------------------------------------------------------------
# effects <-> expectations
# ----------------------------------------------------------------------------------------------

def _templatize(text: str, vars: dict[str, str]) -> str:
    """Replace run-specific values (e.g. a unique mission name) with ${vars.x} placeholders."""
    for k, v in sorted(vars.items(), key=lambda kv: -len(str(kv[1]))):
        if v and len(str(v)) >= 4 and str(v) in text:
            text = text.replace(str(v), "${vars.%s}" % k)
    return text


def expect_from_effects(eff: Effects, vars: Optional[dict[str, str]] = None) -> StepExpect:
    """Baseline contract: what this step visibly did (mutating API calls, navigation, new headings)."""
    net, seen = [], set()
    for c in eff.network:
        if c.method.upper() in MUTATING and (c.method, c.path) not in seen:
            seen.add((c.method, c.path))
            net.append(NetCall(method=c.method, path=c.path, status=c.status, resource_type=c.resource_type))
    heads = [_templatize(h, vars or {}) for h in eff.new_headings[:3]]
    return StepExpect(url_changed=True if eff.url_changed else None,
                      url_pattern=normalize_path(eff.url_after) if eff.url_changed else None,
                      network=net[:5], headings_any=heads)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _console_errors(eff: Effects) -> list[str]:
    return [c for c in eff.console_errors if not any(x in c.lower() for x in _IGNORED_CONSOLE)]


def compare_effects(step: Step, eff: Effects, vars: Optional[dict[str, str]] = None) -> list[Observation]:
    """Falsify the step contract: expected calls / navigation / screen vs what actually happened."""
    obs: list[Observation] = []
    exp = step.expect
    if vars and exp.headings_any:
        exp = exp.model_copy(update={"headings_any": [interpolate(h, vars) for h in exp.headings_any]})
    reported: set[tuple[str, str]] = set()
    for nc in exp.network:
        hits = network_match(eff.network, nc.method, nc.path)
        if not hits:
            obs.append(Observation(kind="effect_mismatch", step_id=step.id,
                                   detail=f"expected {nc.method} {nc.path} ({nc.status}) was not called",
                                   evidence={"expected": nc.model_dump(), "observed": [c.model_dump() for c in eff.network]}))
            continue
        got = hits[-1]
        reported.add((got.method, got.path))
        if status_class(got.status) != status_class(nc.status):
            kind = "network_error" if got.status >= 500 or got.status == 0 else "effect_mismatch"
            obs.append(Observation(kind=kind, step_id=step.id,
                                   detail=f"{got.method} {got.path} returned {got.status} (baseline {nc.status})",
                                   evidence={"expected": nc.model_dump(), "observed": got.model_dump()}))
    for c in eff.network:
        if (c.status >= 500 or c.status == 0) and (c.method, c.path) not in reported:
            obs.append(Observation(kind="network_error", step_id=step.id,
                                   detail=f"{c.method} {c.path} failed with {c.status or 'no response'}",
                                   evidence={"observed": c.model_dump()}))
    if exp.url_changed and not eff.url_changed:
        obs.append(Observation(kind="effect_mismatch", step_id=step.id,
                               detail=f"expected navigation to {exp.url_pattern} but stayed on "
                                      f"{normalize_path(eff.url_after)}"
                                      + (f"; page says: {'; '.join(eff.new_alerts[:2])}" if eff.new_alerts else ""),
                               evidence={"alerts": eff.new_alerts}))
    elif exp.url_pattern and eff.url_changed and not path_matches(normalize_path(eff.url_after), exp.url_pattern):
        obs.append(Observation(kind="effect_mismatch", step_id=step.id,
                               detail=f"navigated to {normalize_path(eff.url_after)} instead of {exp.url_pattern}"))
    if exp.headings_any and eff.dom_changed is not None:
        from rapidfuzz import fuzz
        seen_h = eff.new_headings
        if seen_h and not any(fuzz.token_set_ratio(_norm(a), _norm(b)) >= 70 for a in exp.headings_any for b in seen_h):
            obs.append(Observation(kind="effect_mismatch", step_id=step.id,
                                   detail=f"expected screen {exp.headings_any[:2]} but saw {seen_h[:2]}",
                                   evidence={"weak": True}))
    for pe in eff.page_errors:
        obs.append(Observation(kind="page_error", step_id=step.id, detail=f"uncaught exception: {pe[:300]}"))
    for ce in _console_errors(eff):
        obs.append(Observation(kind="console_error", step_id=step.id, detail=f"console error: {ce[:300]}"))
    return obs


# ----------------------------------------------------------------------------------------------
# acting
# ----------------------------------------------------------------------------------------------

async def _act(page: Any, handle: Any, action: str, value: Optional[str]) -> None:
    if action == "click":
        await handle.click(timeout=5000)
    elif action == "fill":
        await handle.fill(value or "", timeout=5000)
    elif action == "select":
        try:
            await handle.select_option(label=value, timeout=3000)
        except Exception:
            await handle.select_option(value=value, timeout=3000)
    elif action in ("check", "uncheck"):
        fn = handle.check if action == "check" else handle.uncheck
        try:
            await fn(timeout=3000)
        except Exception:
            # custom-styled control: the native input is hidden, its <label> is the real click target
            await handle.evaluate("""el => { const l = el.closest('label') ||
                (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
                (l || el).click(); }""")
    elif action == "press":
        await handle.press(value or "Enter")
    elif action == "hover":
        await handle.hover(timeout=4000)


async def _shot(page: Any, ctx: RunContext, name: str) -> Optional[str]:
    rel = f"screenshots/{name}.jpg"
    try:
        await page.screenshot(path=str(ctx.run_dir / rel), type="jpeg", quality=55)
        return rel
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------
# interrupt handling: popups / modals / cookie banners that block the flow
# ----------------------------------------------------------------------------------------------

# Safest dismiss labels, best first (exact match wins over "contains").
_DISMISS_PRIORITY = ("got it", "close", "dismiss", "skip", "not now", "no thanks",
                     "maybe later", "ok", "×", "✕", "continue")
_DESTRUCTIVE_WORDS = re.compile(
    r"\b(delete|remove|destroy|erase|reset|log ?out|sign ?out|unsubscribe|uninstall|buy|purchase|"
    r"pay|order|subscribe|install|upgrade|downgrade|account|confirm|accept all|allow)\b", re.I)


def _dismiss_rank(label: str) -> Optional[int]:
    """Priority of `label` as a dismiss control, or None if it is not a dismiss-like name."""
    stripped = label.strip().lower()
    core = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", stripped)).strip()
    for i, want in enumerate(_DISMISS_PRIORITY):
        if core == want or stripped == want:
            return i
    if core and len(core.split()) <= 3:
        for i, want in enumerate(_DISMISS_PRIORITY):
            if re.search(rf"\b{re.escape(want)}\b", core):
                return len(_DISMISS_PRIORITY) + i
    return None


def _is_dialogish(e: ElementInfo) -> bool:
    return (e.role in ("dialog", "alertdialog") or e.tag == "dialog"
            or e.attrs.get("aria-modal") == "true")


def _blocking_overlay(snap: Any) -> Optional[ElementInfo]:
    """The visible blocking overlay in the snapshot: dialog / aria-modal / fixed full-viewport layer.

    Dialogish overlays win over plain fixed layers (a backdrop is usually a sibling/parent of the
    dialog panel and has no dismiss control of its own).
    """
    best_dialog: Optional[ElementInfo] = None
    best_layer: Optional[ElementInfo] = None
    for e in snap.elements:
        if not e.visible:
            continue
        dialogish = _is_dialogish(e)
        if not (dialogish or e.attrs.get("data-argus-overlay")):
            continue
        if not dialogish and snap.headings and all(h in (e.text or "") for h in snap.headings):
            continue  # fixed layer wrapping the whole app = layout shell, not an interrupt
        cur = best_dialog if dialogish else best_layer
        if cur is None or (e.bbox and cur.bbox and e.bbox.w * e.bbox.h > cur.bbox.w * cur.bbox.h):
            if dialogish:
                best_dialog = e
            else:
                best_layer = e
    return best_dialog or best_layer


def _in_overlay(e: ElementInfo, overlay: ElementInfo) -> bool:
    """Whether `e` sits inside `overlay` (bbox containment, or in_dialog for dialog-ish overlays)."""
    if e.ref == overlay.ref:
        return False
    if e.bbox and overlay.bbox and overlay.bbox.w and overlay.bbox.h:
        cx, cy = e.bbox.x + e.bbox.w / 2, e.bbox.y + e.bbox.h / 2
        return (overlay.bbox.x <= cx <= overlay.bbox.x + overlay.bbox.w
                and overlay.bbox.y <= cy <= overlay.bbox.y + overlay.bbox.h)
    dialogish = overlay.role in ("dialog", "alertdialog") or overlay.tag == "dialog" \
        or overlay.attrs.get("aria-modal") == "true"
    return bool(e.in_dialog and dialogish)


def _overlay_is_target(snap: Any, overlay: ElementInfo, step: Optional[Step]) -> bool:
    """True when the step's own target lives inside the overlay: the modal IS the step's business."""
    if step is None or step.target is None:
        return False
    t = step.target
    for e in snap.elements:
        if not e.interactive or not _in_overlay(e, overlay):
            continue
        if (e.role or e.tag) == (t.role or t.tag) and text_sim(e.name or "", t.name or "") >= 0.85:
            return True
    return False


def _overlay_heading(snap: Any, overlay: ElementInfo) -> str:
    """Stable identity of an overlay across runs: its dialog heading (or trimmed text)."""
    text = overlay.text or overlay.name or ""
    for h in snap.headings:
        if h and h in text:
            return h
    return _norm(overlay.name or text)[:60] or f"{overlay.role or overlay.tag}-overlay"


def _dismiss_control(snap: Any, overlay: ElementInfo, known: str = "") -> Optional[ElementInfo]:
    """Safest dismiss control inside the overlay; `known` = control name remembered from a past run."""
    best: Optional[tuple[int, ElementInfo]] = None
    for e in snap.elements:
        if not (e.interactive and e.visible and e.enabled) or e.editable:
            continue
        if not _in_overlay(e, overlay):
            continue
        label = (e.name or e.text or "").strip()
        if not label or _DESTRUCTIVE_WORDS.search(label):
            continue
        r = _dismiss_rank(label)
        if r is None:
            continue
        if known and text_sim(label, known) >= 0.9:
            r = -1  # dismissed via this control before: instant
        if best is None or r < best[0]:
            best = (r, e)
    return best[1] if best else None


def _interrupt_knowledge(ctx: RunContext) -> Optional[dict]:
    kn = getattr(ctx.memory, "_knowledge", None)
    return kn if isinstance(kn, dict) else None


def _remember_interrupt(ctx: RunContext, heading: str, control: str) -> None:
    """Persist dismissed overlays (by heading) in knowledge.json so next runs dismiss instantly."""
    kn = _interrupt_knowledge(ctx)
    if kn is None:
        return
    kn.setdefault("interrupts", {})[_norm(heading)] = {"control": control, "at": now_iso()}
    try:
        ctx.memory._save_knowledge()
    except Exception:
        pass


def _known_interrupt(ctx: RunContext, heading: str) -> str:
    kn = _interrupt_knowledge(ctx)
    if kn is None:
        return ""
    entry = kn.get("interrupts", {}).get(_norm(heading))
    return str(entry.get("control", "")) if isinstance(entry, dict) else ""


async def _overlay_gone(page: Any, ref: str) -> bool:
    """Whether the overlay element is detached or no longer visible (dismissal actually worked)."""
    try:
        idx = int(ref[1:])
        return bool(await page.evaluate(
            "(i) => { const el = window.__argus && window.__argus.refs && window.__argus.refs[i];"
            " if (!el || !el.isConnected) return true;"
            " try { return !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}); }"
            " catch (_) { const s = window.getComputedStyle(el);"
            " return s.display === 'none' || s.visibility === 'hidden'; } }", idx))
    except Exception:
        return True


async def _dismiss_interrupts(ctx: RunContext, page: Any, rec: EffectRecorder, snap: Any,
                              step: Optional[Step] = None) -> bool:
    """Dismiss a blocking overlay ("What's new", cookie banner, tour) that is not the step's target.

    Clicks the safest dismiss control (got it / close / skip / ... - never destructive) or presses
    Escape. Records an `interrupt_dismissed` observation on `ctx.interrupts` (cosmetic for triage)
    and remembers the overlay in knowledge.json. Returns True when something was dismissed.
    """
    overlay = _blocking_overlay(snap)
    if overlay is None or _overlay_is_target(snap, overlay, step):
        return False
    heading = _overlay_heading(snap, overlay)
    control = _dismiss_control(snap, overlay, _known_interrupt(ctx, heading))
    if control is None and not _is_dialogish(overlay):
        return False  # a plain fixed layer with no dismiss control: don't guess (Escape won't help)
    await rec.begin()
    how = ""
    try:
        if control is not None:
            handle = await element_handle(page, control.ref)
            if handle is not None:
                await handle.click(timeout=3000)
                how = f"clicked '{(control.name or control.text)[:40]}'"
        if not how:
            await page.keyboard.press("Escape")
            how = "pressed Escape"
    except Exception:
        try:
            await page.keyboard.press("Escape")
            how = "pressed Escape"
        except Exception:
            await rec.end()
            return False
    await rec.end()
    try:
        await page.wait_for_timeout(150)
    except Exception:
        pass
    if not await _overlay_gone(page, overlay.ref):
        return False
    _remember_interrupt(ctx, heading, (control.name or control.text).strip() if control else "escape")
    ctx.interrupts.append(Observation(
        kind="interrupt_dismissed", step_id=step.id if step else None,
        detail=f"dismissed interrupt '{heading}' ({how})",
        evidence={"overlay": describe(overlay), "heading": heading}))
    return True


def _drain_interrupts(ctx: RunContext, result: TestResult, step_id: Optional[str] = None) -> None:
    """Move pending interrupt observations onto the test result (triage treats them as cosmetic)."""
    for o in ctx.interrupts:
        if o.step_id is None:
            o.step_id = step_id
        result.observations.append(o)
    ctx.interrupts.clear()


class _Exec:
    """Mutable per-test execution state."""

    def __init__(self, test: TestSpec):
        self.test = test
        self.results: list[StepResult] = []
        self.trace: list[tuple[Step, str]] = []       # executed steps (new targets / observed effects), origin
        self.healed: list[tuple[Fingerprint, Fingerprint]] = []
        self.calls: list[NetCall] = []
        self.done: list[str] = []
        self.idx = 0


async def _execute(ctx: RunContext, page: Any, rec: EffectRecorder, st: _Exec, step: Step, res: Resolution,
                   snap: Any, origin: str) -> StepResult:
    t0 = time.perf_counter()
    el = res.element
    value = ctx.value_for(step) if step.action in ("fill", "select", "press") else None
    ctx.naive_tokens += len(compact_for_llm(snap)) // 4 + 350
    _DETACH_MARKERS = ("detached", "not attached", "element is not attached")

    await rec.begin()
    error = None
    try:
        handle = await element_handle(page, el.ref)
        if handle is None:
            raise RuntimeError(f"element {el.ref} detached")
        await _act(page, handle, step.action, value)
    except Exception as exc:
        error = str(exc).splitlines()[0][:200]
        # Retry once if the element was detached between snapshot and action (e.g. React re-render)
        if any(m in error.lower() for m in _DETACH_MARKERS):
            try:
                fresh_snap = await take_snapshot(page)
                retry_res = await resolve(fresh_snap, step, ctx.weights, None, ctx.settings)
                if retry_res.found:
                    el = retry_res.element
                    handle = await element_handle(page, el.ref)
                    if handle is not None:
                        await _act(page, handle, step.action, value)
                        error = None   # retry succeeded
            except Exception as retry_exc:
                error = str(retry_exc).splitlines()[0][:200]
    eff = await rec.end()
    st.calls.extend(eff.network)
    if step.save_as and value is not None:
        ctx.vars[step.save_as] = value

    obs: list[Observation] = []
    new_fp = Fingerprint.from_element(el)
    tier = res.tier
    if origin == "orig" and tier in (1, 3, 5):
        obs.append(Observation(kind="locator_healed", step_id=step.id, tier=tier, confidence=res.score,
                               detail=f"{step.target.describe()} -> {describe(el)} via {res.method} "
                                      f"(score {res.score:.2f}, margin {res.margin:.2f})",
                               evidence={"candidates": res.candidates, "reason": res.reason}))
        st.healed.append((step.target, new_fp))
    elif origin == "reordered":
        obs.append(Observation(kind="step_reordered", step_id=step.id, tier=2, confidence=res.score,
                               detail=f"'{step.intent}' executed out of order (flow reordered); matched "
                                      f"{describe(el)} score {res.score:.2f}",
                               evidence={"label": el.label or el.name, "context": el.context,
                                         "action": step.action}))
    elif origin == "added":
        obs.append(Observation(kind="step_added", step_id=step.id, tier=4,
                               detail=f"new step: {step.intent} -> {describe(el)}"
                                      + (f" = {value!r}" if value else ""),
                               evidence={"label": el.label or el.name, "context": el.context,
                                         "action": step.action, "method": res.method}))
    if error:
        obs.append(Observation(kind="timeout", step_id=step.id, detail=f"action failed: {error}"))
    if origin != "added":
        obs.extend(compare_effects(step, eff, ctx.vars))
    else:
        obs.extend(o for o in compare_effects(step.model_copy(update={"expect": StepExpect()}), eff))

    st.idx += 1
    shot = await _shot(page, ctx, f"{st.test.id}_{st.idx:02d}")
    status = {"orig": "passed" if tier == 0 else "healed", "reordered": "reordered", "added": "added"}[origin]
    if error:
        status = "failed"
    sr = StepResult(step_id=step.id, intent=step.intent, status=status, tier=tier, score=res.score,
                    duration_ms=int((time.perf_counter() - t0) * 1000), screenshot=shot,
                    observations=obs, effects=eff)
    st.results.append(sr)
    st.trace.append((step.model_copy(update={"target": new_fp, "expect": expect_from_effects(eff, ctx.vars)}), origin))
    st.done.append(f"{len(st.done) + 1}. {step.intent}" + (f" = {value!r}" if value and step.action != "press" else ""))
    return sr


_PROGRESS = re.compile(r"\b(next|continue|proceed|save|submit|apply|confirm)\b", re.I)


def _blocking_fields(snap: Any) -> list:
    """Visible, enabled, empty fields that the page marks as required (attribute, '*', or an alert)."""
    alerts = " ".join(snap.alerts).lower()
    out = []
    for e in snap.elements:
        if not (e.interactive and e.editable and e.enabled) or e.attrs.get("value"):
            continue
        if e.attrs.get("type") in ("password", "search", "hidden"):
            continue
        label = (e.label or e.name).strip()
        core = label.rstrip("* ").lower()
        if "required" in e.attrs or label.endswith("*") or (core and core in alerts):
            out.append(e)
    return out


async def _complete_required(ctx: RunContext, page: Any, rec: EffectRecorder, st: "_Exec", step: Step,
                             snap: Any) -> int:
    """Fill newly required fields with valid heuristic data and press the page's progress button."""
    from argus.explore.explorer import heuristic_value
    fields = _blocking_fields(snap)
    if not fields:
        return 0
    done = 0
    for n, el in enumerate(fields[:4], 1):
        value = heuristic_value(el, ctx.settings.credentials)
        if not value:
            continue
        s = Step(id=f"{step.id}-h{n}", intent=f"Fill new required field '{(el.label or el.name).rstrip('* ')}'",
                 action="fill", target=Fingerprint.from_element(el), value=value)
        await _execute(ctx, page, rec, st, s, Resolution(element=el, tier=4, method="heuristic", score=1.0),
                       snap, "added")
        done += 1
    if not done:
        return 0
    ctx_name = fields[0].context
    buttons = [e for e in snap.elements if e.role == "button" and e.enabled and _PROGRESS.search(e.name or e.text)]
    buttons.sort(key=lambda e: e.context != ctx_name)
    if buttons:
        b = buttons[0]
        s = Step(id=f"{step.id}-h0", intent=f"Continue with '{b.name}'", action="click",
                 target=Fingerprint.from_element(b))
        await _execute(ctx, page, rec, st, s, Resolution(element=b, tier=4, method="heuristic", score=1.0),
                       snap, "added")
    return done


async def _execute_visual(ctx: RunContext, page: Any, rec: EffectRecorder, st: "_Exec", step: Step) -> bool:
    """Canvas/map target: locate the VisualMark (cached coords -> template -> multiscale -> VLM) and click.

    When locate() returns tier > 0 (the mark had to be re-found by template/multiscale/VLM), we
    re-capture the mark at the new hit point so that _apply_verdict can persist the updated
    VisualMark into the plan after a verified run.  Next run will then hit V0 (tier 0) from the
    freshly-captured crop/phash/center_norm instead of re-running the full cascade.
    """
    from argus.vision.locate import locate
    from argus.vision.marks import VisualMark, capture_mark
    t0 = time.perf_counter()
    old_mark = VisualMark(**step.visual)
    hit = await locate(page, old_mark, ctx.llm)
    if hit is None:
        return False

    # When the mark was healed (tier > 0), re-capture it at the new page position so that
    # _apply_verdict can persist the up-to-date VisualMark into the compiled plan.
    new_visual: dict = step.visual  # default: keep original if recapture fails
    if hit["tier"] != 0:
        try:
            canvas_selector = old_mark.canvas_selector
            # hit["x"]/hit["y"] are page coordinates; convert to canvas-relative coords
            bbox = await page.locator(canvas_selector).bounding_box()
            if bbox is not None:
                canvas_x = hit["x"] - bbox["x"]
                canvas_y = hit["y"] - bbox["y"]
                hint = old_mark.hint
                fresh = await capture_mark(page, canvas_selector, canvas_x, canvas_y,
                                           hint, size=old_mark.crop_size or 64)
                new_visual = fresh.model_dump()
        except Exception:
            pass  # non-fatal: worst case next run re-heals again

    await rec.begin()
    await page.mouse.click(hit["x"], hit["y"])
    eff = await rec.end()
    st.calls.extend(eff.network)
    tier = 0 if hit["tier"] == 0 else 5
    obs = compare_effects(step, eff, ctx.vars)
    if tier:
        obs.insert(0, Observation(kind="locator_healed", step_id=step.id, tier=5, confidence=float(hit["confidence"]),
                                  detail=f"visual '{step.visual.get('hint', '')}' re-found by {hit['method']} "
                                         f"(confidence {float(hit['confidence']):.2f})"))
    st.idx += 1
    shot = await _shot(page, ctx, f"{st.test.id}_{st.idx:02d}")
    st.results.append(StepResult(step_id=step.id, intent=step.intent, status="passed" if tier == 0 else "healed",
                                 tier=tier, score=float(hit["confidence"]), screenshot=shot, observations=obs,
                                 duration_ms=int((time.perf_counter() - t0) * 1000), effects=eff))
    # Carry the (possibly recaptured) VisualMark on the trace step so _apply_verdict can persist it.
    st.trace.append((step.model_copy(update={"visual": new_visual,
                                             "expect": expect_from_effects(eff, ctx.vars)}), "orig"))
    st.done.append(f"{len(st.done) + 1}. {step.intent}")
    return True


def _target_present(snap: Any, fp: Fingerprint, weights: dict[str, float]) -> bool:
    from argus.healing.similarity import rank
    ranked = rank(fp, snap.elements, "goto", weights, top_k=1)
    return bool(ranked) and ranked[0][1] >= 0.6


async def _advance_to_target(ctx: RunContext, page: Any, rec: EffectRecorder, st: "_Exec", fp: Fingerprint,
                             hops: int = 3) -> None:
    """Walk forward through a (possibly reordered) flow until the oracle's target is on screen."""
    for hop in range(hops):
        snap = await take_snapshot(page)
        if _target_present(snap, fp, ctx.weights):
            return
        if await _complete_required(ctx, page, rec, st, Step(id=f"adv{hop}", intent="advance", action="click"), snap):
            continue
        buttons = [e for e in snap.elements if e.role == "button" and e.enabled and _PROGRESS.search(e.name or e.text)]
        if not buttons:
            return
        b = buttons[0]
        s = Step(id=f"adv{hop}-c", intent=f"Advance with '{b.name}' to reach the checked screen", action="click",
                 target=Fingerprint.from_element(b))
        await _execute(ctx, page, rec, st, s, Resolution(element=b, tier=4, method="heuristic", score=1.0), snap, "added")


def _is_commit(step: Step) -> bool:
    """A commit point made a mutating API call on the baseline (create/save/launch...)."""
    return any(c.method.upper() in MUTATING for c in step.expect.network)


async def _save_trace(context: Any, ctx: RunContext, test: TestSpec, result: TestResult) -> None:
    """Keep a Playwright trace (open with `playwright show-trace`) only for runs with real deviations."""
    deviating = any(o.kind not in ("locator_healed", "interrupt_dismissed") for o in all_observations(result))
    try:
        if deviating:
            rel = f"traces/{test.id}.zip"
            (ctx.run_dir / "traces").mkdir(exist_ok=True)
            await context.tracing.stop(path=str(ctx.run_dir / rel))
            result.trace = rel
        else:
            await context.tracing.stop()
    except Exception:
        pass


def _rules_excerpt(product: str, limit: int = 1500) -> str:
    rules = [l.strip() for l in product.splitlines() if re.match(r"^\s*[-*]?\s*\**\s*R\d+\b", l)]
    return "\n".join(rules)[:limit] if rules else product[:limit] or "(none)"


async def _replan(ctx: RunContext, snap: Any, st: _Exec, step: Step, pending: list[Step]) -> Optional[dict]:
    if ctx.llm is None:
        return None
    user = P.REPLAN_USER.format(
        goal=st.test.goal, done="\n".join(st.done[-8:]) or "(nothing yet)", intent=step.intent,
        original=step.target.describe() if step.target else "-",
        remaining="\n".join(f"- {s.intent}" for s in pending[1:6]) or "(none)",
        rules=_rules_excerpt(ctx.product_context), url=normalize_path(snap.url), title=snap.title,
        headings=", ".join(snap.headings[:6]) or "(none)", alerts="; ".join(snap.alerts[:6]) or "(none)",
        elements=compact_for_llm(snap, limit=120, only_interactive=True))
    try:
        return await ctx.llm.json(tier="smart", purpose="replan", system=P.REPLAN_SYSTEM, user=user, max_tokens=600)
    except Exception:
        return None


async def _oracles_hold(ctx: RunContext, page: Any, test: TestSpec, st: _Exec, rec: EffectRecorder) -> bool:
    for a in test.oracles:
        if a.kind == "llm_check":
            return False
        ok, _ = await check(a, page, ctx, st.calls, rec.all_page_errors(), wait_ms=0)
        if not ok:
            return False
    return bool(test.oracles)


# ----------------------------------------------------------------------------------------------
# one test
# ----------------------------------------------------------------------------------------------

async def run_test(test: TestSpec, ctx: RunContext, browser: Browser, auth_state: Optional[str],
                   update: bool = True) -> TestResult:
    t0 = time.perf_counter()
    ctx.vars = {}
    st = _Exec(test)
    result = TestResult(test_id=test.id, test_name=test.name, test_version=test.version, status="passed",
                        verdict=Verdict(category="PASS"))
    context = await browser.new_context(viewport=ctx.settings.viewport,
                                        storage_state=auth_state if (test.requires_login and auth_state) else None)
    await _inject_session_storage(context, ctx)
    try:
        await context.tracing.start(screenshots=True, snapshots=True)
    except Exception:
        pass
    page = await context.new_page()
    rec = EffectRecorder(page)
    try:
        try:
            resp = await page.goto(ctx.url(test.start_url), wait_until="domcontentloaded", timeout=15000)
            await wait_for_settle(page, rec)
        except Exception as exc:
            result.status = "error"
            result.verdict = Verdict(category="INFRA", confidence=0.9, rationale=f"App unreachable: {exc}"[:300])
            return result
        start_status = resp.status if resp else 0
        if test.requires_login and "login" not in test.start_url and "login" in normalize_path(page.url):
            result.observations.append(Observation(
                kind="goal_unreachable", detail=f"session missing: {test.start_url} redirected to login",
                evidence={"precondition": "authenticated session"}))
            result.steps = []
            result.verdict = await triage(test, result, ctx)
            result.status = "error"
            return result
        if start_status >= 400:
            result.observations.append(Observation(
                kind="network_error" if start_status >= 500 else "effect_mismatch",
                detail=f"start page {test.start_url} returned HTTP {start_status}"))

        pending = list(test.steps)
        replans = 0
        heuristic_rounds = 0
        while pending:
            step = pending[0]
            if step.action in ("goto", "wait") or (step.target is None and step.action == "press"):
                await rec.begin()
                if step.action == "goto":
                    await page.goto(ctx.url(step.value or test.start_url), wait_until="domcontentloaded")
                elif step.action == "press":
                    await page.keyboard.press(step.value or "Enter")
                else:
                    await page.wait_for_timeout(int(step.value or 500))
                eff = await rec.end()
                st.calls.extend(eff.network)
                st.results.append(StepResult(step_id=step.id, intent=step.intent, status="passed", tier=0,
                                             observations=compare_effects(step, eff, ctx.vars), effects=eff))
                st.trace.append((step, "orig"))
                pending.pop(0)
                continue

            if step.visual:
                if await _execute_visual(ctx, page, rec, st, step):
                    pending.pop(0)
                    continue
                result.observations.append(Observation(kind="goal_unreachable", step_id=step.id,
                                                       detail=f"visual target '{step.visual.get('hint', '')}' not found "
                                                              "on the canvas (V0-V3)"))
                break
            snap = await take_snapshot(page)
            if await _dismiss_interrupts(ctx, page, rec, snap, step):
                _drain_interrupts(ctx, result, step.id)
                snap = await take_snapshot(page)
            res = await resolve(snap, step, ctx.weights, ctx.llm, ctx.settings)
            if res.found:
                sr = await _execute(ctx, page, rec, st, step, res, snap, "orig")
                pending.pop(0)
                blocked = any(o.kind == "effect_mismatch" and "expected navigation" in o.detail for o in sr.observations)
                if blocked and heuristic_rounds < 2:
                    after = await take_snapshot(page)
                    if _blocking_fields(after):
                        heuristic_rounds += 1
                        await _complete_required(ctx, page, rec, st, step, after)
                continue

            # an interrupt that appeared after the first check may still hide the target (T2/T4 guard)
            if await _dismiss_interrupts(ctx, page, rec, snap, step):
                _drain_interrupts(ctx, result, step.id)
                snap = await take_snapshot(page)
                res = await resolve(snap, step, ctx.weights, ctx.llm, ctx.settings)
                if res.found:
                    await _execute(ctx, page, rec, st, step, res, snap, "orig")
                    pending.pop(0)
                    continue

            # T2: the flow may have been reordered - is a later step's target here? (no LLM)
            moved = False
            for j in range(1, min(LOOKAHEAD, len(pending) - 1) + 1):
                if _is_commit(pending[j - 1]):
                    break  # ordering constraint: nothing may jump across a step that commits data
                later = pending[j]
                if later.target is None or later.action == "goto":
                    continue
                r2 = await resolve(snap, later, ctx.weights, None, ctx.settings, strict=True)
                if r2.found:
                    await _execute(ctx, page, rec, st, later, r2, snap, "reordered")
                    pending.pop(j)
                    moved = True
                    break
            if moved:
                continue
            if step.optional:
                pending.pop(0)
                continue
            if "negative" in test.tags and await _oracles_hold(ctx, page, test, st, rec):
                for s in pending:
                    result.observations.append(Observation(kind="step_missing", step_id=s.id,
                                                           detail=f"'{s.intent}' not needed: business oracles "
                                                                  "already hold at this point"))
                pending.clear()
                break

            # T4a: a new required field blocks the flow -> complete it deterministically (no LLM)
            if heuristic_rounds < 2 and start_status < 400:
                heuristic_rounds += 1
                if await _complete_required(ctx, page, rec, st, step, snap):
                    continue

            # T4: bounded replanning with a model
            if replans >= MAX_REPLANS or start_status >= 400 or ctx.llm is None:
                result.observations.append(Observation(
                    kind="goal_unreachable", step_id=step.id,
                    detail=f"cannot find '{step.intent}' ({res.reason}); page {normalize_path(snap.url)} "
                           f"headings {snap.headings[:3]} alerts {snap.alerts[:3]}",
                    evidence={"candidates": res.candidates}))
                break
            replans += 1
            plan = await _replan(ctx, snap, st, step, pending)
            decision = (plan or {}).get("decision", "blocked")
            if decision == "act":
                acted = 0
                for n, a in enumerate((plan.get("actions") or [])[:5], 1):
                    el = snap.by_ref(str(a.get("ref", "")))
                    action = str(a.get("action", "click"))
                    if el is None or action not in ("click", "fill", "select", "check", "uncheck", "press"):
                        continue
                    new_step = Step(id=f"{step.id}-r{replans}{n}", intent=str(a.get("intent") or f"{action} {el.name}"),
                                    action=action, target=Fingerprint.from_element(el), value=a.get("value"),
                                    page_hint=(snap.headings or [""])[0])
                    before = page.url
                    await _execute(ctx, page, rec, st, new_step,
                                   Resolution(element=el, tier=4, method="llm", score=1.0), snap, "added")
                    acted += 1
                    if page.url != before:
                        break
                if acted:
                    continue
                decision = "blocked"
            if decision == "skip_step":
                result.observations.append(Observation(kind="step_missing", step_id=step.id,
                                                       detail=f"'{step.intent}' no longer applies: "
                                                              f"{(plan or {}).get('reason', '')}"))
                pending.pop(0)
                continue
            result.observations.append(Observation(
                kind="goal_unreachable", step_id=step.id,
                detail=f"blocked at '{step.intent}': {(plan or {}).get('reason', 'no plan')}; page "
                       f"{normalize_path(snap.url)} alerts {snap.alerts[:3]}"))
            break

        # an element oracle whose target lives on a later screen (flow reordered): advance until it appears
        for a in test.oracles:
            if a.kind in ("element_state", "element_visible") and isinstance(a.params.get("fingerprint"), dict):
                await _advance_to_target(ctx, page, rec, st, Fingerprint(**a.params["fingerprint"]))

        # business oracles
        for i, a in enumerate(test.oracles):
            ok, detail = await check(a, page, ctx, st.calls, rec.all_page_errors())
            if not ok:
                result.observations.append(Observation(
                    kind="assertion_failed", detail=f"{a.description or a.kind}: {detail}",
                    evidence={"oracle_index": i, "rule_ref": a.rule_ref, "assertion": a.model_dump()}))
        reported = {o.detail for s in st.results for o in s.observations}
        for pe in rec.all_page_errors():
            if not any(pe[:80] in d for d in reported):
                result.observations.append(Observation(kind="page_error", detail=f"uncaught exception: {pe[:300]}"))
        if any(o.kind in ("assertion_failed", "page_error", "goal_unreachable") for o in result.observations):
            try:
                rel = f"screenshots/{test.id}_final.jpg"
                await page.screenshot(path=str(ctx.run_dir / rel), type="jpeg", quality=60, full_page=True)
                result.observations[-1].evidence["screenshot"] = rel
            except Exception:
                pass
    except Exception as _unexpected:
        # Catch-all: any unexpected exception during the step loop becomes an INFRA verdict so
        # the suite can continue with the next test instead of propagating a crash.
        result.status = "error"
        result.verdict = Verdict(
            category="INFRA", confidence=0.9,
            rationale=f"Unexpected error during test execution: {_unexpected}"[:300],
        )
    finally:
        result.steps = st.results
        await _save_trace(context, ctx, test, result)
        await context.close()

    result.verdict = await triage(test, result, ctx)
    cat = result.verdict.category
    result.status = ("passed" if cat in ("PASS", "COSMETIC_DRIFT", "INTENDED_CHANGE")
                     else "retired" if cat == "FEATURE_REMOVED" else "failed")
    if update:
        _apply_verdict(test, result, st, ctx)
    if cat == "BUG":
        result.bug_report = bug_report(test, result, st, ctx)
    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    if ctx.llm is not None:
        result.llm_calls = ctx.llm.take_ledger()
    return result


def _steps_text(steps: list[Step]) -> list[str]:
    return [f"{s.action} {s.target.describe() if s.target else s.value or ''} :: {s.intent}" for s in steps]


def _apply_verdict(test: TestSpec, result: TestResult, st: _Exec, ctx: RunContext) -> None:
    """Verified-only memory: tests/heals are updated only when the business outcome held."""
    v = result.verdict
    mem = ctx.memory
    if v.category == "COSMETIC_DRIFT":
        # Build a lookup of updated targets AND updated visuals from the trace.
        # _execute_visual places a fresh VisualMark on the trace step when tier > 0,
        # so new_visuals carries the healed canvas mark that must replace the old one.
        new_targets = {s.id: s.target for s, origin in st.trace if origin == "orig"}
        new_visuals = {s.id: s.visual for s, origin in st.trace if origin == "orig" and s.visual is not None}
        steps = [
            s.model_copy(update={
                "target": new_targets.get(s.id, s.target),
                "visual": new_visuals.get(s.id, s.visual),
            })
            for s in test.steps
        ]
        for old, new in st.healed:
            mem.learn_from_heal(old, new)
        n = len(st.healed)
        saved = mem.save_test(test.model_copy(update={"steps": steps}), TestChange(
            version=test.version + 1, kind="healed", summary=f"Self-healed {n} locator(s); flow unchanged.",
            diff="\n".join(f"- {o.describe()}\n+ {nw.describe()}" for o, nw in st.healed),
            verdict_ref=ctx.run_id), build=ctx.settings.build)
        result.updated_to_version = saved.version
    elif v.category == "INTENDED_CHANGE":
        # st.trace steps already carry the updated target + visual (set by _execute and _execute_visual).
        steps = [s for s, _ in st.trace]
        for old, new in st.healed:
            mem.learn_from_heal(old, new)
        diff = "\n".join(difflib.unified_diff(_steps_text(test.steps), _steps_text(steps), "before", "after",
                                              lineterm="", n=0))
        saved = mem.save_test(test.model_copy(update={"steps": steps}), TestChange(
            version=test.version + 1, kind="updated", summary=f"Adapted to intended change: {v.rationale}"[:400],
            diff=diff, verdict_ref=ctx.run_id), build=ctx.settings.build)
        result.updated_to_version = saved.version
        mem.put_decision(deviation_signature(test.id, all_observations(result)), v)
    elif v.category == "FEATURE_REMOVED":
        saved = mem.save_test(test.model_copy(update={"status": "retired"}), TestChange(
            version=test.version + 1, kind="retired", summary=f"Retired: {v.rationale}"[:400], verdict_ref=ctx.run_id), build=ctx.settings.build)
        result.updated_to_version = saved.version
    elif v.category == "NEEDS_REVIEW":
        proposal = test.model_copy(update={"steps": [s for s, _ in st.trace]})
        (ctx.run_dir / "proposals").mkdir(exist_ok=True)
        (ctx.run_dir / "proposals" / f"{test.id}.json").write_text(proposal.model_dump_json(indent=2), encoding="utf-8")


def bug_report(test: TestSpec, result: TestResult, st: _Exec, ctx: RunContext) -> str:
    obs = all_observations(result)
    key = [o for o in obs if o.kind in ("page_error", "network_error", "console_error", "assertion_failed",
                                        "effect_mismatch", "goal_unreachable", "timeout")]
    shots = [s.screenshot for s in result.steps if s.screenshot][-2:]
    lines = [f"## BUG: {test.name}", "", f"**Verdict:** {result.verdict.rationale}", "",
             f"**Business goal:** {test.goal}", "", "**Reproduce:**"]
    lines += [f"{i}. {d.split('. ', 1)[-1]}" for i, d in enumerate([f"Open {test.start_url}"] + st.done, 1)]
    lines += ["", "**Expected vs actual:**"]
    lines += [f"- `{o.kind}` {o.detail}" for o in key[:8]]
    if ctx.changelog:
        lines += ["", "**Release notes check:** none of the release notes explain this behaviour."]
    if shots:
        lines += ["", "**Evidence:** " + ", ".join(shots)]
    return "\n".join(lines)


async def record_baseline(test: TestSpec, ctx: RunContext, browser: Browser,
                          auth_state: Optional[str]) -> Optional[TestSpec]:
    """Execute a (generated) test on a known-good build and freeze its contract.

    Targets are re-fingerprinted from the live page, step expectations come from observed effects,
    and oracles that do not hold on the baseline are dropped (they were wrong, not the app).
    Returns None if the flow cannot be executed end to end.
    """
    ctx.vars = {}
    st = _Exec(test)
    context = await browser.new_context(viewport=ctx.settings.viewport,
                                        storage_state=auth_state if (test.requires_login and auth_state) else None)
    await _inject_session_storage(context, ctx)
    page = await context.new_page()
    rec = EffectRecorder(page)
    try:
        await page.goto(ctx.url(test.start_url), wait_until="domcontentloaded", timeout=15000)
        await wait_for_settle(page, rec)
        for step in test.steps:
            if step.visual:
                if not await _execute_visual(ctx, page, rec, st, step):
                    return None
                continue
            if step.action in ("goto", "wait") or step.target is None:
                if step.action == "goto":
                    await page.goto(ctx.url(step.value or test.start_url), wait_until="domcontentloaded")
                    await wait_for_settle(page, rec)
                st.trace.append((step, "orig"))
                continue
            snap = await take_snapshot(page)
            if await _dismiss_interrupts(ctx, page, rec, snap, step):
                ctx.interrupts.clear()  # baseline authoring: no observations to report
                snap = await take_snapshot(page)
            res = await resolve(snap, step, ctx.weights, ctx.llm, ctx.settings)
            if not res.found:
                return None
            sr = await _execute(ctx, page, rec, st, step.model_copy(update={"expect": StepExpect()}), res, snap, "orig")
            if sr.status == "failed" or any(o.kind in ("page_error", "network_error") for o in sr.observations):
                return None
        keep = []
        for a in test.oracles:
            ok, _ = await check(a, page, ctx, st.calls, rec.all_page_errors())
            if ok:
                keep.append(a)
        if not keep:
            keep = [Assertion(kind="no_page_errors", description="No uncaught errors during the journey")]
    finally:
        await context.close()
    steps = [s for s, _ in st.trace]
    return test.model_copy(update={"steps": steps, "oracles": keep})


# ----------------------------------------------------------------------------------------------
# auth + suite
# ----------------------------------------------------------------------------------------------

_LOGIN_WORDS = re.compile(r"\b(log ?in|sign ?in|continue|submit|enter)\b", re.I)

_SESSION_DUMP_JS = ("() => { const o = {}; try { for (let i = 0; i < sessionStorage.length; i++)"
                    " { const k = sessionStorage.key(i); o[k] = sessionStorage.getItem(k); } }"
                    " catch (_) {} return o; }")


def _origin_of(url: str) -> str:
    """App origin for session re-injection; empty for file:// and other opaque origins."""
    p = urlparse(url or "")
    if not p.netloc or p.scheme not in ("http", "https"):
        return ""
    return f"{p.scheme}://{p.netloc}"


async def _capture_session_storage(page: Any) -> dict[str, str]:
    try:
        data = await page.evaluate(_SESSION_DUMP_JS)
        return {str(k): str(v) for k, v in (data or {}).items() if v is not None}
    except Exception:
        return {}


async def _inject_session_storage(context: Any, ctx: RunContext) -> None:
    """Re-inject captured sessionStorage (token auth) into a new context, guarded by app origin."""
    path = ctx.settings.home / "auth" / "session.json"
    if not path.exists():
        return
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    data = blob.get("data") if isinstance(blob, dict) else None
    if not isinstance(data, dict) or not data:
        return
    origin = str(blob.get("origin", ""))
    script = ("(() => { const d = " + json.dumps(data) + ", o = " + json.dumps(origin) + ";"
              " try { if (!o || location.origin === o)"
              " { for (const k in d) sessionStorage.setItem(k, d[k]); } } catch (_) {} })();")
    try:
        await context.add_init_script(script)
    except Exception:
        pass


async def ensure_auth(browser: Browser, ctx: RunContext) -> Optional[str]:
    """Heuristic, model-free login with configured credentials; returns a storage_state path."""
    creds = ctx.settings.credentials
    if not creds.get("user"):
        return None
    context = await browser.new_context(viewport=ctx.settings.viewport)
    page = await context.new_page()
    rec = EffectRecorder(page)
    try:
        await page.goto(ctx.url(creds.get("login_path", "/login")), wait_until="domcontentloaded")
        await wait_for_settle(page, rec)
        snap = await take_snapshot(page)
        if await _dismiss_interrupts(ctx, page, rec, snap):
            ctx.interrupts.clear()
            snap = await take_snapshot(page)
        pw = next((e for e in snap.elements if e.attrs.get("type") == "password" and e.editable), None)
        if pw is None:
            return None
        user = next((e for e in snap.elements if e.editable and e.ref != pw.ref and (
            e.attrs.get("type") == "email" or re.search(r"user|email|login", " ".join(
                [e.name, e.label, e.attrs.get("name", ""), e.attrs.get("placeholder", "")]), re.I))), None)
        submit = next((e for e in snap.elements if e.role == "button" and e.enabled and (
            _LOGIN_WORDS.search(e.name or e.text) or e.attrs.get("type") == "submit")), None)
        if user is None or submit is None:
            return None
        await (await element_handle(page, user.ref)).fill(creds["user"])
        await (await element_handle(page, pw.ref)).fill(creds.get("password", ""))
        await rec.begin()
        await (await element_handle(page, submit.ref)).click()
        await rec.end()
        after = await take_snapshot(page)
        if any(e.attrs.get("type") == "password" for e in after.elements):
            return None
        path = ctx.settings.home / "auth" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(path))
        session = await _capture_session_storage(page)
        if session:
            spath = ctx.settings.home / "auth" / "session.json"
            spath.write_text(json.dumps({"origin": _origin_of(page.url), "data": session}, indent=2),
                             encoding="utf-8")
        return str(path)
    finally:
        await context.close()


def compute_totals(report: RunReport, naive_tokens: int) -> RunTotals:
    t = RunTotals(tests=len(report.results))
    for r in report.results:
        t.passed += r.status == "passed"
        t.failed += r.status in ("failed", "error")
        t.verdicts[r.verdict.category] = t.verdicts.get(r.verdict.category, 0) + 1
        t.duration_ms += r.duration_ms
        for s in r.steps:
            if s.tier is not None:
                t.tiers[str(s.tier)] = t.tiers.get(str(s.tier), 0) + 1
            t.replayed_steps += s.tier == 0
            t.healed_steps += s.status == "healed"
        for c in r.llm_calls:
            if c.cached:
                t.llm_calls_cached += 1
            else:
                t.llm_calls += 1
            t.tokens_in += c.tokens_in
            t.tokens_out += c.tokens_out
            t.cost_usd += c.cost_usd
            t.list_cost_usd += c.list_cost_usd
    t.naive_tokens_estimate = naive_tokens
    t.cost_usd = round(t.cost_usd, 6)
    t.list_cost_usd = round(t.list_cost_usd, 6)
    return t


async def run_suite(settings: Settings, *, test_ids: Optional[list[str]] = None, label: str = "",
                    update: bool = True, on_result: Optional[Any] = None) -> RunReport:
    memory = Memory(settings.home)
    run_id, run_dir = memory.new_run_dir()
    llm = None
    if settings.llm_enabled:
        from argus.llm.client import LLMClient
        llm = LLMClient(settings)
    ctx = RunContext(settings, memory, llm, run_id, run_dir)
    tests = [t for t in memory.list_tests("active", build=settings.build) if not test_ids or t.id in test_ids]
    tests.sort(key=lambda t: (0 if "auth" in t.tags else 1, "negative" in t.tags, t.id))
    report = RunReport(run_id=run_id, app_url=settings.base_url, label=label)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.headless)
        try:
            auth_state = await ensure_auth(browser, ctx) if any(t.requires_login for t in tests) else None
            for test in tests:
                try:
                    res = await run_test(test, ctx, browser, auth_state, update=update)
                except Exception as exc:
                    # run_test itself crashed (e.g. browser context gone); record INFRA and continue.
                    res = TestResult(
                        test_id=test.id, test_name=test.name, test_version=test.version,
                        status="error",
                        verdict=Verdict(
                            category="INFRA", confidence=0.9,
                            rationale=f"run_test raised an unexpected exception: {exc}"[:300],
                        ),
                    )
                report.results.append(res)
                if on_result:
                    on_result(res)
        finally:
            await browser.close()
    report.totals = compute_totals(report, ctx.naive_tokens)
    report.learned = {"stability": {k: round(v, 3) for k, v in memory.stability().items()}}
    memory.save_run(report)
    return report
