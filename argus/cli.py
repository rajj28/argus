"""Argus CLI: the tireless hand in the browser.

  argus init --url http://localhost:8000 --context demo_app/context --user pilot@skyops.io --password flysafe123
  argus explore            # deterministic crawl -> app atlas (0 LLM calls)
  argus generate           # atlas + product context -> regression suite (1-3 LLM calls)
  argus author suite.json  # hand-written semantic steps -> baselined tests (0 LLM calls)
  argus run --label "after deploy 1.2"
  argus dashboard          # Mission Control UI
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from argus.config import load_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, help="Argus - autonomous, self-healing, cost-aware UI testing.")
console = Console()
HOME = typer.Option(".argus", "--home", help="Memory folder")

COLORS = {"PASS": "green", "COSMETIC_DRIFT": "cyan", "INTENDED_CHANGE": "blue", "FEATURE_REMOVED": "bright_black",
          "BUG": "bold red", "NEEDS_REVIEW": "yellow", "INFRA": "magenta"}
TIER = {0: "replay", 1: "heal", 2: "reorder", 3: "llm-heal", 4: "replan", 5: "vision"}


@app.command()
def init(url: str = typer.Option(..., help="Base URL of the app under test"),
         context: Optional[Path] = typer.Option(None, help="Folder with PRODUCT.md / CHANGELOG.md"),
         user: Optional[str] = None, password: Optional[str] = None, headed: bool = False, home: str = HOME):
    """Create .argus/config.json for an app."""
    s = load_settings(home)
    s.base_url = url.rstrip("/")
    s.context_dir = context
    if user:
        s.credentials = {"user": user, "password": password or ""}
    s.headless = not headed
    s.save()
    console.print(f"[green]Argus initialised[/] for {s.base_url} (memory: {s.home}, LLM: "
                  f"{'on' if s.llm_enabled else 'off'})")


@app.command()
def author(spec_file: Path, home: str = HOME):
    """Author tests from semantic step descriptions (JSON list) and record their baselines."""
    from argus.runner.author import author as _author
    s = load_settings(home)
    specs = json.loads(spec_file.read_text(encoding="utf-8"))
    tests = asyncio.run(_author(s, specs, log=console.print))
    console.print(f"[green]{len(tests)} test(s) baselined[/]")


@app.command()
def explore(max_states: int = 25, max_actions: int = 90, home: str = HOME):
    """Crawl the app deterministically and build the app atlas (no LLM)."""
    from argus.explore.explorer import explore as _explore
    s = load_settings(home)
    atlas = asyncio.run(_explore(s, max_states=max_states, max_actions=max_actions, log=console.print))
    console.print(f"[green]Atlas:[/] {len(atlas.get('states', {}))} states, "
                  f"{len(atlas.get('transitions', []))} transitions")


@app.command()
def generate(max_tests: int = 8, home: str = HOME):
    """Generate a regression suite from the atlas + product context."""
    from argus.explore.generator import generate as _generate
    s = load_settings(home)
    tests = asyncio.run(_generate(s, max_tests=max_tests, log=console.print))
    for t in tests:
        console.print(f"  [cyan]{t.id}[/] {t.name} - {len(t.steps)} steps, {len(t.oracles)} oracles {t.tags}")


@app.command()
def run(label: str = "", test: Optional[list[str]] = typer.Option(None, "--test"), no_update: bool = False,
        headed: bool = False, home: str = HOME):
    """Run the suite: replay, self-heal, adapt, triage."""
    from argus.runner.runner import run_suite
    s = load_settings(home)
    if headed:
        s.headless = False

    def on_result(r):
        v = r.verdict
        tiers = {}
        for st in r.steps:
            if st.tier is not None:
                tiers[TIER.get(st.tier, st.tier)] = tiers.get(TIER.get(st.tier, st.tier), 0) + 1
        calls = sum(1 for c in r.llm_calls if not c.cached)
        extra = f" -> v{r.updated_to_version}" if r.updated_to_version else ""
        console.print(f"[{COLORS.get(v.category, 'white')}]{v.category:<16}[/] {r.test_name:<42} "
                      f"{tiers}  llm={calls}  {r.duration_ms / 1000:.1f}s{extra}")
        if v.category not in ("PASS",):
            console.print(f"      [dim]{v.rationale[:220]}[/]")
        for q in v.changelog_refs[:2]:
            console.print(f"      [blue]cites:[/] \"{q[:120]}\"")

    console.rule(f"[bold]Argus run[/] {label}")
    report = asyncio.run(run_suite(s, test_ids=test, label=label, update=not no_update, on_result=on_result))
    _summary(report)


def _summary(report) -> None:
    t = report.totals
    table = Table(show_header=False, box=None)
    steps = sum(t.tiers.values()) or 1
    table.add_row("tests", str(t.tests), "verdicts", ", ".join(f"{k}:{v}" for k, v in t.verdicts.items()))
    table.add_row("steps replayed (T0)", f"{t.replayed_steps}/{steps}", "self-healed", str(t.healed_steps))
    table.add_row("LLM calls", f"{t.llm_calls} (+{t.llm_calls_cached} cached)", "tokens",
                  f"{t.tokens_in + t.tokens_out:,}")
    saved = max(0, t.naive_tokens_estimate - t.tokens_in - t.tokens_out)
    table.add_row("cost", f"${t.cost_usd:.4f} (list ${t.list_cost_usd:.4f})", "tokens avoided vs LLM-per-step",
                  f"{saved:,} ({saved / max(1, t.naive_tokens_estimate):.0%})")
    table.add_row("duration", f"{t.duration_ms / 1000:.1f}s", "run", report.run_id)
    console.print(table)
    bugs = [r for r in report.results if r.bug_report]
    for r in bugs:
        console.print(f"\n[bold red]{r.bug_report}[/]")


@app.command()
def status(home: str = HOME):
    """Show tests in memory and recent runs."""
    from argus.memory.store import Memory
    s = load_settings(home)
    m = Memory(s.home)
    table = Table("test", "status", "version", "steps", "oracles", "tags")
    for t in m.list_tests(None):
        table.add_row(t.id, t.status, str(t.version), str(len(t.steps)), str(len(t.oracles)), ",".join(t.tags))
    console.print(table)
    runs = m.list_runs()[-8:]
    for r in runs:
        console.print(f"{r.run_id}  {r.label:<24} {dict(r.totals.verdicts)}  llm={r.totals.llm_calls}")


@app.command()
def approve(run_id: str, test_id: str, as_: str = typer.Option("intended", "--as", help="intended | bug"),
            home: str = HOME):
    """Human-in-the-loop: resolve a NEEDS_REVIEW verdict; the decision is remembered."""
    from argus.memory.store import Memory
    from argus.models import TestChange, TestSpec, Verdict
    from argus.triage.triage import all_observations, deviation_signature
    s = load_settings(home)
    m = Memory(s.home)
    report = next(r for r in m.list_runs() if r.run_id == run_id)
    res = next(r for r in report.results if r.test_id == test_id)
    cat = "INTENDED_CHANGE" if as_.startswith("int") else "BUG"
    v = Verdict(category=cat, confidence=1.0, decided_by="human", rationale=f"Human decision on run {run_id}",
                action="test_updated" if cat == "INTENDED_CHANGE" else "bug_reported")
    m.put_decision(deviation_signature(test_id, all_observations(res)), v)
    proposal = s.home / "runs" / run_id / "proposals" / f"{test_id}.json"
    if cat == "INTENDED_CHANGE" and proposal.exists():
        spec = TestSpec.model_validate_json(proposal.read_text(encoding="utf-8"))
        saved = m.save_test(spec, TestChange(version=spec.version + 1, kind="updated",
                                             summary="Approved by a human reviewer", verdict_ref=run_id))
        console.print(f"[blue]{test_id} updated to v{saved.version}[/]")
    console.print(f"[green]Decision remembered:[/] {cat}")


@app.command()
def gauntlet(trials: int = 6, bugs: bool = True, home: str = HOME):
    """Robustness benchmark: random UI mutations (must heal, no false bugs) + injected bugs (must catch)."""
    from argus.chaos.gauntlet import run_gauntlet
    s = load_settings(home)
    asyncio.run(run_gauntlet(s, trials=trials, bugs=bugs, log=console.print))


@app.command()
def dashboard(port: int = 8765, home: str = HOME):
    """Serve the Mission Control dashboard."""
    import uvicorn
    from argus.report.dashboard import create_app
    s = load_settings(home)
    console.print(f"Mission Control on http://localhost:{port}")
    uvicorn.run(create_app(s.home), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def export(out: Path = Path("playwright-tests"), home: str = HOME):
    """Export the suite as standard Playwright Test files."""
    from argus.export.playwright_export import export_all
    s = load_settings(home)
    export_all(s.home, out, s.base_url) if export_all.__code__.co_argcount >= 3 else export_all(s.home, out)
    console.print(f"[green]Exported to {out}[/]")


@app.command()
def mcp(home: str = HOME):
    """Serve Argus as an MCP server (stdio) for coding agents."""
    from argus.mcp_server import serve
    serve(home)


if __name__ == "__main__":
    app()
