"""Robustness unit tests – no network, no browser, no LLM.

Covers four bugs fixed in this session:
  Bug 1 – author.py  : KeyError when a step has no 'find' key (wait / press / goto)
  Bug 2 – runner.py  : detached-element retry in _execute
  Bug 3 – llm/client : LLMClient.__init__ crashes when api_key is empty
  Bug 4 – runner.py  : run_test / run_suite must not propagate unexpected exceptions
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared fakes
# ---------------------------------------------------------------------------

def _settings(tmp_path: Path, **kwargs):
    """Minimal Settings object; never reads .env."""
    from argus.config import Settings
    defaults = dict(
        home=tmp_path / ".argus",
        api_key="sk-test",
        llm_base_url="http://fake/v1",
        llm_cache=False,
        models={
            "fast": ["fake/model"],
            "smart": ["fake/model"],
            "vision": ["fake/model"],
        },
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _settings_no_key(tmp_path: Path, **kwargs):
    """Settings with an empty api_key (simulates missing primary key)."""
    return _settings(tmp_path, api_key="", **kwargs)


# ===========================================================================
# Bug 1 – author.py: steps without 'find' must not raise KeyError
# ===========================================================================

class _FakePage:
    """Minimal synchronous-style fake for author() tests that only exercise step dispatch."""

    def __init__(self):
        self.waited_ms: list[int] = []
        self.pressed_keys: list[str] = []
        self.navigated: list[str] = []

    async def wait_for_timeout(self, ms: int) -> None:
        self.waited_ms.append(ms)

    async def keyboard(self):  # noqa: D102 – not a real method; accessed as .keyboard.press
        pass  # patched below

    async def goto(self, url: str, **_kwargs) -> None:
        self.navigated.append(url)


def _make_keyboard_fake(pressed_keys: list):
    kb = MagicMock()
    kb.press = AsyncMock(side_effect=lambda key: pressed_keys.append(key))
    return kb


@pytest.mark.asyncio
async def test_author_wait_step_no_find_key(tmp_path):
    """A 'wait' step dict without a 'find' key must not raise KeyError."""
    from argus.runner.author import author

    wait_step = {"action": "wait", "value": "1200", "intent": "Wait for animation"}
    spec_dict: dict[str, Any] = {
        "id": "t1",
        "name": "Wait test",
        "goal": "test wait",
        "start_url": "/",
        "requires_login": False,
        "steps": [wait_step],
        "oracles": [],
    }

    fake_page = _FakePage()
    pressed: list[str] = []
    fake_page.keyboard = _make_keyboard_fake(pressed)  # type: ignore[assignment]

    # Patch away the playwright/browser machinery — we only test the step-dispatch logic.
    with (
        patch("argus.runner.author.async_playwright") as mock_pw,
        patch("argus.runner.author.Memory") as MockMemory,
        patch("argus.runner.author.EffectRecorder") as MockEffectRecorder,
        patch("argus.runner.author.ensure_auth", new=AsyncMock(return_value=None)),
        patch("argus.runner.author.wait_for_settle", new=AsyncMock()),
        patch("argus.runner.author.take_snapshot", new=AsyncMock(return_value=MagicMock(elements=[]))),
        patch("argus.runner.author.record_baseline", new=AsyncMock(return_value=None)),
    ):
        # Build a fake playwright context manager chain
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)
        fake_context.__aenter__ = AsyncMock(return_value=fake_context)
        fake_context.__aexit__ = AsyncMock(return_value=False)

        fake_browser = AsyncMock()
        fake_browser.new_context = AsyncMock(return_value=fake_context)
        fake_browser.close = AsyncMock()

        fake_pw_instance = AsyncMock()
        fake_pw_instance.chromium.launch = AsyncMock(return_value=fake_browser)
        fake_pw_instance.__aenter__ = AsyncMock(return_value=fake_pw_instance)
        fake_pw_instance.__aexit__ = AsyncMock(return_value=False)
        mock_pw.return_value = fake_pw_instance

        # Memory stub
        mock_memory = MockMemory.return_value
        mock_memory.new_run_dir.return_value = ("run-1", tmp_path / "run-1")

        # EffectRecorder stub (avoid real page.on() calls)
        MockEffectRecorder.return_value = AsyncMock()

        settings = _settings(tmp_path)
        # Should not raise; wait_for_timeout must be called with 1200
        result = await author(settings, [spec_dict])

    assert fake_page.waited_ms == [1200], (
        "author() should call page.wait_for_timeout(1200) for a 'wait' step"
    )


@pytest.mark.asyncio
async def test_author_press_step_no_find_key(tmp_path):
    """A 'press' step dict without a 'find' key must use page.keyboard.press."""
    from argus.runner.author import author

    pressed: list[str] = []
    press_step = {"action": "press", "value": "Tab", "intent": "Press Tab"}
    spec_dict: dict[str, Any] = {
        "id": "t2",
        "name": "Press test",
        "goal": "test press",
        "start_url": "/",
        "requires_login": False,
        "steps": [press_step],
        "oracles": [],
    }

    fake_page = _FakePage()
    fake_page.keyboard = _make_keyboard_fake(pressed)  # type: ignore[assignment]

    with (
        patch("argus.runner.author.async_playwright") as mock_pw,
        patch("argus.runner.author.Memory") as MockMemory,
        patch("argus.runner.author.EffectRecorder") as MockEffectRecorder,
        patch("argus.runner.author.ensure_auth", new=AsyncMock(return_value=None)),
        patch("argus.runner.author.wait_for_settle", new=AsyncMock()),
        patch("argus.runner.author.take_snapshot", new=AsyncMock(return_value=MagicMock(elements=[]))),
        patch("argus.runner.author.record_baseline", new=AsyncMock(return_value=None)),
    ):
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)
        fake_context.__aenter__ = AsyncMock(return_value=fake_context)
        fake_context.__aexit__ = AsyncMock(return_value=False)

        fake_browser = AsyncMock()
        fake_browser.new_context = AsyncMock(return_value=fake_context)
        fake_browser.close = AsyncMock()

        fake_pw_instance = AsyncMock()
        fake_pw_instance.chromium.launch = AsyncMock(return_value=fake_browser)
        fake_pw_instance.__aenter__ = AsyncMock(return_value=fake_pw_instance)
        fake_pw_instance.__aexit__ = AsyncMock(return_value=False)
        mock_pw.return_value = fake_pw_instance

        mock_memory = MockMemory.return_value
        mock_memory.new_run_dir.return_value = ("run-1", tmp_path / "run-1")

        MockEffectRecorder.return_value = AsyncMock()

        settings = _settings(tmp_path)
        await author(settings, [spec_dict])

    assert pressed == ["Tab"], (
        "author() should call page.keyboard.press('Tab') for a targetless 'press' step"
    )


@pytest.mark.asyncio
async def test_author_goto_step_uses_value_not_find(tmp_path):
    """A 'goto' step with no 'find' key must navigate to the given value path."""
    from argus.runner.author import author

    goto_step = {"action": "goto", "value": "/dashboard", "intent": "Navigate to dashboard"}
    spec_dict: dict[str, Any] = {
        "id": "t3",
        "name": "Goto test",
        "goal": "test goto",
        "start_url": "/",
        "requires_login": False,
        "steps": [goto_step],
        "oracles": [],
    }

    fake_page = _FakePage()
    pressed: list[str] = []
    fake_page.keyboard = _make_keyboard_fake(pressed)  # type: ignore[assignment]
    navigated: list[str] = []

    async def fake_goto(url, **_kw):
        navigated.append(url)

    fake_page.goto = fake_goto  # type: ignore[assignment]

    with (
        patch("argus.runner.author.async_playwright") as mock_pw,
        patch("argus.runner.author.Memory") as MockMemory,
        patch("argus.runner.author.EffectRecorder") as MockEffectRecorder,
        patch("argus.runner.author.ensure_auth", new=AsyncMock(return_value=None)),
        patch("argus.runner.author.wait_for_settle", new=AsyncMock()),
        patch("argus.runner.author.take_snapshot", new=AsyncMock(return_value=MagicMock(elements=[]))),
        patch("argus.runner.author.record_baseline", new=AsyncMock(return_value=None)),
    ):
        fake_context = AsyncMock()
        fake_context.new_page = AsyncMock(return_value=fake_page)
        fake_context.__aenter__ = AsyncMock(return_value=fake_context)
        fake_context.__aexit__ = AsyncMock(return_value=False)

        fake_browser = AsyncMock()
        fake_browser.new_context = AsyncMock(return_value=fake_context)
        fake_browser.close = AsyncMock()

        fake_pw_instance = AsyncMock()
        fake_pw_instance.chromium.launch = AsyncMock(return_value=fake_browser)
        fake_pw_instance.__aenter__ = AsyncMock(return_value=fake_pw_instance)
        fake_pw_instance.__aexit__ = AsyncMock(return_value=False)
        mock_pw.return_value = fake_pw_instance

        mock_memory = MockMemory.return_value
        mock_memory.new_run_dir.return_value = ("run-1", tmp_path / "run-1")

        MockEffectRecorder.return_value = AsyncMock()

        settings = _settings(tmp_path)
        await author(settings, [spec_dict])

    # The start_url goto + the step goto both land in navigated; the step value must be present.
    assert any("/dashboard" in u for u in navigated), (
        f"author() should navigate to /dashboard for a 'goto' step; got {navigated}"
    )


# ===========================================================================
# Bug 2 – runner.py: detached-element retry in _execute
# ===========================================================================

def _make_element(ref: str = "e1"):
    from argus.models import ElementInfo
    return ElementInfo(ref=ref, tag="button", role="button", name="Submit", interactive=True)


def _make_fingerprint(ref: str = "e1"):
    from argus.models import Fingerprint
    el = _make_element(ref)
    return Fingerprint.from_element(el)


def _make_resolution(el):
    from argus.healing.resolver import Resolution
    return Resolution(element=el, tier=0, method="replay", score=1.0)


def _make_step(action: str = "click"):
    from argus.models import Step, StepExpect
    from argus.models import Fingerprint
    el = _make_element()
    return Step(
        id="s1",
        intent="click the button",
        action=action,
        target=Fingerprint.from_element(el),
        expect=StepExpect(),
    )


def _make_snap(ref: str = "e1"):
    from argus.models import PageSnapshot, ElementInfo
    el = ElementInfo(ref=ref, tag="button", role="button", name="Submit", interactive=True)
    return PageSnapshot(url="http://localhost/", elements=[el])


def _make_effects():
    from argus.models import Effects
    return Effects(url_before="http://localhost/", url_after="http://localhost/")


@pytest.mark.asyncio
async def test_execute_retries_on_detach_and_succeeds(tmp_path):
    """When the first _act raises a detach error, _execute must retry once and succeed."""
    import argus.runner.runner as runner_mod
    from argus.runner.runner import RunContext, _Exec, _execute
    from argus.memory.store import Memory
    from argus.browser.effects import EffectRecorder

    settings = _settings(tmp_path)
    memory = Memory(settings.home)
    memory.home.mkdir(parents=True, exist_ok=True)

    ctx = RunContext(settings, memory, None, "run-1", tmp_path / "run-1")
    (tmp_path / "run-1" / "screenshots").mkdir(parents=True, exist_ok=True)

    step = _make_step("click")
    el = _make_element("e1")
    res = _make_resolution(el)
    snap = _make_snap("e1")

    # Fake EffectRecorder
    effects = _make_effects()
    rec = AsyncMock(spec=EffectRecorder)
    rec.begin = AsyncMock()
    rec.end = AsyncMock(return_value=effects)
    rec.all_page_errors = MagicMock(return_value=[])

    # Fake page: first handle raises detach, second handle succeeds
    call_count = {"n": 0}

    class _FakeHandle:
        async def click(self, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("Element is not attached to the DOM")
            # second call succeeds

    fresh_handle = _FakeHandle()

    async def fake_element_handle(page, ref):
        return fresh_handle

    # Fake take_snapshot returns a snap with the element still present
    async def fake_take_snapshot(page):
        return snap

    # resolve returns the same element
    async def fake_resolve(snap, step, weights, llm, settings, strict=False):
        return res

    fake_page = MagicMock()
    fake_page.screenshot = AsyncMock(return_value=b"")

    with (
        patch.object(runner_mod, "element_handle", fake_element_handle),
        patch.object(runner_mod, "take_snapshot", fake_take_snapshot),
        patch.object(runner_mod, "resolve", fake_resolve),
    ):
        test_spec = MagicMock()
        test_spec.id = "t1"
        st = _Exec(test_spec)

        sr = await _execute(ctx, fake_page, rec, st, step, res, snap, "orig")

    # The action must have been called twice (first detach, then success)
    assert call_count["n"] == 2, "expected exactly 2 calls: one detach + one retry"
    # No error observation because retry succeeded
    timeout_obs = [o for o in sr.observations if o.kind == "timeout"]
    assert not timeout_obs, f"No timeout observation expected on successful retry, got: {timeout_obs}"


@pytest.mark.asyncio
async def test_execute_records_error_when_retry_also_fails(tmp_path):
    """If both the initial act and the detach-retry fail, the step status must be 'failed'."""
    import argus.runner.runner as runner_mod
    from argus.runner.runner import RunContext, _Exec, _execute
    from argus.memory.store import Memory
    from argus.browser.effects import EffectRecorder

    settings = _settings(tmp_path)
    memory = Memory(settings.home)
    memory.home.mkdir(parents=True, exist_ok=True)

    ctx = RunContext(settings, memory, None, "run-1", tmp_path / "run-1")
    (tmp_path / "run-1" / "screenshots").mkdir(parents=True, exist_ok=True)

    step = _make_step("click")
    el = _make_element("e1")
    res = _make_resolution(el)
    snap = _make_snap("e1")

    effects = _make_effects()
    rec = AsyncMock(spec=EffectRecorder)
    rec.begin = AsyncMock()
    rec.end = AsyncMock(return_value=effects)
    rec.all_page_errors = MagicMock(return_value=[])

    class _AlwaysDetachedHandle:
        async def click(self, **_kw):
            raise Exception("not attached to the DOM")

    async def fake_element_handle(page, ref):
        return _AlwaysDetachedHandle()

    async def fake_take_snapshot(page):
        return snap

    async def fake_resolve(snap, step, weights, llm, settings, strict=False):
        return res

    fake_page = MagicMock()
    fake_page.screenshot = AsyncMock(return_value=b"")

    with (
        patch.object(runner_mod, "element_handle", fake_element_handle),
        patch.object(runner_mod, "take_snapshot", fake_take_snapshot),
        patch.object(runner_mod, "resolve", fake_resolve),
    ):
        test_spec = MagicMock()
        test_spec.id = "t1"
        st = _Exec(test_spec)

        sr = await _execute(ctx, fake_page, rec, st, step, res, snap, "orig")

    assert sr.status == "failed", "step status must be 'failed' when retry also raises"
    timeout_obs = [o for o in sr.observations if o.kind == "timeout"]
    assert timeout_obs, "a 'timeout' observation should record the error detail"


@pytest.mark.asyncio
async def test_execute_no_retry_on_non_detach_error(tmp_path):
    """Non-detach errors (e.g. timeout) must NOT trigger the retry path."""
    import argus.runner.runner as runner_mod
    from argus.runner.runner import RunContext, _Exec, _execute
    from argus.memory.store import Memory
    from argus.browser.effects import EffectRecorder

    settings = _settings(tmp_path)
    memory = Memory(settings.home)
    memory.home.mkdir(parents=True, exist_ok=True)

    ctx = RunContext(settings, memory, None, "run-1", tmp_path / "run-1")
    (tmp_path / "run-1" / "screenshots").mkdir(parents=True, exist_ok=True)

    step = _make_step("click")
    el = _make_element("e1")
    res = _make_resolution(el)
    snap = _make_snap("e1")

    effects = _make_effects()
    rec = AsyncMock(spec=EffectRecorder)
    rec.begin = AsyncMock()
    rec.end = AsyncMock(return_value=effects)
    rec.all_page_errors = MagicMock(return_value=[])

    call_count = {"n": 0}

    class _TimeoutHandle:
        async def click(self, **_kw):
            call_count["n"] += 1
            raise Exception("Timeout 5000ms exceeded")

    snapshot_calls = {"n": 0}

    async def fake_element_handle(page, ref):
        return _TimeoutHandle()

    async def fake_take_snapshot(page):
        snapshot_calls["n"] += 1
        return snap

    async def fake_resolve(snap, step, weights, llm, settings, strict=False):
        return res

    fake_page = MagicMock()
    fake_page.screenshot = AsyncMock(return_value=b"")

    with (
        patch.object(runner_mod, "element_handle", fake_element_handle),
        patch.object(runner_mod, "take_snapshot", fake_take_snapshot),
        patch.object(runner_mod, "resolve", fake_resolve),
    ):
        test_spec = MagicMock()
        test_spec.id = "t1"
        st = _Exec(test_spec)

        sr = await _execute(ctx, fake_page, rec, st, step, res, snap, "orig")

    # _act called exactly once (no retry)
    assert call_count["n"] == 1, "timeout error must not trigger a retry"
    # take_snapshot called once for the initial action only (not again for retry)
    assert snapshot_calls["n"] == 0, "take_snapshot should not be called for a non-detach error"
    assert sr.status == "failed"


# ===========================================================================
# Bug 3 – llm/client.py: empty api_key must not raise "Missing credentials"
# ===========================================================================

def test_llm_client_init_empty_key_does_not_raise(tmp_path):
    """LLMClient.__init__ with an empty api_key must not raise an exception."""
    import argus.llm.client as llm_mod

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            key = kwargs.get("api_key", "")
            if not key:
                raise ValueError("Missing credentials")

    with patch.object(llm_mod, "AsyncOpenAI", _FakeAsyncOpenAI):
        settings = _settings_no_key(tmp_path)
        # Must not raise
        client = llm_mod.LLMClient(settings)

    assert client._client is None, (
        "When api_key is empty, _client must be None (not a constructed AsyncOpenAI)"
    )


def test_llm_client_init_with_key_creates_client(tmp_path):
    """LLMClient.__init__ with a non-empty api_key must build the default OpenRouter client."""
    import argus.llm.client as llm_mod

    created_kwargs: list[dict] = []

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            created_kwargs.append(kwargs)

    with patch.object(llm_mod, "AsyncOpenAI", _FakeAsyncOpenAI):
        settings = _settings(tmp_path, api_key="sk-real-key")
        client = llm_mod.LLMClient(settings)

    assert client._client is not None, "With a real key, _client must be set"
    assert any(k.get("api_key") == "sk-real-key" for k in created_kwargs)


def test_llm_client_for_openrouter_returns_none_when_no_key(tmp_path):
    """_client_for('openrouter') must return None when the primary key is absent."""
    import argus.llm.client as llm_mod

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            key = kwargs.get("api_key", "")
            if not key:
                raise ValueError("Missing credentials")

    with patch.object(llm_mod, "AsyncOpenAI", _FakeAsyncOpenAI):
        settings = _settings_no_key(tmp_path)
        client = llm_mod.LLMClient(settings)

    result = client._client_for("openrouter")
    assert result is None, (
        "_client_for('openrouter') must return None when api_key is empty"
    )


# ===========================================================================
# Bug 4 – runner.py: run_test / run_suite must handle unexpected exceptions
# ===========================================================================

def _minimal_test_spec(test_id: str = "t1"):
    from argus.models import TestSpec
    return TestSpec(
        id=test_id,
        name="Test " + test_id,
        goal="verify something",
        start_url="/",
        requires_login=False,
    )


def _make_run_ctx(tmp_path: Path):
    from argus.runner.runner import RunContext
    from argus.memory.store import Memory
    settings = _settings(tmp_path)
    memory = Memory(settings.home)
    memory.home.mkdir(parents=True, exist_ok=True)
    return RunContext(settings, memory, None, "run-1", tmp_path / "run-1")


@pytest.mark.asyncio
async def test_run_test_unexpected_exception_gives_infra_verdict(tmp_path):
    """An unexpected exception inside run_test's step loop must yield INFRA, not propagate."""
    from argus.runner.runner import run_test, RunContext
    from argus.memory.store import Memory

    test = _minimal_test_spec()
    ctx = _make_run_ctx(tmp_path)

    # Fake browser / context / page
    fake_page = AsyncMock()
    # page.goto succeeds (returns a response with status 200)
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_page.goto = AsyncMock(return_value=fake_resp)
    fake_page.url = "http://localhost/"
    fake_page.screenshot = AsyncMock(return_value=b"")

    fake_context = AsyncMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.tracing = AsyncMock()
    fake_context.tracing.start = AsyncMock()
    fake_context.tracing.stop = AsyncMock()
    fake_context.close = AsyncMock()

    fake_browser = AsyncMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)

    import argus.runner.runner as runner_mod
    from argus.browser.effects import EffectRecorder

    # Make wait_for_settle raise an unexpected error after page.goto succeeds
    async def boom(*_args, **_kwargs):
        raise RuntimeError("Simulated unexpected crash inside step loop")

    with patch.object(runner_mod, "wait_for_settle", boom):
        result = await run_test(test, ctx, fake_browser, None, update=False)

    assert result.status == "error", f"expected 'error', got '{result.status}'"
    assert result.verdict.category == "INFRA", (
        f"expected INFRA verdict, got '{result.verdict.category}'"
    )
    assert "unexpected" in result.verdict.rationale.lower() or "crash" in result.verdict.rationale.lower() or "simulated" in result.verdict.rationale.lower(), (
        f"rationale should mention the error: {result.verdict.rationale!r}"
    )


@pytest.mark.asyncio
async def test_run_test_goto_dns_failure_gives_infra_verdict(tmp_path):
    """net::ERR_NAME_NOT_RESOLVED on page.goto must yield an INFRA verdict."""
    from argus.runner.runner import run_test

    test = _minimal_test_spec()
    ctx = _make_run_ctx(tmp_path)

    fake_page = AsyncMock()
    fake_page.goto = AsyncMock(side_effect=Exception("net::ERR_NAME_NOT_RESOLVED"))
    fake_page.url = "http://localhost/"

    fake_context = AsyncMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.tracing = AsyncMock()
    fake_context.tracing.start = AsyncMock()
    fake_context.tracing.stop = AsyncMock()
    fake_context.close = AsyncMock()

    fake_browser = AsyncMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)

    result = await run_test(test, ctx, fake_browser, None, update=False)

    assert result.status == "error"
    assert result.verdict.category == "INFRA"
    assert "NAME_NOT_RESOLVED" in result.verdict.rationale or "unreachable" in result.verdict.rationale.lower()


@pytest.mark.asyncio
async def test_run_suite_continues_after_run_test_raises(tmp_path):
    """run_suite must not propagate a crash from run_test; other tests must still run."""
    import argus.runner.runner as runner_mod
    from argus.runner.runner import run_suite
    from argus.config import Settings

    settings = _settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)

    # Two test specs in memory
    t1 = _minimal_test_spec("t1")
    t2 = _minimal_test_spec("t2")

    # Fake Memory: list_tests returns both tests; new_run_dir returns stable paths
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)

    mock_memory = MagicMock()
    mock_memory.new_run_dir.return_value = ("r1", run_dir)
    mock_memory.list_tests.return_value = [t1, t2]
    mock_memory.stability.return_value = {}
    mock_memory.save_run = MagicMock()

    call_order: list[str] = []

    async def fake_run_test(test, ctx, browser, auth_state, update=True):
        call_order.append(test.id)
        if test.id == "t1":
            raise RuntimeError("Simulated crash in run_test for t1")
        from argus.models import TestResult, Verdict
        return TestResult(
            test_id=test.id, test_name=test.name, test_version=1,
            status="passed", verdict=Verdict(category="PASS"),
        )

    with (
        patch.object(runner_mod, "Memory", return_value=mock_memory),
        patch.object(runner_mod, "run_test", fake_run_test),
        patch("argus.runner.runner.async_playwright") as mock_pw,
        patch.object(runner_mod, "ensure_auth", new=AsyncMock(return_value=None)),
    ):
        fake_browser = AsyncMock()
        fake_browser.close = AsyncMock()

        fake_pw_instance = AsyncMock()
        fake_pw_instance.chromium.launch = AsyncMock(return_value=fake_browser)
        fake_pw_instance.__aenter__ = AsyncMock(return_value=fake_pw_instance)
        fake_pw_instance.__aexit__ = AsyncMock(return_value=False)
        mock_pw.return_value = fake_pw_instance

        report = await run_suite(settings, update=False)

    # Both tests ran (suite did not abort after t1 crashed)
    assert "t1" in call_order, "t1 must have been attempted"
    assert "t2" in call_order, "t2 must still run even though t1 crashed"

    # t1's result must be INFRA
    t1_results = [r for r in report.results if r.test_id == "t1"]
    assert t1_results, "report must include a result for t1"
    assert t1_results[0].verdict.category == "INFRA", (
        f"crashed test should get INFRA, got {t1_results[0].verdict.category}"
    )

    # t2 passed
    t2_results = [r for r in report.results if r.test_id == "t2"]
    assert t2_results, "report must include a result for t2"
    assert t2_results[0].verdict.category == "PASS"
