"""Author tests from semantic step descriptions (no selectors, no LLM).

Each step says *what* to act on the way a human would ("the textbox named Mission name"); the author
resolves it on the live page once, freezes a multi-attribute fingerprint, and records the baseline contract.
Used for hand-written suites and as the zero-LLM fallback of the generator.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from playwright.async_api import async_playwright

from argus.browser.effects import EffectRecorder, wait_for_settle
from argus.browser.snapshot import take_snapshot
from argus.memory.store import Memory
from argus.models import Assertion, ElementInfo, Fingerprint, PageSnapshot, Step, TestChange, TestSpec
from argus.runner.runner import RunContext, _act, ensure_auth, record_baseline


def find_element(snap: PageSnapshot, find: dict[str, Any]) -> Optional[ElementInfo]:
    """Match {role?, name? (substring, case-insensitive), context?, type?} against a snapshot."""
    role = find.get("role")
    name = (find.get("name") or "").lower()
    ctx = (find.get("context") or "").lower()
    typ = find.get("type")
    best = None
    for e in snap.elements:
        if not e.interactive:
            continue
        if role and e.role != role:
            continue
        if typ and e.attrs.get("type") != typ:
            continue
        label = " ".join([e.name, e.text, e.label]).lower()
        if name and name not in label:
            continue
        if ctx and ctx not in e.context.lower():
            continue
        exact = (e.name or e.text).strip().lower() == name
        if best is None or (exact and not best[1]):
            best = (e, exact)
    return best[0] if best else None


def spec_from_dict(d: dict[str, Any]) -> TestSpec:
    steps = []
    for i, s in enumerate(d["steps"], 1):
        steps.append(Step(id=f"s{i}", intent=s["intent"], action=s["action"], value=s.get("value"),
                          save_as=s.get("save_as"), optional=s.get("optional", False), visual=s.get("visual_mark"),
                          target=Fingerprint(**s["fp"]) if s.get("fp") else None))
    return TestSpec(id=d["id"], name=d["name"], goal=d["goal"], tags=d.get("tags", []), start_url=d["start_url"],
                    steps=steps, oracles=[Assertion(**o) for o in d.get("oracles", [])],
                    requires_login=d.get("requires_login", True), origin=d.get("origin", "manual"))


async def author(settings: Any, specs: list[dict[str, Any]], log=print) -> list[TestSpec]:
    """Resolve each semantic step on the live app, then freeze the baseline via record_baseline."""
    memory = Memory(settings.home)
    run_id, run_dir = memory.new_run_dir()
    ctx = RunContext(settings, memory, None, run_id, run_dir)
    saved: list[TestSpec] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.headless)
        auth = await ensure_auth(browser, ctx)
        for d in specs:
            context = await browser.new_context(viewport=settings.viewport,
                                                storage_state=auth if d.get("requires_login", True) else None)
            page = await context.new_page()
            rec = EffectRecorder(page)
            await page.goto(ctx.url(d["start_url"]), wait_until="domcontentloaded")
            await wait_for_settle(page, rec)
            ok = True
            for s in d["steps"]:
                if s["action"] == "goto":
                    await page.goto(ctx.url(s["value"]), wait_until="domcontentloaded")
                    await wait_for_settle(page, rec)
                    continue
                if s.get("visual"):
                    from argus.vision.marks import capture_mark
                    v = s["visual"]
                    box = await page.locator(v["canvas"]).bounding_box()
                    x, y = v["at"][0] * box["width"], v["at"][1] * box["height"]
                    mark = await capture_mark(page, v["canvas"], x, y, hint=v.get("hint", ""))
                    s["visual_mark"] = mark.model_dump()
                    await rec.begin()
                    await page.mouse.click(box["x"] + x, box["y"] + y)
                    await rec.end()
                    continue
                # Steps that act on the page without a DOM target
                if s["action"] == "wait":
                    await page.wait_for_timeout(int(s.get("value") or 500))
                    continue
                if s["action"] == "press" and not s.get("find"):
                    await page.keyboard.press(s.get("value") or "Enter")
                    continue
                snap = await take_snapshot(page)
                el = find_element(snap, s["find"])
                if el is None:
                    log(f"  ! {d['id']}: cannot find {s['find']} on {snap.url}")
                    ok = False
                    break
                s["fp"] = Fingerprint.from_element(el).model_dump()
                value = ctx.value_for(Step(id="x", intent="", action=s["action"], value=s.get("value")))
                handle = await page.evaluate_handle(f"() => window.__argus.refs[{int(el.ref[1:])}]")
                await rec.begin()
                await _act(page, handle.as_element(), s["action"], value)
                await rec.end()
            if ok:
                # oracles that point at an element ("find") get a fingerprint from the final page
                snap = await take_snapshot(page)
                for o in d.get("oracles", []):
                    find = o.get("params", {}).pop("find", None)
                    if find:
                        el = find_element(snap, find)
                        if el is None:
                            log(f"  ! {d['id']}: oracle target {find} not found")
                            ok = False
                        else:
                            o["params"]["fingerprint"] = Fingerprint.from_element(el).model_dump()
            await context.close()
            if not ok:
                continue
            spec = spec_from_dict(d)
            frozen = await record_baseline(spec, ctx, browser, auth)
            if frozen is None:
                log(f"  ! {d['id']}: baseline run failed")
                continue
            saved.append(memory.save_test(frozen, TestChange(version=1, kind="created",
                                                             summary=f"Authored ({frozen.origin}); baseline recorded"),
                                          build=getattr(settings, "build", "")))
            log(f"  + {frozen.id}: {len(frozen.steps)} steps, {len(frozen.oracles)} oracles")
            dropped = [o.description or o.kind for o in spec.oracles if o not in frozen.oracles]
            if dropped:
                log(f"    ! oracle(s) did not hold on the baseline and were dropped: {dropped}")
        await browser.close()
    return saved


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]
