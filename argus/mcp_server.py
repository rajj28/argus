"""Argus as an MCP server: coding agents write the feature, Argus verifies it in a real browser.

Register with any MCP client, e.g. Claude Code:  claude mcp add argus -- argus mcp
"""
from __future__ import annotations

import json
from pathlib import Path

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from argus.config import load_settings


def _report_digest(report) -> dict:
    t = report.totals
    return {
        "run_id": report.run_id,
        "verdicts": t.verdicts,
        "llm_calls": t.llm_calls,
        "healed_steps": t.healed_steps,
        "tests": [{"test": r.test_name, "verdict": r.verdict.category, "confidence": r.verdict.confidence,
                   "why": r.verdict.rationale, "changelog_refs": r.verdict.changelog_refs,
                   "bug_report": r.bug_report} for r in report.results],
    }


def build(home: str = ".argus") -> FastMCP:
    mcp = FastMCP("argus", instructions=(
        "Argus runs the app's end-to-end UI regression suite in a real browser, self-heals broken locators, "
        "and classifies every deviation as BUG, INTENDED_CHANGE, FEATURE_REMOVED or NEEDS_REVIEW. Call "
        "verify_change after implementing a feature, passing a description of what you intended to change."))

    @mcp.tool()
    async def run_tests(label: str = "mcp run") -> str:
        """Run the full suite and return verdicts per test (JSON)."""
        from argus.runner.runner import run_suite
        report = await run_suite(load_settings(home), label=label)
        return json.dumps(_report_digest(report), indent=2)

    @mcp.tool()
    async def verify_change(description: str) -> str:
        """Verify a change you just made. `description` is your intent (like a PR description); Argus uses it
        as the oracle for 'intended change vs regression' and reports bugs with repro steps."""
        from argus.runner.runner import run_suite
        s = load_settings(home)
        s.pending_change = description
        report = await run_suite(s, label=f"verify: {description[:40]}")
        return json.dumps(_report_digest(report), indent=2)

    @mcp.tool()
    async def explore_and_generate(max_tests: int = 6) -> str:
        """For an app with no tests: explore it and generate a baselined regression suite."""
        from argus.explore.explorer import explore
        from argus.explore.generator import generate
        s = load_settings(home)
        atlas = await explore(s, log=lambda *a: None)
        tests = await generate(s, atlas=atlas, max_tests=max_tests, log=lambda *a: None)
        return json.dumps([{"id": t.id, "name": t.name, "steps": len(t.steps)} for t in tests], indent=2)

    @mcp.tool()
    def last_report() -> str:
        """Verdicts of the most recent run."""
        from argus.memory.store import Memory
        runs = Memory(load_settings(home).home).list_runs()
        return json.dumps(_report_digest(runs[-1]), indent=2) if runs else "no runs yet"

    return mcp


def serve(home: str = ".argus") -> None:
    build(home).run()
