"""Deterministic, zero-LLM exploration of a web app into a state atlas.

A **state** is a page condition identified by `structural_signature` (role+name of the
visible interactive elements + normalized url path). A fresh, authenticated browser page is
opened at a state's `entry_url`, its `path` of fills/clicks is replayed, and candidate
actions (links, buttons, progress-buttons-with-form-fills) are tried one at a time in their
own fresh page. Effects are captured with `EffectRecorder`:

    url path change             -> new state (empty path, entry_url = new url)
    same url, new signature     -> new state (path = parent path + fills + click)
    signature of a known state  -> transition to that existing state
    nothing changed             -> self transition only when a mutating API call happened

Public API:
    async def explore(settings, max_states=25, max_actions=90, log=print) -> dict
    def input_kind(el) -> str
    def heuristic_value(el, creds, unique="${unique}") -> str | None
    def pick_option(options: list[dict]) -> str | None
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import urlsplit

from playwright.async_api import Browser, async_playwright

from argus.browser.effects import EffectRecorder, wait_for_settle
from argus.browser.snapshot import element_handle, normalize_path, take_snapshot
from argus.config import Settings
from argus.healing.resolver import resolve
from argus.healing.similarity import DEFAULT_WEIGHTS, effective_weights
from argus.memory.store import Memory
from argus.models import ElementInfo, Fingerprint, PageSnapshot, Step
from argus.runner.runner import MUTATING, RunContext, _act, ensure_auth

_PROGRESS_RE = re.compile(
    r"\b(next|continue|save|submit|launch|create|start|apply|confirm|sign ?in|log ?in|search)\b",
    re.I,
)
_DENY_RE = re.compile(
    r"\b(delete|remove|log ?out|sign ?out|abort|cancel|pay|purchase|export|download)\b",
    re.I,
)
_PLACEHOLDER_RE = re.compile(r"\b(select|choose|pick|placeholder)\b", re.I)
_PATH_ENTRY_RE = re.compile(r"^(?:/login|/__admin|/api/)$")


@dataclass
class _Candidate:
    fp: Fingerprint
    action: str
    intent: str
    is_progress: bool
    href: str = ""


@dataclass
class _Fill:
    action: str
    el: ElementInfo
    template: Optional[str]  # value template, e.g. "Argus ${unique}"; None for radio clicks
    intent: str


# --------------------------------------------------------------------------------------
# heuristic form filling (pure, unit-testable)
# --------------------------------------------------------------------------------------

def input_kind(el: ElementInfo) -> str:
    """Classify a form control into a family with a matching heuristic filler."""
    t = (el.attrs.get("type") or "text").lower()
    if el.role == "radio" or t == "radio":
        return "radio"
    if el.role == "checkbox" or t == "checkbox":
        return "checkbox"
    if el.role == "searchbox" or t == "search":
        return "search"
    if el.role == "combobox" or el.tag == "select":
        return "select"
    if el.tag == "textarea":
        return "textarea"
    if t == "email":
        return "email"
    if t == "password":
        return "password"
    if t == "tel":
        return "tel"
    if t in ("url", "uri"):
        return "url"
    if t in ("date", "datetime-local", "month", "time", "week"):
        return "date"
    if t in ("number", "range"):
        return "number"
    return "text"


def pick_option(options: list[dict]) -> Optional[str]:
    """First option with a non-empty value whose label is not a placeholder."""
    for o in options:
        value = (o.get("value") or "").strip()
        if not value:
            continue
        label = (o.get("label") or value or "").strip()
        if _PLACEHOLDER_RE.search(label):
            continue
        return value
    return None


def heuristic_value(el: ElementInfo, creds: dict[str, str], unique: str = "${unique}") -> Optional[str]:
    """A valid-looking value for a control, or None when the caller handles it differently."""
    kind = input_kind(el)
    if kind == "email":
        return "qa.bot@example.com"
    if kind == "password":
        return creds.get("password") or "changeme123"
    if kind == "tel":
        return "+919876543210"
    if kind == "url":
        return "https://example.com"
    if kind == "date":
        return (date.today() + timedelta(days=1)).isoformat()
    if kind == "number":
        try:
            lo, hi = float(el.attrs.get("min")), float(el.attrs.get("max"))
        except (TypeError, ValueError):
            return "5"
        mid = (lo + hi) / 2.0
        return str(int(mid)) if mid.is_integer() else str(round(mid, 1))
    if kind == "textarea":
        return "Automated exploration note"
    if kind == "text":
        hay = " ".join([el.name, el.label, el.attrs.get("placeholder", ""), el.attrs.get("name", "")])
        if re.search(r"name", hay, re.I):
            return "Argus " + unique
        return "Argus test"
    return None


# --------------------------------------------------------------------------------------
# explorer
# --------------------------------------------------------------------------------------

class _Explorer:
    """Stateful BFS crawler. One instance per `explore()` call."""

    def __init__(self, settings: Settings, max_states: int, max_actions: int, log, memory: Memory):
        self.settings = settings
        self.max_states = max_states
        self.max_actions = max_actions
        self.log = log
        self.memory = memory
        self.weights = effective_weights(dict(DEFAULT_WEIGHTS), memory.stability())
        self.origin = self._origin(settings.base_url)

        self.states: dict[str, dict] = {}
        self.transitions: list[dict] = []
        self.by_sig: dict[str, str] = {}
        self.visited_paths: set[str] = set()
        self.attempted: dict[str, set[str]] = {}
        self._pending: list[str] = []
        self._next_id = 0
        self.actions = 0
        self.login_sid: Optional[str] = None
        self.wctx: Optional[RunContext] = None
        self._last_rec: Optional[EffectRecorder] = None

    @staticmethod
    def _origin(base_url: str) -> str:
        parts = urlsplit(base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def states_left(self) -> bool:
        return len(self.states) < self.max_states

    @property
    def actions_left(self) -> bool:
        return self.actions < self.max_actions

    # -- registration ---------------------------------------------------------

    def _register(self, snap: PageSnapshot, entry_url: str, path: list[dict]) -> str:
        sig = snap.signature or f"nosig-{len(self.states)}"
        if sig in self.by_sig:
            return self.by_sig[sig]
        sid = f"S{self._next_id}"
        self._next_id += 1
        elements: list[dict] = []
        for i, e in enumerate(snap.elements):
            if not e.interactive:
                continue
            fp = Fingerprint.from_element(e)
            elements.append({"id": f"{sid}.e{i}", "fp": fp.model_dump(mode="json"),
                             "desc": fp.describe(), "enabled": e.enabled})
            if len(elements) >= 60:
                break
        self.states[sid] = {
            "id": sid,
            "url": normalize_path(entry_url),
            "entry_url": entry_url,
            "path": path,
            "signature": sig,
            "title": snap.title or "",
            "headings": list(snap.headings[:5]),
            "alerts": list(snap.alerts[:5]),
            "elements": elements,
            "forms": [],
        }
        self.by_sig[sig] = sid
        self.visited_paths.add(normalize_path(entry_url))
        return sid

    def _transition(self, frm: str, to: str, cand: _Candidate, network: list[str]) -> None:
        self.transitions.append({
            "from": frm, "to": to, "action": cand.action,
            "target": cand.fp.model_dump(mode="json"),
            "intent": cand.intent, "network": network,
        })

    @staticmethod
    def _fmt_network(network) -> list[str]:
        return [f"{c.method} {c.path} {c.status}" for c in network if c.method.upper() in MUTATING]

    @staticmethod
    def _fill_step(f: _Fill) -> dict:
        return {
            "action": f.action,
            "target": Fingerprint.from_element(f.el).model_dump(mode="json"),
            "value": f.template,
            "intent": f.intent,
        }

    # -- page setup -----------------------------------------------------------

    async def _open_page(self, context, st: dict) -> Optional[Any]:
        page = await context.new_page()
        rec = EffectRecorder(page)
        try:
            await page.goto(self.wctx.url(st["entry_url"]), wait_until="domcontentloaded", timeout=15000)
            await wait_for_settle(page, rec)
        except Exception:
            await page.close()
            return None
        self._last_rec = rec
        if not await self._replay(page, rec, st):
            await page.close()
            return None
        return page

    async def _replay(self, page, rec: EffectRecorder, st: dict) -> bool:
        for i, s in enumerate(st["path"]):
            snap = await take_snapshot(page)
            step = Step(id=f"r{i + 1}", intent=s["intent"], action=s["action"],
                        target=Fingerprint(**s["target"]) if s.get("target") else None,
                        value=s.get("value"))
            res = await resolve(snap, step, self.weights, None, self.settings, allow_llm=False)
            if not res.found:
                return False
            handle = await element_handle(page, res.element.ref)
            if handle is None:
                return False
            value = self.wctx.value_for(step) if step.action in ("fill", "select", "press") else None
            await _act(page, handle, step.action, value)
            await wait_for_settle(page, rec)
        return True

    # -- discovery ------------------------------------------------------------

    async def _capture_login(self, browser: Browser) -> None:
        creds = self.settings.credentials
        login_path = creds.get("login_path", "/login")
        context = await browser.new_context(viewport=self.settings.viewport)
        page = await context.new_page()
        try:
            await page.goto(self.wctx.url(login_path), wait_until="domcontentloaded")
            await wait_for_settle(page, EffectRecorder(page))
            snap = await take_snapshot(page)
            self.login_sid = self._register(snap, page.url, [])
            self.log(f"[explore] login page: {self.login_sid} {normalize_path(page.url)}")
        finally:
            await context.close()

    def _link_ok(self, href: str) -> bool:
        href = (href or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            return False
        if href.startswith("http") and not href.startswith(self.origin):
            return False
        path = href.split("?")[0].split("#")[0].lower()
        if any(tok in path for tok in ("/logout", "/__admin", "/api/")):
            return False
        if path.endswith((".csv", ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                          ".xlsx", ".docx", ".exe", ".dmg", ".tar", ".gz")):
            return False
        return True

    def _candidates(self, snap: PageSnapshot, st: dict) -> list[_Candidate]:
        out: list[_Candidate] = []
        seen_href: set[str] = set()
        for e in snap.elements:
            if not (e.interactive and e.enabled and e.visible):
                continue
            if e.role == "link" or (e.tag == "a" and e.attrs.get("href")):
                href = e.attrs.get("href", "")
                if not self._link_ok(href):
                    continue
                hp = normalize_path(href)
                if hp in self.visited_paths or hp in seen_href:
                    continue
                seen_href.add(hp)
                out.append(_Candidate(fingerprint_from(e), "click", f"click '{e.name or e.text}'",
                                      False, href=hp))
            elif e.role == "button":
                label = e.name or e.text
                if _DENY_RE.search(label):
                    continue
                is_progress = bool(_PROGRESS_RE.search(label) or e.attrs.get("type") == "submit")
                out.append(_Candidate(fingerprint_from(e), "click", f"click '{label}'", is_progress))
        return out

    # -- filling --------------------------------------------------------------

    async def _progress_fills(self, page, snap: PageSnapshot) -> list[_Fill]:
        fills: list[_Fill] = []
        seen_radio: set[str] = set()
        for e in snap.elements:
            if not (e.interactive and e.enabled and e.visible):
                continue
            kind = input_kind(e)
            if kind in ("search", "checkbox"):
                continue
            if kind == "radio":
                group = e.attrs.get("name") or e.name
                if group in seen_radio:
                    continue
                seen_radio.add(group)
                checked = any(x.role == "radio" and (x.attrs.get("name") or x.name) == group and x.checked
                              for x in snap.elements)
                if checked:
                    continue
                fills.append(_Fill("click", e, None, f"select '{label_from(e)}'"))
            elif kind == "select":
                handle = await element_handle(page, e.ref)
                if handle is None:
                    continue
                try:
                    current = await handle.input_value()
                except Exception:
                    current = ""
                if current:
                    continue
                options = await handle.evaluate(
                    "el => Array.from(el.options).map(o => ({value: o.value, label: o.text}))"
                ) or []
                value = pick_option(options)
                if value is None:
                    continue
                fills.append(_Fill("select", e, str(value), f"select '{value}' in '{label_from(e)}'"))
            else:
                if "readonly" in e.attrs or not e.editable:
                    continue
                if "value" in e.attrs:
                    continue
                if kind == "textarea" and e.text:
                    continue
                value = heuristic_value(e, self.settings.credentials)
                if value is None:
                    continue
                fills.append(_Fill("fill", e, value, f"fill '{label_from(e)}'"))
        return fills

    async def _apply_fill(self, page, f: _Fill) -> None:
        handle = await element_handle(page, f.el.ref)
        if handle is None:
            return
        value = self.wctx.value_for(Step(id="f", intent=f.intent, action=f.action,
                                         target=fingerprint_from(f.el), value=f.template)) \
            if f.action in ("fill", "select") else None
        try:
            await _act(page, handle, f.action, value)
        except Exception:
            if f.action == "click":
                await handle.evaluate("el => el.click()")
            else:
                self.log(f"[explore] fill {f.action} '{f.intent}' failed")

    # -- one action -----------------------------------------------------------

    async def _try_action(self, context, sid: str, st: dict, cand: _Candidate) -> None:
        key = f"{cand.action}:{cand.intent}"
        if key in self.attempted.setdefault(sid, set()):
            return
        self.attempted[sid].add(key)
        page = await self._open_page(context, st)
        if page is None:
            return
        try:
            snap = await take_snapshot(page)
            step = Step(id="x", intent=cand.intent, action=cand.action, target=cand.fp)
            res = await resolve(snap, step, self.weights, None, self.settings, allow_llm=False)
            if not res.found:
                return
            fills: list[_Fill] = []
            if cand.is_progress:
                fills = await self._progress_fills(page, snap)
                for f in fills:
                    await self._apply_fill(page, f)
                    await wait_for_settle(page, self._last_rec)
            rec = EffectRecorder(page)
            await rec.begin()
            handle = await element_handle(page, res.element.ref)
            if handle is None:
                return
            value = self.wctx.value_for(step) if step.action in ("fill", "select", "press") else None
            await _act(page, handle, step.action, value)
            eff = await rec.end()
            self.actions += 1
            self.log(f"[explore] {sid} -> {cand.intent} ({len(eff.network)} net calls)")
            await self._record(sid, st, cand, fills, eff, page)
        finally:
            await page.close()

    async def _record(self, sid: str, st: dict, cand: _Candidate, fills: list[_Fill],
                      eff, page) -> None:
        url_path_changed = normalize_path(eff.url_after) != normalize_path(eff.url_before)
        sig = ""
        snap = None
        try:
            snap = await take_snapshot(page)
            sig = snap.signature
        except Exception:
            pass
        net = self._fmt_network(eff.network)
        do_step = {"action": cand.action, "target": cand.fp.model_dump(mode="json"),
                   "value": None, "intent": cand.intent}
        fill_steps = [self._fill_step(f) for f in fills]

        if url_path_changed:
            target = self.by_sig.get(sig) if sig else None
            if target is None:
                if not self.states_left or not snap:
                    return
                target = self._register(snap, eff.url_after, [])
                self.log(f"[explore] new state: {target} {'/'.join([sid, target])} "
                         f"{st['url']} -> {normalize_path(eff.url_after)} ({snap.title})")
                self._pending.append(target)
            self._transition(sid, target, cand, net)
        elif sig and sig in self.by_sig:
            self._transition(sid, self.by_sig[sig], cand, net)
        else:
            unchanged = sig and sig == st.get("signature")
            if not unchanged:
                if not self.states_left:
                    return
                target = self._register(snap, st["entry_url"], st["path"] + fill_steps + [do_step])
                self.log(f"[explore] new state: {target} ({snap.title}) via {cand.intent}")
                self._pending.append(target)
                self._transition(sid, target, cand, net)
            elif net or eff.page_errors:
                self._transition(sid, sid, cand, net)

    # -- main loop ------------------------------------------------------------

    async def _run(self, browser: Browser, auth_state: Optional[str]) -> None:
        while self._pending and self.states_left and self.actions_left:
            sid = self._pending.pop(0)
            st = self.states[sid]
            self.log(f"[explore] expanding {sid} {st['url']} ({st['title']})")
            context = await browser.new_context(viewport=self.settings.viewport,
                                                storage_state=auth_state)
            try:
                page = await self._open_page(context, st)
                if page is None:
                    continue
                try:
                    snap = await take_snapshot(page)
                    for cand in self._candidates(snap, st):
                        if not (self.states_left and self.actions_left):
                            break
                        await self._try_action(context, sid, st, cand)
                finally:
                    await page.close()
            finally:
                await context.close()

    def finish(self) -> dict:
        start = self.states.get("S1", {})
        return {
            "meta": {
                "app": self.settings.base_url,
                "start_url": start.get("url", "/"),
                "start_state": "S1" if "S1" in self.states else None,
                "login_state": self.login_sid,
                "counts": {"states": len(self.states), "actions": self.actions,
                           "transitions": len(self.transitions)},
            },
            "states": self.states,
            "transitions": self.transitions,
            "login": self.states.get(self.login_sid) if self.login_sid else None,
        }


def fingerprint_from(e: ElementInfo) -> Fingerprint:
    return Fingerprint.from_element(e)


def label_from(e: ElementInfo) -> str:
    return e.name or e.text or e.label or e.attrs.get("placeholder", "") or e.attrs.get("id", "")


async def explore(settings: Settings, max_states: int = 25, max_actions: int = 90,
                  log=print) -> dict:
    """Crawl the app at ``settings.base_url`` into a state atlas and persist it."""
    if max_states < 2 or max_actions < 1:
        raise ValueError("max_states >= 2 and max_actions >= 1 required")
    memory = Memory(settings.home)
    builder = _Explorer(settings, max_states, max_actions, log, memory)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.headless)
        try:
            run_id, run_dir = memory.new_run_dir()
            builder.wctx = RunContext(settings, memory, None, run_id, run_dir)
            if settings.credentials.get("user"):
                await builder._capture_login(browser)
            auth_state = await ensure_auth(browser, builder.wctx)
            start_path = settings.base_url.rstrip("/") + "/"
            context = await browser.new_context(viewport=settings.viewport,
                                                storage_state=auth_state)
            page = await context.new_page()
            try:
                await page.goto(start_path, wait_until="domcontentloaded", timeout=15000)
                await wait_for_settle(page, EffectRecorder(page))
                snap = await take_snapshot(page)
                sid = builder._register(snap, page.url, [])
                log(f"[explore] start state: {sid} {normalize_path(page.url)} ({snap.title})")
                builder._pending.append(sid)
            finally:
                await context.close()
            await builder._run(browser, auth_state)
        finally:
            await browser.close()
    atlas = builder.finish()
    memory.save_atlas(atlas)
    log(f"[explore] done: {atlas['meta']['counts']}")
    return atlas