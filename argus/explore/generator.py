"""Turn an exploration atlas into a regression suite with at most one LLM call.

`propose` makes a single `smart` call asking for raw test dicts; `to_spec` compiles each
raw dict into a `TestSpec` deterministically (fingerprint lookup, path pre-pending,
oracle whitelisting, negative tagging). `generate` orchestrates: propose -> compile with
`record_baseline` -> save, always appending a deterministic `login` test, and falling back
to deterministic journeys from the atlas when no model is available.

Public API:
    def atlas_digest(atlas, max_chars=24000) -> str
    async def propose(settings, atlas, llm) -> list[dict]
    def to_spec(raw, atlas, idx) -> TestSpec | None
    async def generate(settings, atlas=None, max_tests=8, log=print) -> list[TestSpec]
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import async_playwright

from argus.config import Settings
from argus.explore.explorer import explore
from argus.llm import prompts as P
from argus.memory.store import Memory
from argus.models import Assertion, Fingerprint, Step, TestChange, TestSpec
from argus.runner.runner import RunContext, ensure_auth, record_baseline

_LOGIN_WORDS = re.compile(r"\b(log ?in|sign ?in|submit|continue)\b", re.I)
_NEG_RE = re.compile(
    r"\b(negative|reject|rejected|invalid|duplicate|must not|should not|cannot|can't|"
    r"too (high|low)|exceed|exceeds|unauthorized|forbidden)\b",
    re.I,
)
_ORACLE_KINDS = {
    "text_visible", "text_absent", "url_matches", "network_called", "network_absent",
    "no_page_errors", "element_visible", "value_equals", "element_state",
}
_ALLOWED_ACTIONS = {"click", "fill", "select", "check", "uncheck", "press"}
_SMOKE_PATHS = ("/dashboard", "/missions", "/logs", "/settings", "/analytics")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_LAUNCH_RE = re.compile(r"\b(launch|create|submit|start)\b", re.I)


def _path_of(url: str) -> str:
    """Raw path of a (possibly absolute) url, without id-normalization."""
    if not url:
        return "/"
    path = url.split("://", 1)[-1]
    path = path.split("?", 1)[0].split("#", 1)[0]
    if "/" in path:
        path = "/" + path.split("/", 1)[1]
    else:
        path = ""
    return path.rstrip("/") or "/"


def _slug(text: str) -> str:
    return re.sub(_SLUG_RE, "-", text.lower()).strip("-")[:60] or "test"


def _crop_fences(text: str) -> str:
    """Unwrap a markdown code fence (```json ... ```) around a JSON payload."""
    t = (text or "").strip()
    m = re.search(r"`{3,}[a-zA-Z0-9_-]*\s*(.*?)\s*`{3,}", t, re.S)
    return m.group(1) if m else t


def _is_launch(el: dict) -> bool:
    fp = el.get("fp") or {}
    label = " ".join(str(fp.get(k) or "") for k in ("name", "text", "label"))
    return bool(_LAUNCH_RE.search(label)) and bool(el.get("enabled", True))


def _pstep(ps: dict, id: str) -> Step:
    return Step(id=id, intent=ps.get("intent"), action=ps.get("action"),
                target=Fingerprint(**ps["target"]) if ps.get("target") else None,
                value=ps.get("value"))


# --------------------------------------------------------------------------------------
# digest
# --------------------------------------------------------------------------------------

def atlas_digest(atlas: dict, max_chars: int = 24000) -> str:
    """Compact, LLM-friendly text view of the atlas."""
    lines: list[str] = []
    for sid, st in atlas.get("states", {}).items():
        path = " · ".join(str(s.get("intent") or s.get("action")) for s in st.get("path") or []) or "none"
        lines.append(f'{sid} {st.get("url")} "{st.get("title")}" path: [{path}]')
        for el in st.get("elements") or []:
            flags = []
            if not el.get("enabled", True):
                flags.append("disabled")
            if "required" in ((el.get("fp") or {}).get("attrs") or {}):
                flags.append("required")
            lines.append(f'{el["id"]} {el.get("desc")}' + (f" {{{', '.join(flags)}}}" if flags else ""))
        if st.get("alerts"):
            lines.append("    alerts: " + " | ".join(str(a) for a in st["alerts"][:3]))
    for t in atlas.get("transitions") or []:
        net = ", ".join(str(n) for n in t.get("network") or [])
        line = f'{t.get("from")} --{t.get("action")} "{t.get("intent")}"--> {t.get("to")}'
        if net:
            line += f" [{net}]"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 20)].rstrip() + "\n… (truncated)"
    return text


# --------------------------------------------------------------------------------------
# LLM proposal -> spec
# --------------------------------------------------------------------------------------

async def propose(settings: Settings, atlas: dict, llm, log=print) -> list[dict]:
    """One `smart` call proposing the regression suite; returns raw test dicts.

    If the model returns a fragment or an object without a `tests` wrapper (some free
    providers truncate long completions), retry up to 2 extra times with a corrective
    hint appended — each retry has its own cache key, so it only costs a call when a
    previous attempt actually was malformed.
    """
    base = P.GENERATE_USER.format(
        product=settings.product_context() or "(no product context provided)",
        atlas=atlas_digest(atlas),
    )
    _CORRECTION = (
        "\n\nYour previous response was NOT a single {'tests': [...]} JSON object. "
        "Return exactly one valid JSON object: {\"tests\": [<test objects>]} and nothing else."
    )
    user = base
    for attempt in range(3):
        data = await llm.json(tier="smart", purpose="generate",
                              system=P.GENERATE_SYSTEM, user=user, max_tokens=2600)
        if isinstance(data, str):
            import json as _json
            try:
                data = _json.loads(_crop_fences(data))
            except (TypeError, ValueError):
                data = None
            if isinstance(data, str):  # double-encoded JSON string
                try:
                    data = _json.loads(_crop_fences(data))
                except (TypeError, ValueError):
                    data = None
        tests = [t for t in (data or {}).get("tests") or [] if isinstance(t, dict)]
        if tests:
            return tests
        if attempt < 2:
            log(f"[generate] proposal attempt {attempt + 1} had no 'tests' wrapper; retrying")
            user = base + _CORRECTION
        else:
            log("[generate] proposal failed: no usable 'tests' wrapper after 3 attempts")
    return []


def to_spec(raw: dict, atlas: dict, idx: int) -> Optional[TestSpec]:
    """Compile one raw LLM test dict into a TestSpec, or None when unusable."""
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    states = atlas.get("states", {})
    el_map: dict[str, Fingerprint] = {}
    for st in states.values():
        for el in st.get("elements") or []:
            try:
                el_map[el["id"]] = Fingerprint(**el["fp"])
            except (KeyError, TypeError, ValueError):
                continue

    mapped: list[dict] = []
    for sr in raw.get("steps") or []:
        el_id = str(sr.get("el") or "")
        fp = el_map.get(el_id)
        action = str(sr.get("action") or "").lower()
        if fp is None or action not in _ALLOWED_ACTIONS or el_id.split(".")[0] not in states:
            continue
        value = sr.get("value")
        mapped.append({
            "el_id": el_id, "fp": fp, "action": action,
            "value": str(value) if value is not None else None,
            "intent": str(sr.get("intent") or "").strip(),
            "save_as": sr.get("save_as") or None,
        })
    if not mapped:
        return None

    first_sid = mapped[0]["el_id"].split(".")[0]
    first_st = states.get(first_sid)
    entry = first_st.get("entry_url") if first_st else ""
    start_url = _path_of(entry) if entry else str(atlas.get("meta", {}).get("start_url") or "/")

    steps: list[Step] = [_pstep(ps, f"s{i + 1}") for i, ps in enumerate(first_st.get("path") or [])]
    for i, m in enumerate(mapped, len(steps) + 1):
        steps.append(Step(id=f"s{i}", intent=m["intent"] or f"{m['action']} {m['fp'].describe()}",
                          action=m["action"], target=m["fp"], value=m["value"], save_as=m["save_as"]))

    tags = [str(t) for t in raw.get("tags") or [] if str(t).strip()]
    if _NEG_RE.search(name) or _NEG_RE.search(" ".join(tags)):
        tags.append("negative")

    oracles = []
    for o in raw.get("oracles") or []:
        kind = str(o.get("kind") or "")
        if kind not in _ORACLE_KINDS:
            continue
        oracles.append(Assertion(kind=kind, params=dict(o.get("params") or {}),
                                 description=str(o.get("description") or kind),
                                 rule_ref=o.get("rule_ref")))

    return TestSpec(id=_slug(name), name=name, goal=str(raw.get("goal") or name),
                    tags=tags, start_url=start_url, steps=steps, oracles=oracles,
                    requires_login=bool(raw.get("requires_login", True)),
                    origin="generated")


# --------------------------------------------------------------------------------------
# deterministic builders
# --------------------------------------------------------------------------------------

def _login_spec(atlas: dict) -> Optional[TestSpec]:
    login = atlas.get("login") or {}
    email = pw = submit = None
    for el in login.get("elements") or []:
        fp = Fingerprint(**el["fp"])
        if fp.attrs.get("type") == "password" and pw is None:
            pw = fp
        elif fp.attrs.get("type") == "email" and email is None:
            email = fp
        elif fp.role == "button" and submit is None and _LOGIN_WORDS.search(
                " ".join([fp.name, fp.text, fp.label])):
            submit = fp
    if not (email and pw and submit):
        return None
    steps = [
        Step(id="s1", action="fill", target=email, value="${creds.user}",
             intent="Enter the pilot email"),
        Step(id="s2", action="fill", target=pw, value="${creds.password}",
             intent="Enter the password"),
        Step(id="s3", action="click", target=submit, intent="Click 'Log in'"),
    ]
    return TestSpec(
        id="login", name="Log in with demo credentials",
        goal="Signing in with valid credentials opens the dashboard",
        tags=["auth"], start_url=_path_of(login.get("entry_url") or "/login"),
        steps=steps,
        oracles=[Assertion(kind="url_matches", params={"pattern": "/dashboard"},
                           description="Landed on the dashboard", rule_ref="R5")],
        requires_login=False,
    )


def _deterministic_tests(atlas: dict) -> list[TestSpec]:
    """Smoke tests per top-level page + the wizard launch journey found in the atlas."""
    tests: list[TestSpec] = []
    for path in _SMOKE_PATHS:
        st = next((s for s in atlas.get("states", {}).values()
                   if s.get("url") == path and not s.get("path")), None)
        if not st:
            continue
        heading = next((h for h in st.get("headings") or [] if str(h).strip()), None)
        if heading is None:
            continue
        tests.append(TestSpec(
            id="visit_" + (re.sub(_SLUG_RE, "", path.strip("/")) or "home"),
            name=f"Open {heading}", goal=f"The {heading} page opens and shows its heading",
            tags=["smoke"], start_url=path, steps=[],
            oracles=[Assertion(kind="text_visible", params={"text": str(heading)},
                               description="Main heading is visible")],
            requires_login=True,
        ))

    launch_state = None
    launch_el = None
    for st in atlas.get("states", {}).values():
        if not st.get("path"):
            continue
        hit = next((el for el in st.get("elements") or [] if _is_launch(el)), None)
        if hit is not None and (launch_state is None or len(st["path"]) > len(launch_state["path"])):
            launch_state, launch_el = st, hit
    if launch_state and launch_el is not None:
        steps = [_pstep(ps, f"s{i + 1}") for i, ps in enumerate(launch_state["path"])]
        fp = Fingerprint(**launch_el["fp"])
        steps.append(Step(id=f"s{len(steps) + 1}", intent="click 'Launch mission'",
                          action="click", target=fp,
                          page_hint=(launch_state.get("headings") or [""])[0]))
        tests.append(TestSpec(
            id="wizard_launch", name="Launch a mission via the wizard",
            goal="A valid mission launch creates a mission and lands on its detail page",
            tags=["critical"], start_url=_path_of(launch_state.get("entry_url") or ""),
            steps=steps,
            oracles=[
                Assertion(kind="network_called",
                          params={"method": "POST", "path": "/api/missions", "status_class": "2xx"},
                          description="Mission is created via the API", rule_ref="R4"),
                Assertion(kind="url_matches", params={"pattern": r"missions/[a-z0-9]+"},
                          description="Redirected to the new mission page"),
            ],
            requires_login=True,
        ))
    return tests


# --------------------------------------------------------------------------------------
# orchestrator
# --------------------------------------------------------------------------------------

async def generate(settings: Settings, atlas: Optional[dict] = None, max_tests: int = 8,
                   log=print) -> list[TestSpec]:
    """Produce and persist a regression suite; always includes a deterministic login test."""
    memory = Memory(settings.home)
    if atlas is None:
        atlas = memory.atlas()
        if not (atlas or {}).get("states"):
            atlas = await explore(settings, log=log)
    login_spec = _login_spec(atlas) if atlas else None

    produced: list[TestSpec] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.headless)
        try:
            llm = None
            if settings.llm_enabled:
                from argus.llm.client import LLMClient
                try:
                    llm = LLMClient(settings)
                except Exception as exc:
                    log(f"[generate] LLM unavailable: {type(exc).__name__}")

            if llm is not None:
                try:
                    raw = await propose(settings, atlas, llm)
                except Exception as exc:
                    log(f"[generate] proposal failed ({type(exc).__name__}); using deterministic tests")
                    raw = []
                seen: set[str] = set()
                specs: list[TestSpec] = []
                for i, r in enumerate(raw[:max_tests]):
                    spec = to_spec(r, atlas, i)
                    if spec is None:
                        continue
                    if spec.id in seen:
                        spec = spec.model_copy(update={"id": f"{spec.id}-{i + 1}"})
                    seen.add(spec.id)
                    specs.append(spec)
                run_id, run_dir = memory.new_run_dir()
                ctx = RunContext(settings, memory, llm, run_id, run_dir)
                auth_state = await ensure_auth(browser, ctx) if any(s.requires_login for s in specs) else None
                for spec in specs:
                    try:
                        done = await record_baseline(spec, ctx, browser, auth_state)
                    except Exception as exc:
                        log(f"[generate] drop '{spec.name}': {type(exc).__name__}")
                        continue
                    if done is None:
                        log(f"[generate] drop '{spec.name}': could not execute end to end")
                        continue
                    done = memory.save_test(done, TestChange(version=1, kind="created",
                        summary=f"Generated {len(done.steps)}-step test with {len(done.oracles)} oracles"))
                    produced.append(done)
                    log(f"[generate] {done.id} '{done.name}' ({len(done.steps)} steps)")

            if not produced:
                for spec in _deterministic_tests(atlas):
                    saved = memory.save_test(spec, TestChange(version=1, kind="created",
                        summary=f"Deterministic test from exploration"))
                    produced.append(saved)
                    log(f"[generate] {saved.id} '{saved.name}' ({len(saved.steps)} steps)")
        finally:
            await browser.close()

    if login_spec is not None:
        login_spec = memory.save_test(login_spec, TestChange(version=1, kind="created",
                                                            summary="Deterministic login test"))
        produced.insert(0, login_spec)
        log(f"[generate] {login_spec.id} 'Log in with demo credentials'")
    return produced