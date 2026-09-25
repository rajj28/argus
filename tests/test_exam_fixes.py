"""Tests for the blind-exam fixes (tasks/exam_fixes.md).

Covers: interrupt dismissal (+ memory + cosmetic triage), identical-sibling tie-breaks
(context/ordinal), Shadow-DOM-safe css/xpath builders, sessionStorage auth capture and
re-injection, and the `sum_equals` arithmetic oracle. Local file:// fixtures only:
no servers, no network, no LLM.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from argus.browser.effects import EffectRecorder
from argus.browser.snapshot import take_snapshot
from argus.config import Settings
from argus.healing.resolver import _sibling_tiebreak, resolve
from argus.healing.similarity import DEFAULT_WEIGHTS
from argus.memory.store import Memory
from argus.models import (Assertion, BBox, ElementInfo, Fingerprint, Observation, PageSnapshot,
                          Step, TestResult, TestSpec, Verdict)
from argus.runner.assertions import check, parse_amounts, sum_equals_from_texts
from argus.runner.runner import (RunContext, _blocking_overlay, _dismiss_control, _dismiss_interrupts,
                                 _dismiss_rank, _DISMISS_PRIORITY, _inject_session_storage,
                                 _overlay_is_target, ensure_auth)
from argus.triage.triage import deviation_signature, triage

FIXTURES = Path(__file__).parent / "fixtures"


def _url(name: str) -> str:
    return (FIXTURES / name).as_uri()


@pytest_asyncio.fixture
async def browser():
    """One chromium instance shared across tests (each test opens its own page)."""
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        yield b
        await b.close()


def _ctx(tmp_path: Path, **settings_kw) -> RunContext:
    from argus.runner.runner import RunContext
    defaults = dict(home=tmp_path / ".argus", base_url="http://localhost:1", llm_enabled=False)
    defaults.update(settings_kw)
    settings = Settings(**defaults)
    settings.home.mkdir(parents=True, exist_ok=True)
    memory = Memory(settings.home)
    run_dir = tmp_path / "run"
    (run_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    return RunContext(settings, memory, None, "run-test", run_dir)


async def _open(browser, name: str):
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    await page.goto(_url(name))
    return context, page


def _el(ref: str, name: str = "", role: str = "button", tag: str = "button", context: str = "",
        ordinal: str | None = None, visible: bool = True, interactive: bool = True,
        bbox: BBox | None = None, **attrs: str) -> ElementInfo:
    a = dict(attrs)
    if ordinal is not None:
        a["ordinal"] = ordinal
    return ElementInfo(ref=ref, tag=tag, role=role, name=name, text=name, context=context,
                       visible=visible, interactive=interactive, attrs=a, bbox=bbox)


# ======================================================================================
# Item 3 - Shadow DOM: getCSSPath / getXPath must not crash and must prefix the host path
# ======================================================================================

@pytest.mark.asyncio
async def test_shadow_dom_snapshot_paths(browser):
    """Elements inside an open shadow root get host-prefixed css/xpath instead of a TypeError."""
    context, page = await _open(browser, "shadow_dom.html")
    try:
        snap = await take_snapshot(page)  # pre-fix: TypeError ... toLowerCase in getCSSPath
        btn = next((e for e in snap.elements if e.name == "Enable SMS"), None)
        assert btn is not None, "button inside the shadow root must be collected"
        assert "my-widget" in btn.css, f"css path must be prefixed with the host: {btn.css!r}"
        assert "my-widget" in btn.xpath, f"xpath must be prefixed with the host: {btn.xpath!r}"
        assert btn.attrs.get("ordinal") == "0"
    finally:
        await context.close()


# ======================================================================================
# Item 2 - identical siblings: ordinal at record time + resolver tie-break
# ======================================================================================

@pytest.mark.asyncio
async def test_ordinal_index_among_identical_siblings(browser):
    context, page = await _open(browser, "siblings.html")
    try:
        snap = await take_snapshot(page)
        cancels = [e for e in snap.elements if e.tag == "button" and e.name == "Cancel"]
        assert len(cancels) == 3
        assert [e.attrs.get("ordinal") for e in cancels] == ["0", "1", "2"]
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_resolve_picks_recorded_identical_sibling(browser):
    """A fingerprint recorded on sibling #2 must resolve back to sibling #2 (tier 0/1)."""
    context, page = await _open(browser, "siblings.html")
    try:
        snap = await take_snapshot(page)
        cancels = [e for e in snap.elements if e.tag == "button" and e.name == "Cancel"]
        fp = Fingerprint.from_element(cancels[1])
        assert fp.attrs.get("ordinal") == "1", "ordinal must be stored in Fingerprint.attrs"
        step = Step(id="s1", intent="Click Cancel on the second appointment", action="click", target=fp)
        res = await resolve(snap, step, dict(DEFAULT_WEIGHTS), None, Settings())
        assert res.found, f"expected a match, got: {res.reason}"
        assert res.element.ref == cancels[1].ref
        assert res.tier in (0, 1)
    finally:
        await context.close()


def test_sibling_tiebreak_by_context():
    e1 = _el("e1", "Cancel", context="Mon 10:00 Anita Desai Dr. Rao", ordinal="0")
    e2 = _el("e2", "Cancel", context="Tue 11:30 Vikram Singh Dr. Mehta", ordinal="1")
    ranked = [(e1, 0.96, {}), (e2, 0.95, {})]
    t = Fingerprint(role="button", name="Cancel", context="Tue 11:30 Vikram Singh Dr. Mehta",
                    attrs={"ordinal": "1"})
    out = _sibling_tiebreak(t, ranked, 0.12)
    assert out is not None and out[0].ref == "e2" and "context" in out[2]


def test_sibling_tiebreak_by_ordinal_when_context_indecisive():
    e1 = _el("e1", "Cancel", context="row", ordinal="0")
    e2 = _el("e2", "Cancel", context="row", ordinal="1")
    ranked = [(e1, 0.96, {}), (e2, 0.955, {})]
    t = Fingerprint(role="button", name="Cancel", attrs={"ordinal": "1"})
    out = _sibling_tiebreak(t, ranked, 0.12)
    assert out is not None and out[0].ref == "e2" and "ordinal" in out[2]


def test_sibling_tiebreak_requires_identical_role_name():
    e1 = _el("e1", "Cancel", ordinal="0")
    e2 = _el("e2", "Reschedule", ordinal="0")
    ranked = [(e1, 0.96, {}), (e2, 0.95, {})]
    t = Fingerprint(role="button", name="Cancel", attrs={"ordinal": "0"})
    assert _sibling_tiebreak(t, ranked, 0.12) is None


def test_sibling_tiebreak_indecisive_returns_none():
    e1 = _el("e1", "Cancel", ordinal="0")
    e2 = _el("e2", "Cancel", ordinal="1")
    ranked = [(e1, 0.96, {}), (e2, 0.955, {})]
    t = Fingerprint(role="button", name="Cancel", context="")
    assert _sibling_tiebreak(t, ranked, 0.12) is None


# ======================================================================================
# Item 1 - interrupt handling
# ======================================================================================

def test_dismiss_rank_priority():
    assert _dismiss_rank("Got it!") == 0
    assert _dismiss_rank("×") == _DISMISS_PRIORITY.index("×")
    contains = _dismiss_rank("Close dialog")
    assert contains is not None and contains >= len(_DISMISS_PRIORITY)
    assert _dismiss_rank("Learn more") is None
    assert _dismiss_rank("Next") is None


def _overlay_snap(*elements: ElementInfo, headings: list[str] | None = None) -> PageSnapshot:
    return PageSnapshot(url="http://app/", elements=list(elements), headings=headings or [])


def test_blocking_overlay_detects_dialog_and_aria_modal():
    dlg = _el("e1", "", role="dialog", tag="dialog", bbox=BBox(x=100, y=100, w=400, h=300))
    assert _blocking_overlay(_overlay_snap(dlg)).ref == "e1"
    modal = _el("e2", "", role="div", tag="div", bbox=BBox(x=0, y=0, w=1280, h=800),
                **{"aria-modal": "true"})
    assert _blocking_overlay(_overlay_snap(modal)).ref == "e2"


def test_blocking_overlay_ignores_fixed_app_shell():
    shell = ElementInfo(ref="e1", tag="div", role="div", visible=True,
                        text="Home Dashboard everything on the page",
                        attrs={"data-argus-overlay": "fixed-full-viewport"},
                        bbox=BBox(x=0, y=0, w=1280, h=800))
    snap = _overlay_snap(shell, headings=["Home", "Dashboard"])
    assert _blocking_overlay(snap) is None


def test_dismiss_control_prefers_safest_and_skips_destructive():
    overlay = _el("e0", "", role="dialog", tag="dialog", bbox=BBox(x=100, y=100, w=600, h=400))
    delete = _el("e1", "Delete account", bbox=BBox(x=120, y=400, w=100, h=30))
    close_account = _el("e2", "Close account", bbox=BBox(x=240, y=400, w=100, h=30))
    gotit = _el("e3", "Got it", bbox=BBox(x=360, y=400, w=100, h=30))
    cont = _el("e4", "Continue", bbox=BBox(x=480, y=400, w=100, h=30))
    snap = _overlay_snap(overlay, delete, close_account, gotit, cont)
    assert _dismiss_control(snap, overlay).ref == "e3"


def test_overlay_is_target_guard():
    overlay = _el("e0", "", role="dialog", tag="dialog", bbox=BBox(x=100, y=100, w=600, h=400))
    confirm = _el("e1", "Confirm", bbox=BBox(x=200, y=300, w=100, h=30))
    snap = _overlay_snap(overlay, confirm)
    inside = Step(id="s", intent="confirm", action="click", target=Fingerprint(role="button", name="Confirm"))
    assert _overlay_is_target(snap, overlay, inside)
    outside = Step(id="s", intent="cancel", action="click", target=Fingerprint(role="button", name="Cancel"))
    assert not _overlay_is_target(snap, overlay, outside)


@pytest.mark.asyncio
async def test_dismiss_interrupt_dialog_clicks_got_it(browser, tmp_path):
    ctx = _ctx(tmp_path)
    context, page = await _open(browser, "interrupts.html")
    try:
        rec = EffectRecorder(page)
        snap = await take_snapshot(page)
        assert await _dismiss_interrupts(ctx, page, rec, snap) is True
        assert not await page.evaluate("document.getElementById('whatsnew').open")
        assert [o.kind for o in ctx.interrupts] == ["interrupt_dismissed"]
        detail = ctx.interrupts[0].detail
        assert "What's new in v2.4" in detail and "Got it" in detail
        # remembered in knowledge.json for instant dismissal next run
        kj = json.loads((ctx.settings.home / "knowledge.json").read_text(encoding="utf-8"))
        assert any("what's new" in k for k in kj.get("interrupts", {}))
        # overlay gone -> nothing left to dismiss
        snap2 = await take_snapshot(page)
        assert await _dismiss_interrupts(ctx, page, rec, snap2) is False
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_dismiss_interrupt_escape_fallback(browser, tmp_path):
    ctx = _ctx(tmp_path)
    context, page = await _open(browser, "interrupts_escape.html")
    try:
        rec = EffectRecorder(page)
        snap = await take_snapshot(page)
        assert await _dismiss_interrupts(ctx, page, rec, snap) is True
        assert not await page.evaluate("!!document.getElementById('backdrop')")
        detail = ctx.interrupts[0].detail
        assert "Quick tour" in detail and "Escape" in detail
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_triage_treats_interrupts_as_cosmetic():
    test = TestSpec(id="t1", name="n", goal="g", start_url="/")
    ctx = types.SimpleNamespace(llm=None, memory=None, product_context="", changelog="")

    result = TestResult(test_id="t1", status="passed", verdict=Verdict(category="PASS"))
    result.observations = [Observation(kind="interrupt_dismissed", detail="dismissed interrupt 'X'")]
    assert (await triage(test, result, ctx)).category == "PASS"

    result.observations.append(Observation(kind="locator_healed", tier=1, confidence=0.9, detail="moved"))
    assert (await triage(test, result, ctx)).category == "COSMETIC_DRIFT"


def test_deviation_signature_ignores_interrupts():
    heal = Observation(kind="locator_healed", step_id="s1", detail="button moved")
    intr = Observation(kind="interrupt_dismissed", step_id="s1", detail="dismissed interrupt 'News'")
    assert deviation_signature("t1", [heal]) == deviation_signature("t1", [heal, intr])


def test_new_model_kinds_are_default_compatible():
    Observation(kind="interrupt_dismissed", detail="d")
    Assertion(kind="sum_equals", params={"total_label": "Total"})
    old = TestResult.model_validate_json(
        '{"test_id":"t","status":"passed","verdict":{"category":"PASS"}}')
    assert old.observations == []


# ======================================================================================
# Item 4 - sessionStorage auth
# ======================================================================================

@pytest.mark.asyncio
async def test_ensure_auth_captures_session_storage(browser, tmp_path):
    ctx = _ctx(tmp_path, base_url=FIXTURES.as_uri(),
               credentials={"user": "front_desk", "password": "pw", "login_path": "login_session.html"})
    state = await ensure_auth(browser, ctx)
    assert state and Path(state).exists()
    blob = json.loads((ctx.settings.home / "auth" / "session.json").read_text(encoding="utf-8"))
    assert blob["data"]["auth_token"] == "tok-123"


@pytest.mark.asyncio
async def test_new_context_reinjects_session_storage(browser, tmp_path):
    ctx = _ctx(tmp_path)
    auth_dir = ctx.settings.home / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "session.json").write_text(
        json.dumps({"origin": "", "data": {"auth_token": "tok-xyz"}}), encoding="utf-8")
    context = await browser.new_context(viewport={"width": 800, "height": 600})
    try:
        await _inject_session_storage(context, ctx)
        page = await context.new_page()
        await page.goto(_url("invoice.html"))
        assert await page.evaluate("sessionStorage.getItem('auth_token')") == "tok-xyz"
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_inject_session_storage_missing_file_is_noop(tmp_path):
    ctx = _ctx(tmp_path)
    calls: list[str] = []

    class _FakeContext:
        async def add_init_script(self, script: str) -> None:
            calls.append(script)

    await _inject_session_storage(_FakeContext(), ctx)
    assert calls == []


# ======================================================================================
# Item 5 - sum_equals arithmetic oracle
# ======================================================================================

def test_parse_amounts_generic_currencies():
    assert parse_amounts("₹1,234.50") == [1234.5]
    assert parse_amounts("$45") == [45.0]
    assert parse_amounts("2 x ₹500.00") == [500.0]
    assert parse_amounts("Total: €2,000.00 for 3 items") == [2000.0]
    assert parse_amounts("USD 12.34") == [12.34]
    assert parse_amounts("Rs. 450.00") == [450.0]
    assert parse_amounts("1,234.56") == [1234.56]
    assert parse_amounts("Room 10 on 12.03.2026") == []


def test_sum_equals_from_texts():
    items = ["Specialist consultation ₹500.00", "Medicine ₹230.50"]
    ok, detail = sum_equals_from_texts("Total", "Total ₹730.50", items)
    assert ok, detail
    ok, detail = sum_equals_from_texts("Total", "Total ₹731.50", items)
    assert not ok and "off by" in detail
    ok, _ = sum_equals_from_texts("Total", "Total ₹730.50", items + ["Total ₹730.50"])
    assert ok, "the total line itself must not be summed as an item"
    ok, _ = sum_equals_from_texts("Total", "Total due", items)
    assert not ok
    ok, _ = sum_equals_from_texts("Total", "Total ₹730.50", ["no amounts here"])
    assert not ok


@pytest.mark.asyncio
async def test_sum_equals_on_invoice_fixture(browser):
    context, page = await _open(browser, "invoice.html")
    try:
        ctx = types.SimpleNamespace(vars={})
        a = Assertion(kind="sum_equals", params={"total_label": "Total"},
                      description="Invoice total equals the sum of line items")
        ok, detail = await check(a, page, ctx, [], [])
        assert ok, detail
        a2 = Assertion(kind="sum_equals", params={"total_label": "Total",
                                                  "items_selector_text": "tbody tr"})
        ok2, detail2 = await check(a2, page, ctx, [], [])
        assert ok2, detail2
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_sum_equals_detects_wrong_total(browser):
    context, page = await _open(browser, "invoice.html")
    try:
        await page.evaluate("document.querySelector('tfoot td:last-child').textContent = '₹9,999.99'")
        ctx = types.SimpleNamespace(vars={})
        a = Assertion(kind="sum_equals", params={"total_label": "Total"})
        ok, detail = await check(a, page, ctx, [], [], wait_ms=0)
        assert not ok and "off by" in detail
    finally:
        await context.close()
