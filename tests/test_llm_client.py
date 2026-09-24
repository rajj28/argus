"""LLM client tests (no network): fallback on 429, JSON extraction, cache, ledger,
budget, disabled -> LLMUnavailable, rate limiter, vision images."""
from __future__ import annotations

import base64
import time
import types

import pytest

import argus.llm.client as llm_mod
from argus.config import Settings
from argus.llm.client import BudgetExceeded, LLMClient, LLMUnavailable


def _settings(tmp_path, **kwargs) -> Settings:
    defaults = dict(
        home=tmp_path / "home",
        api_key="sk-test",
        llm_base_url="http://fake/v1",
        models={
            "fast": ["model-fast-1", "model-fast-2"],
            "smart": ["model-smart"],
            "vision": ["model-vision"],
        },
    )
    defaults.update(kwargs)
    return Settings(**defaults)


class _FakeError(Exception):
    def __init__(self, status=None):
        self.status_code = status
        super().__init__(f"fake error {status}")


def _fake_response(content: str, model: str = "fake-model", tokens_in: int = 10, tokens_out: int = 5):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))],
        usage=types.SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out),
        model=model,
    )


@pytest.fixture
def fake_openai(monkeypatch):
    state = {"calls": [], "handler": lambda kwargs, n: _fake_response('{"ok": true}')}

    async def dispatch(**kwargs):
        state["calls"].append(kwargs)
        result = state["handler"](kwargs, len(state["calls"]))
        if isinstance(result, BaseException):
            raise result
        return result

    class _Completions:
        async def create(self, **kwargs):
            return await dispatch(**kwargs)

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    return state


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    llm_mod._request_ts.clear()
    yield
    llm_mod._request_ts.clear()


# --------------------------------------------------------------------------- JSON
@pytest.mark.asyncio
async def test_json_extraction_from_messy_text(fake_openai, tmp_path):
    messy = (
        "Here you go!\n"
        "<thinking>sure, outputting JSON</thinking>\n"
        "```json\n"
        '{"answer": "42", "ok": true}\n'
        "```\n"
        "hope that helps"
    )
    fake_openai["handler"] = lambda kwargs, n: _fake_response(messy)
    client = LLMClient(_settings(tmp_path, llm_cache=False))
    data = await client.json(tier="smart", purpose="triage", system="sys", user="usr")
    assert data == {"answer": "42", "ok": True}
    assert fake_openai["calls"][0]["messages"][1]["content"] == "usr"


@pytest.mark.asyncio
async def test_json_extraction_from_prose(fake_openai, tmp_path):
    fake_openai["handler"] = lambda kwargs, n: _fake_response('plain prose then {"a": 1} trailing words')
    client = LLMClient(_settings(tmp_path, llm_cache=False))
    assert await client.json(tier="smart", purpose="triage", system="s", user="u") == {"a": 1}


# ---------------------------------------------------------------------- fallback
@pytest.mark.asyncio
async def test_fallback_and_backoff_on_429(fake_openai, tmp_path):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    settings = _settings(tmp_path, llm_cache=False, models={"fast": ["first", "second"]})
    client = LLMClient(settings)
    client._sleep = fake_sleep

    def handler(kwargs, n):
        if n <= 3:
            return _FakeError(429)
        return _fake_response('{"found": "yes"}')

    fake_openai["handler"] = handler
    data = await client.json(tier="fast", purpose="heal", system="s", user="u", max_tokens=50)
    assert data == {"found": "yes"}
    models_used = [c["model"] for c in fake_openai["calls"]]
    assert models_used == ["first", "first", "first", "second"]
    assert sleeps == [1.0, 2.0]  # 3 attempts per model -> exponential backoff 1s, 2s
    assert fake_openai["calls"][0]["response_format"] == {"type": "json_object"}
    assert fake_openai["calls"][0]["extra_body"] == {"reasoning": {"effort": "low", "exclude": True}}


@pytest.mark.asyncio
async def test_all_models_fail_raises_unavailable(fake_openai, tmp_path):
    async def no_sleep(_seconds):
        return None

    settings = _settings(tmp_path, llm_cache=False, models={"smart": ["m1", "m2"]})
    client = LLMClient(settings)
    client._sleep = no_sleep
    fake_openai["handler"] = lambda kwargs, n: _FakeError(500)
    with pytest.raises(LLMUnavailable):
        await client.json(tier="smart", purpose="triage", system="s", user="u")


@pytest.mark.asyncio
async def test_400_retries_without_response_format(fake_openai, tmp_path):
    client = LLMClient(_settings(tmp_path, llm_cache=False, models={"smart": ["m"]}))
    seen: list[bool] = []

    def handler(kwargs, n):
        seen.append("response_format" in kwargs)
        if n == 1:
            return _FakeError(400)
        return _fake_response('{"ok": true}')

    fake_openai["handler"] = handler
    data = await client.json(tier="smart", purpose="triage", system="s", user="u")
    assert data == {"ok": True}
    assert seen == [True, False]


# ------------------------------------------------------------------------- cache
@pytest.mark.asyncio
async def test_cache_hit_skips_second_api_call(fake_openai, tmp_path):
    client = LLMClient(_settings(tmp_path))
    first = await client.json(tier="smart", purpose="triage", system="sys", user="usr")
    second = await client.json(tier="smart", purpose="triage", system="sys", user="usr")
    assert first == second == {"ok": True}
    assert len(fake_openai["calls"]) == 1
    assert len(client.ledger) == 2
    assert client.ledger[0].cached is False
    hit = client.ledger[1]
    assert hit.cached is True
    assert hit.cost_usd == 0.0 and hit.tokens_in == 0 and hit.tokens_out == 0
    assert (tmp_path / "home" / "llm_cache").exists()


@pytest.mark.asyncio
async def test_cache_disabled_always_calls(fake_openai, tmp_path):
    client = LLMClient(_settings(tmp_path, llm_cache=False))
    await client.json(tier="smart", purpose="triage", system="sys", user="usr")
    await client.json(tier="smart", purpose="triage", system="sys", user="usr")
    assert len(fake_openai["calls"]) == 2


# ------------------------------------------------------------------------ ledger
@pytest.mark.asyncio
async def test_ledger_accounting_and_take(fake_openai, tmp_path):
    settings = _settings(tmp_path, llm_cache=False, models={"smart": ["paid/model"]})
    settings.reference_prices = {"smart": (0.5, 2.0)}
    client = LLMClient(settings)
    fake_openai["handler"] = lambda kwargs, n: _fake_response(
        '{"v": 1}', model="paid/model", tokens_in=1000, tokens_out=500
    )
    await client.json(tier="smart", purpose="replan", system="s", user="u")
    rec = client.ledger[0]
    assert rec.purpose == "replan" and rec.tier == "smart" and rec.model == "paid/model"
    assert rec.tokens_in == 1000 and rec.tokens_out == 500
    assert rec.cost_usd == pytest.approx(1000 / 1e6 * 0.5 + 500 / 1e6 * 2.0)
    assert rec.list_cost_usd == rec.cost_usd
    assert rec.cached is False and rec.ok is True and rec.latency_ms >= 0
    ledger = client.take_ledger()
    assert len(ledger) == 1 and client.ledger == []


@pytest.mark.asyncio
async def test_free_model_zero_cost_but_list_cost_positive(fake_openai, tmp_path):
    settings = _settings(tmp_path, llm_cache=False, models={"smart": ["nvidia/x:free"]})
    client = LLMClient(settings)
    fake_openai["handler"] = lambda kwargs, n: _fake_response(
        "{}", model="nvidia/x:free", tokens_in=1000, tokens_out=500
    )
    await client.json(tier="smart", purpose="triage", system="s", user="u")
    rec = client.ledger[0]
    assert rec.cost_usd == 0.0
    assert rec.list_cost_usd > 0.0


# ------------------------------------------------------------------------ budget
@pytest.mark.asyncio
async def test_budget_exceeded(fake_openai, tmp_path):
    settings = _settings(tmp_path, llm_cache=False, max_llm_calls_per_run=2)
    client = LLMClient(settings)
    await client.json(tier="smart", purpose="a", system="s", user="u")
    await client.json(tier="smart", purpose="b", system="s", user="u")
    with pytest.raises(BudgetExceeded):
        await client.json(tier="smart", purpose="c", system="s", user="u")


# --------------------------------------------------------------- availability
@pytest.mark.asyncio
async def test_disabled_raises_llm_unavailable(fake_openai, tmp_path):
    client = LLMClient(_settings(tmp_path, llm_enabled=False))
    with pytest.raises(LLMUnavailable):
        await client.json(tier="smart", purpose="triage", system="s", user="u")
    assert fake_openai["calls"] == []


# ----------------------------------------------------------------------- vision
@pytest.mark.asyncio
async def test_vision_sends_jpeg_data_url(fake_openai, tmp_path):
    client = LLMClient(_settings(tmp_path, llm_cache=False, models={"vision": ["v-model"]}))
    img = b"\xff\xd8fakejpeg"
    fake_openai["handler"] = lambda kwargs, n: _fake_response('{"ok": true}')
    await client.json(tier="vision", purpose="heal", system="s", user="u", images=[img])
    content = fake_openai["calls"][0]["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "u"}
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert url.endswith(base64.b64encode(img).decode())


# ------------------------------------------------------------------------- rate
@pytest.mark.asyncio
async def test_rate_limiter_caps_requests(monkeypatch):
    monkeypatch.setattr(llm_mod, "_RATE_WINDOW_S", 0.05)
    for _ in range(llm_mod._RATE_MAX):
        await llm_mod._acquire_slot()
    t0 = time.monotonic()
    await llm_mod._acquire_slot()  # must wait for the window to roll
    assert time.monotonic() - t0 >= 0.03