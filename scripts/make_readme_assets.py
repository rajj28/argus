"""Build the README charts and screenshot thumbnails from recorded results.

    python scripts/make_readme_assets.py      # needs matplotlib + pillow

Every number comes from site_data/*.json (exported from real runs); nothing is typed in by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"

SHOTS = {
    "live-map-stale.png": "runs/live/S11-map-stale/shots/1-map-stale.png",
    "live-unauth-control.png": "runs/live/S9-unauth-control/shots/1-stranger-took-off-drone-3.png",
    "live-socket-drop.png": "runs/live/S1-freshness/shots/1-socket-drop.png",
    "live-self-healed.png": "runs/live/L3-heal/shots/1-self-healed.png",
    "live-semantic-judge.png": "runs/live/L3-judge/shots/1-semantic-judgement.png",
    "live-movement-trail.png": "runs/live/S12-movement-trail/shots/1-trail.png",
}


def _load(name: str) -> dict:
    return json.loads((ROOT / "site_data" / name).read_text(encoding="utf-8"))


def ten_runs_chart() -> None:
    runs = _load("ten_runs.json")["runs"]
    x = [r["run"] for r in runs]
    naive = [r["naive_tokens_estimate"] for r in runs]
    llm = [r["llm_calls"] for r in runs]
    healed = [r["healed"] for r in runs]
    replayed = [r["t0_replayed"] for r in runs]

    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.2), dpi=150)
    a.bar(x, naive, color="#c9d3e0", label="LLM-per-action agent (est. tokens)")
    a.bar(x, llm, color="#1f6feb", label="Argus (actual LLM calls = 0)")
    for r in runs:
        a.annotate(f"v{r['version']}", (r["run"], r["naive_tokens_estimate"]), ha="center",
                   va="bottom", fontsize=7, color="#555")
    a.set_title("Tokens per suite run: estimated agent vs Argus", fontsize=10)
    a.set_xlabel("run #"), a.set_ylabel("tokens")
    a.set_ylim(0, max(naive) * 1.3)
    a.set_xticks(x), a.legend(fontsize=8, frameon=False, loc="upper left")

    b.bar(x, replayed, color="#2da44e", label="T0 deterministic replay")
    b.bar(x, healed, bottom=replayed, color="#bf8700", label="T1 self-healed (0 LLM)")
    b.set_title("Steps per run: replayed vs self-healed", fontsize=10)
    b.set_xlabel("run #"), b.set_ylabel("steps"), b.set_xticks(x)
    other = [r["steps"] - r["t0_replayed"] - r["healed"] for r in runs]
    b.bar(x, other, bottom=[p + h for p, h in zip(replayed, healed)], color="#8250df",
          label="other tiers (reorder / new field / retired)")
    b.set_ylim(0, max(r["steps"] for r in runs) * 1.35)
    b.legend(fontsize=8, frameon=False, loc="upper left", ncol=1)
    for ax in (a, b):
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("10 consecutive runs across releases 1.0 → 1.1 → 1.2 (site_data/ten_runs.json)",
                 fontsize=9, color="#555")
    fig.tight_layout()
    fig.savefig(OUT / "ten-runs.png")
    plt.close(fig)


def saucedemo_chart() -> None:
    data = _load("saucedemo.json")
    data = data["users"]
    users = list(data["metrics"])
    order = ["PASS", "NEEDS_REVIEW", "BUG"]
    colors = {"PASS": "#2da44e", "NEEDS_REVIEW": "#bf8700", "BUG": "#cf222e"}
    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150)
    left = [0] * len(users)
    for verdict in order:
        vals = [data["metrics"][u]["counts"].get(verdict, 0) for u in users]
        ax.barh(users, vals, left=left, color=colors[verdict], label=verdict)
        left = [l + v for l, v in zip(left, vals)]
    ax.invert_yaxis()
    ax.set_xlabel("verdicts over 8 journeys (0 LLM calls per user)")
    ax.set_title("SauceDemo (public site): same suite, five built-in test users", fontsize=10)
    ax.legend(fontsize=8, frameon=False, ncol=3, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "saucedemo.png")
    plt.close(fig)


def thumbnails() -> None:
    for name, src in SHOTS.items():
        img = Image.open(ROOT / src).convert("RGB")
        img.thumbnail((1280, 1280))
        img.save(OUT / name, optimize=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    ten_runs_chart()
    saucedemo_chart()
    thumbnails()
    print("wrote", sorted(p.name for p in OUT.iterdir()))
