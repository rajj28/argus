"""Free-tier LLM mesh client: layered provider fallback (JEV/OpenRouter first, then Gemini, Groq,
Cerebras, custom, local Ollama), disk cache, cost ledger, budget cap.

Also exports sliding-window rate limiters (one per provider; OpenRouter <= 18 requests / 60 s). Everything here is pure Python except the OpenAI SDK call;
callers must degrade gracefully when `LLMUnavailable` or `BudgetExceeded` is raised.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Literal

from openai import AsyncOpenAI

from argus.config import Settings
from argus.llm.providers import PROVIDERS, api_key, is_free, split
from argus.models import LLMCallRecord

Tier = Literal["fast", "smart", "vision"]

_TIMEOUT_S = 25.0
_CALL_DEADLINE_S = 75.0          # hard ceiling for one json() decision across all fallbacks
_MAX_ATTEMPTS_PER_MODEL = 3
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_CACHE_DIR = "llm_cache"

# --- module-level rate limiter (≤18 req / 60 s across the process) ------------
_RATE_MAX = 18
_RATE_WINDOW_S = 60.0
_request_ts: list[float] = []            # OpenRouter window (kept for backwards compatibility)
_provider_ts: dict[str, list[float]] = {"openrouter": _request_ts}
_exhausted: dict[str, str] = {}          # provider -> reason: daily quota gone, skip it for this process
_QUOTA_MARKERS = ("per-day", "per day", "daily", "quota", "insufficient credits", "monthly")


class LLMUnavailable(Exception):
    """LLM is disabled or every model on the tier's fallback chain failed."""


class BudgetExceeded(Exception):
    """Per-run budget of non-cached LLM calls is exhausted."""


async def _acquire_slot(provider: str = "openrouter", rpm: int = _RATE_MAX) -> None:
    """Wait until fewer than `_RATE_MAX` requests were made in the last 60 s.

    The prune/check/append section is synchronous with no `await` in between, so it is
    atomic under asyncio's cooperative scheduling even though no lock is held.
    """
    window = _provider_ts.setdefault(provider, [])
    while True:
        now = time.monotonic()
        while window and now - window[0] >= _RATE_WINDOW_S:
            window.pop(0)
        if len(window) < rpm:
            window.append(now)
            return
        oldest = window[0]
        await asyncio.sleep(max(0.0, oldest + _RATE_WINDOW_S - now))


def _error_status(exc: BaseException) -> int | None:
    """Best-effort HTTP status for openai errors and test doubles."""
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _strip_wrappers(text: str) -> str:
    """Drop `<thinking>` blocks and markdown code fences from a completion."""
    text = re.sub(r"(?is)<thinking\b.*?</thinking>", "", text)
    return re.sub(r"`{3,}", "", text)


def _first_object(text: str) -> str | None:
    """Return the first balanced `{...}` span in `text` (string-aware)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def _parse_json(text: str) -> dict | None:
    """Parse a JSON object out of possibly messy completion text; None if impossible."""
    text = _strip_wrappers(text or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    obj = _first_object(text)
    if obj is None:
        return None
    try:
        data = json.loads(obj)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _cache_key(tier: str, system: str, user: str, images: list[bytes] | None) -> str:
    h = hashlib.sha256()
    for part in (tier.encode("utf-8"), system.encode("utf-8"), user.encode("utf-8")):
        h.update(part)
        h.update(b"\x1f")
    for blob in images or []:
        h.update(hashlib.sha256(blob).digest())
        h.update(b"\x1f")
    return h.hexdigest()


def _build_messages(system: str, user: str, images: list[bytes] | None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    if images:
        content: list[Any] = [{"type": "text", "text": user}]
        for blob in images:
            b64 = base64.b64encode(blob).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user})
    return messages


def _content_text(resp: Any) -> str:
    """Extract the message text from a completion response (str or content parts)."""
    try:
        content = getattr(resp.choices[0].message, "content", None) or ""
    except (AttributeError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return str(content)


def _usage_tokens(resp: Any, name: str) -> int:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return 0
    value = usage.get(name, 0) if isinstance(usage, dict) else getattr(usage, name, 0)
    return int(value or 0)


def _extract_gpt_oss_reasoning(resp: Any) -> dict | None:
    """For Groq gpt-oss: when visible content is empty, try to parse JSON from the reasoning
    fields that the model may have populated instead (`reasoning` or `reasoning_content`)."""
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return None
    for attr in ("reasoning", "reasoning_content"):
        raw = getattr(msg, attr, None)
        if not raw:
            # also look inside model_extra / __dict__ for SDK versions that use __extra__
            extra = getattr(msg, "model_extra", None) or getattr(msg, "__dict__", {})
            raw = (extra or {}).get(attr)
        if raw and isinstance(raw, str):
            data = _parse_json(raw)
            if data is not None:
                return data
    return None


class _CallResult:
    __slots__ = ("data", "model", "tokens_in", "tokens_out", "latency_ms")

    def __init__(self, data: dict, model: str, tokens_in: int, tokens_out: int, latency_ms: int):
        self.data = data
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.latency_ms = latency_ms


class LLMClient:
    """Async OpenAI-compatible client with fallback, cache, ledger and budget."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger: list[LLMCallRecord] = []
        self._used_calls = 0
        self._last_error = ""
        self._sleep = asyncio.sleep
        # Build the default OpenRouter client only when a key is present; otherwise leave it
        # None so _client_for("openrouter") returns None and the fallback chain skips it.
        if settings.api_key:
            self._client = AsyncOpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.api_key,
                timeout=_TIMEOUT_S,
                default_headers={"HTTP-Referer": "https://github.com/argus-qa", "X-Title": "Argus"},
            )
        else:
            self._client = None
        self._clients: dict[str, AsyncOpenAI | None] = {"openrouter": self._client}

    def _client_for(self, provider: str) -> AsyncOpenAI | None:
        """One OpenAI-compatible client per provider, created lazily when its key is present."""
        if provider in self._clients:
            return self._clients[provider]
        p = PROVIDERS.get(provider)
        key = api_key(p) if p else ""
        if not p or not key or not p.base_url:
            return None
        self._clients[provider] = AsyncOpenAI(base_url=p.base_url, api_key=key, timeout=_TIMEOUT_S)
        return self._clients[provider]

    # ------------------------------------------------------------------ public
    def take_ledger(self) -> list[LLMCallRecord]:
        """Return and clear the per-call cost ledger."""
        records, self.ledger = list(self.ledger), []
        return records

    async def json(
        self,
        *,
        tier: Tier,
        purpose: str,
        system: str,
        user: str,
        images: list[bytes] | None = None,
        max_tokens: int = 700,
    ) -> dict:
        """Ask the tier's fallback chain for a JSON object and return the parsed dict."""
        if not self.settings.llm_enabled:
            raise LLMUnavailable("LLM is disabled (ARGUS_LLM=off or no API key)")
        models = list(self.settings.models.get(tier, []) or [])
        if not models:
            raise LLMUnavailable(f"no models configured for tier {tier!r}")

        key = _cache_key(tier, system, user, images) if self.settings.llm_cache else ""
        if key:
            cached = self._load_cache(key)
            if cached is not None:
                self.ledger.append(
                    LLMCallRecord(purpose=purpose, tier=tier, model="cache", cached=True, ok=True)
                )
                return cached

        if self._used_calls >= self.settings.max_llm_calls_per_run:
            raise BudgetExceeded(
                f"per-run budget of {self.settings.max_llm_calls_per_run} non-cached LLM calls exhausted"
            )

        result = await self._try_models(models, tier=tier, system=system, user=user,
                                        images=images, max_tokens=max_tokens)
        if key:
            self._write_cache(key, result.data)

        price_in, price_out = self.settings.reference_prices.get(tier, (0.0, 0.0))
        free = is_free(*split(result.model)) or ":free" in result.model
        cost = 0.0 if free else (result.tokens_in / 1e6) * price_in + (result.tokens_out / 1e6) * price_out
        list_cost = (result.tokens_in / 1e6) * price_in + (result.tokens_out / 1e6) * price_out

        self.ledger.append(
            LLMCallRecord(
                purpose=purpose,
                tier=tier,
                model=result.model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=cost,
                list_cost_usd=list_cost,
                latency_ms=result.latency_ms,
                cached=False,
                ok=True,
            )
        )
        self._used_calls += 1
        return result.data

    async def json_from(
        self,
        model_spec: str,
        *,
        purpose: str,
        system: str,
        user: str,
        max_tokens: int = 700,
    ) -> dict:
        """Call ONE specific model spec and return parsed JSON dict.

        Applies the same cache / ledger / budget / circuit-breaker behaviour as
        ``json()``.  ``model_spec`` is a full provider:model string, e.g.
        ``"nvidia/nemotron-3-super-120b-a12b:free"`` (openrouter) or
        ``"openrouter2:qwen/qwen3.8-27b:free"`` or ``"groq:openai/gpt-oss-120b"``.
        """
        if not self.settings.llm_enabled:
            raise LLMUnavailable("LLM is disabled (ARGUS_LLM=off or no API key)")

        # Derive a deterministic tier name from the spec for cache-key and ledger.
        # We use a synthetic tier so the cache is independent of the standard tiers.
        tier: Tier = "smart"

        key = _cache_key(f"jury:{model_spec}", system, user, None) if self.settings.llm_cache else ""
        if key:
            cached = self._load_cache(key)
            if cached is not None:
                self.ledger.append(
                    LLMCallRecord(purpose=purpose, tier=tier, model="cache", cached=True, ok=True)
                )
                return cached

        if self._used_calls >= self.settings.max_llm_calls_per_run:
            raise BudgetExceeded(
                f"per-run budget of {self.settings.max_llm_calls_per_run} non-cached LLM calls exhausted"
            )

        result = await self._try_models(
            [model_spec], tier=tier, system=system, user=user, images=None, max_tokens=max_tokens
        )
        if key:
            self._write_cache(key, result.data)

        price_in, price_out = self.settings.reference_prices.get(tier, (0.0, 0.0))
        free = is_free(*split(result.model)) or ":free" in result.model
        cost = 0.0 if free else (result.tokens_in / 1e6) * price_in + (result.tokens_out / 1e6) * price_out
        list_cost = (result.tokens_in / 1e6) * price_in + (result.tokens_out / 1e6) * price_out

        self.ledger.append(
            LLMCallRecord(
                purpose=purpose,
                tier=tier,
                model=result.model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=cost,
                list_cost_usd=list_cost,
                latency_ms=result.latency_ms,
                cached=False,
                ok=True,
            )
        )
        self._used_calls += 1
        return result.data

    # ---------------------------------------------------------------- internal
    async def _try_models(
        self,
        models: list[str],
        *,
        tier: Tier,
        system: str,
        user: str,
        images: list[bytes] | None,
        max_tokens: int,
    ) -> _CallResult:
        messages = _build_messages(system, user, images)
        deadline = time.monotonic() + _CALL_DEADLINE_S
        for model in models:
            if time.monotonic() > deadline:
                self._last_error = f"decision deadline of {_CALL_DEADLINE_S:.0f}s exceeded"
                break
            result = await self._try_one_model(model, messages, max_tokens, tier=tier)
            if result is not None:
                return result
        raise LLMUnavailable(
            f"all {len(models)} model(s) on tier {tier!r} failed; last error: {self._last_error}"
        )

    async def _try_one_model(
        self, model: str, messages: list[dict[str, Any]], max_tokens: int, *, tier: Tier
    ) -> _CallResult | None:
        provider, model_id = split(model)
        if provider in _exhausted:
            self._last_error = f"{provider} skipped: {_exhausted[provider]}"
            return None
        client = self._client_for(provider)
        if client is None:
            self._last_error = f"provider {provider!r} not configured"
            return None
        rpm = PROVIDERS[provider].rpm if provider in PROVIDERS else _RATE_MAX

        # gpt-oss are Groq reasoning models: hidden reasoning eats the token budget, leaving
        # empty content when max_tokens is too low.  Apply three mitigations:
        #   1. Floor max_tokens at 1024 so the model has room to produce a response.
        #   2. Send reasoning_effort="low" via extra_body to minimise reasoning overhead.
        #   3. On empty content, attempt once more without response_format (see below).
        _is_gpt_oss = provider == "groq" and "gpt-oss" in model_id
        if _is_gpt_oss:
            max_tokens = max(max_tokens, 1024)

        use_json = True
        _gpt_oss_no_format_retry = False   # True after we drop response_format for gpt-oss
        for attempt in range(1, _MAX_ATTEMPTS_PER_MODEL + 1):
            await _acquire_slot(provider, rpm)
            t0 = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "timeout": _TIMEOUT_S,
                }
                if use_json:
                    kwargs["response_format"] = {"type": "json_object"}
                if provider in ("openrouter", "openrouter2"):
                    # free reasoning models otherwise "think" for minutes; keep every tier snappy
                    kwargs["extra_body"] = {"reasoning": {"effort": "low", "exclude": True}}
                elif _is_gpt_oss:
                    # Groq gpt-oss: low reasoning effort keeps the hidden scratchpad short so the
                    # visible content token budget is not consumed by reasoning alone.
                    kwargs["extra_body"] = {"reasoning_effort": "low"}
                resp = await client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - any provider/transport error
                status = _error_status(exc)
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._log_error(model, time.monotonic() - t0, self._last_error)
                if status == 400 and use_json:
                    use_json = False  # provider rejects json_object; retry without it
                    continue
                if status in (402, 429) and any(m in str(exc).lower() for m in _QUOTA_MARKERS):
                    # account-wide quota: every model of this provider will fail - open the circuit
                    _exhausted[provider] = "daily/monthly free quota exhausted"
                    return None
                if "timeout" in type(exc).__name__.lower():
                    return None  # slow model: move on to the next one instead of waiting again
                retriable = status == 429 or (isinstance(status, int) and status >= 500)
                if retriable and attempt < _MAX_ATTEMPTS_PER_MODEL:
                    await self._sleep(_BACKOFF_SECONDS[attempt - 1])
                    continue
                return None  # hard failure on this model -> next in the chain
            latency_ms = int((time.monotonic() - t0) * 1000)
            content = _content_text(resp)
            data = _parse_json(content)
            if data is None:
                # --- gpt-oss empty-content recovery -------------------------------------
                if _is_gpt_oss and not content.strip():
                    # 1. The reasoning field sometimes carries the JSON the model "meant" to
                    #    output.  Check both common field names before giving up.
                    data = _extract_gpt_oss_reasoning(resp)
                    if data is not None:
                        return _CallResult(
                            data=data,
                            model=model,
                            tokens_in=_usage_tokens(resp, "prompt_tokens"),
                            tokens_out=_usage_tokens(resp, "completion_tokens"),
                            latency_ms=latency_ms,
                        )
                    # 2. Retry once without response_format; gpt-oss sometimes refuses to
                    #    emit JSON-mode output but answers correctly in plain-text mode.
                    if use_json and not _gpt_oss_no_format_retry:
                        use_json = False
                        _gpt_oss_no_format_retry = True
                        continue
                # --- generic empty / non-JSON fallback ----------------------------------
                self._last_error = f"model returned non-JSON output: {content[:120]!r}"
                if attempt < _MAX_ATTEMPTS_PER_MODEL:
                    continue  # one free retry, then fall through to the next model
                return None
            return _CallResult(
                data=data,
                model=model,
                tokens_in=_usage_tokens(resp, "prompt_tokens"),
                tokens_out=_usage_tokens(resp, "completion_tokens"),
                latency_ms=latency_ms,
            )
        return None

    def _log_error(self, model: str, seconds: float, error: str) -> None:
        """Append provider failures to .argus/llm_errors.log (diagnostics; never contains keys)."""
        try:
            path = Path(self.settings.home) / "llm_errors.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {model} {seconds:.1f}s {error[:300]}" + chr(10))
        except OSError:
            pass

    # ------------------------------------------------------------------ cache
    def _cache_path(self, key: str) -> Path:
        return self.settings.home / _CACHE_DIR / f"{key}.json"

    def _load_cache(self, key: str) -> dict | None:
        try:
            raw = self._cache_path(key).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _write_cache(self, key: str, data: dict) -> None:
        path = self._cache_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # cache is best-effort; never fail a run because of it