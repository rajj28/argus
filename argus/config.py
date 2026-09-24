"""Settings for Argus. The API key is read from the environment / .env and never persisted."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

# OpenRouter free models (checked 2026-09-24). Order = fallback chain. Override per tier with
# ARGUS_MODELS_FAST / ARGUS_MODELS_SMART / ARGUS_MODELS_VISION (comma separated).
DEFAULT_MODELS: dict[str, list[str]] = {
    "fast": [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "qwen/qwen3.8-27b:free",
        "openrouter/free",
    ],
    "smart": [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nex-agi/nex-n2.5-pro:free",
        "qwen/qwen3.8-27b:free",
        "openrouter/free",
    ],
    "vision": [
        "qwen/qwen3.8-27b:free",
        "google/gemma-4-31b-it:free",
        "openrouter/free",
    ],
}

# Reference paid prices ($ per 1M tokens in, out) used only to report "what this would cost at list price".
REFERENCE_PRICES: dict[str, tuple[float, float]] = {
    "fast": (0.10, 0.40),
    "smart": (0.30, 2.50),
    "vision": (0.30, 2.50),
}


class Thresholds(BaseModel):
    replay: float = 0.90      # T0: cached element still matches
    accept: float = 0.72      # T1: deterministic similarity heal
    margin: float = 0.12      # T1: required gap to the runner-up
    strict: float = 0.85      # T2: out-of-order lookahead must be this sure
    llm_min: float = 0.35     # below this, the element is considered gone
    llm_confidence: float = 0.6


class Settings(BaseModel):
    home: Path = Path(".argus")
    base_url: str = "http://localhost:8000"
    context_dir: Optional[Path] = None          # folder with PRODUCT.md / CHANGELOG.md
    credentials: dict[str, str] = Field(default_factory=dict)   # {"user": ..., "password": ...}
    headless: bool = True
    llm_base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = Field(default="", exclude=True, repr=False)
    models: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_MODELS.items()})
    reference_prices: dict[str, tuple[float, float]] = Field(default_factory=lambda: dict(REFERENCE_PRICES))
    llm_enabled: bool = True
    llm_cache: bool = True
    max_llm_calls_per_run: int = 30
    thresholds: Thresholds = Field(default_factory=Thresholds)
    viewport: dict[str, int] = Field(default_factory=lambda: {"width": 1280, "height": 800})
    build: str = Field(default="", exclude=True)            # label of the build under test (plan versioning)
    pending_change: str = Field(default="", exclude=True)   # intent of an unreleased change (PR / agent)

    def product_context(self) -> str:
        return _read(self.context_dir, "PRODUCT.md")

    def changelog(self) -> str:
        notes = _read(self.context_dir, "CHANGELOG.md")
        if self.pending_change:
            notes = "## Pending change (PR / coding agent)" + chr(10) + self.pending_change + chr(10) * 2 + notes
        return notes

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json", exclude={"api_key", "models", "llm_enabled"})
        (self.home / "config.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read(folder: Optional[Path], name: str) -> str:
    if not folder:
        return ""
    p = Path(folder) / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _load_dotenv(root: Path) -> None:
    env = root / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_settings(home: Path | str = ".argus", **overrides) -> Settings:
    """Load .argus/config.json + environment. The key comes from OPENROUTER_API_KEY or JEV_API."""
    _load_dotenv(Path.cwd())
    home = Path(home)
    data: dict = {}
    cfg = home / "config.json"
    if cfg.exists():
        data = json.loads(cfg.read_text(encoding="utf-8"))
    data.update({k: v for k, v in overrides.items() if v is not None})
    data["home"] = home
    s = Settings(**data)
    s.api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("JEV_API", "")
    from argus.llm.providers import available, default_chain
    for tier in ("fast", "smart", "vision"):
        env = os.environ.get(f"ARGUS_MODELS_{tier.upper()}")
        if env:
            s.models[tier] = [m.strip() for m in env.split(",") if m.strip()]
        else:
            chain = default_chain(tier)          # layer 1: JEV/OpenRouter, layer 2: other free LLMs
            if chain:
                s.models[tier] = chain
    s.llm_enabled = os.environ.get("ARGUS_LLM", "").lower() != "off" and bool(available())
    return s
