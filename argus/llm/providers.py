"""Free-tier provider mesh.

Every provider speaks the OpenAI chat-completions dialect, so one client can fan out across all of
them. A model spec is `provider:model` (e.g. `gemini:gemini-flash-latest`); a bare id means OpenRouter.
Chains are built from whatever keys are present, interleaving providers so one exhausted free quota
(OpenRouter free tier = 50 req/day) never stops a run.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_envs: tuple[str, ...]
    rpm: int                                   # conservative requests/minute for the free tier
    models: dict[str, tuple[str, ...]] = field(default_factory=dict)   # tier -> default free models
    signup: str = ""
    keyless: bool = False


PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        "openrouter", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY", "JEV_API"), 18,
        {"fast": ("nvidia/nemotron-3-super-120b-a12b:free", "qwen/qwen3.8-27b:free"),
         "smart": ("nvidia/nemotron-3-ultra-550b-a55b:free", "nex-agi/nex-n2.5-pro:free"),
         "vision": ("qwen/qwen3.8-27b:free", "google/gemma-4-31b-it:free")},
        "https://openrouter.ai/keys"),
    "gemini": Provider(
        "gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), 10,
        {"fast": ("gemini-flash-lite-latest",), "smart": ("gemini-flash-latest",), "vision": ("gemini-flash-latest",)},
        "https://aistudio.google.com/apikey"),
    "groq": Provider(
        "groq", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",), 25,
        {"fast": ("llama-3.1-8b-instant",), "smart": ("openai/gpt-oss-120b", "llama-3.3-70b-versatile"),
         "vision": ("meta-llama/llama-4-scout-17b-16e-instruct",)},
        "https://console.groq.com/keys"),
    "cerebras": Provider(
        "cerebras", "https://api.cerebras.ai/v1", ("CEREBRAS_API_KEY",), 25,
        {"fast": ("gpt-oss-120b",), "smart": ("gpt-oss-120b",)},
        "https://cloud.cerebras.ai"),
    "ollama": Provider(
        "ollama", os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/v1", (), 600,
        {}, "https://ollama.com/download", keyless=True),
    "custom": Provider(
        "custom", os.environ.get("ARGUS_LLM_BASE_URL", ""), ("ARGUS_LLM_API_KEY",), 30, {}),
}

# Layer 1: JEV (OpenRouter) is always tried first. Layer 2: the other free LLMs carry the load when
# JEV is rate-limited (50 req/day on its free tier) or fails. Ollama (local, offline) is the last net.
ORDER = ("openrouter", "gemini", "groq", "cerebras", "custom", "ollama")


def api_key(p: Provider) -> str:
    for env in p.key_envs:
        if os.environ.get(env):
            return os.environ[env]
    return "ollama" if p.keyless else ""


@lru_cache(maxsize=1)
def ollama_models() -> tuple[str, ...]:
    """Locally pulled Ollama models (empty if Ollama is not running)."""
    try:
        base = PROVIDERS["ollama"].base_url[:-3]
        with urllib.request.urlopen(base + "/api/tags", timeout=1.5) as r:
            return tuple(m["name"] for m in json.load(r).get("models", []))
    except Exception:
        return ()


def available() -> list[str]:
    """Providers usable right now (key present, or Ollama running)."""
    out = []
    for name in ORDER:
        p = PROVIDERS[name]
        if name == "ollama":
            if ollama_models():
                out.append(name)
        elif name == "custom":
            if p.base_url and api_key(p):
                out.append(name)
        elif api_key(p):
            out.append(name)
    return out


def split(spec: str) -> tuple[str, str]:
    """`gemini:gemini-flash-latest` -> (gemini, gemini-flash-latest); bare ids are OpenRouter."""
    head, sep, rest = spec.partition(":")
    if sep and head in PROVIDERS:
        return head, rest
    return "openrouter", spec


def default_chain(tier: str) -> list[str]:
    """Layered chain: all JEV/OpenRouter models first, then each other provider's free models."""
    per = []
    for name in available():
        if name == "ollama":
            models = ollama_models()[:1]
        elif name == "custom":
            models = tuple(filter(None, [os.environ.get("ARGUS_LLM_MODEL", "")]))
        else:
            models = PROVIDERS[name].models.get(tier, PROVIDERS[name].models.get("smart", ()))
        per.append([f"{name}:{m}" if name != "openrouter" else m for m in models])
    chain: list[str] = []
    for name, models in zip(available(), per):   # follows ORDER, so JEV comes first
        chain += models
        if name == "openrouter":
            chain.append("openrouter/free")      # JEV's own free router closes layer 1
    return chain


def is_free(provider: str, model: str) -> bool:
    return provider in ("gemini", "groq", "cerebras", "ollama") or model.endswith(":free") or model == "openrouter/free"
