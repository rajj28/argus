"""Export TestSpecs as self-contained Playwright Test (.spec.ts) files.

Locator preference (best available first, per docs/SPEC.md §9): getByTestId ->
getByRole(role, {name}) -> getByLabel -> getByPlaceholder -> getByText ->
locator(css). Every step is commented with its intent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from argus.models import Assertion, Fingerprint, Step, TestSpec

TESTID_ATTRS = ("data-testid", "data-test", "data-test-id", "data-cy", "data-qa")
DEFAULT_BASE_URL = "http://localhost:8000"


def to_playwright_ts(test: TestSpec, base_url: str = DEFAULT_BASE_URL) -> str:
    """Render `test` as a valid Playwright Test file (TS)."""
    lines = [
        "import { test, expect } from '@playwright/test';",
        "",
        f"// {test.name} - {test.goal}",
        f'test("{_ts_str(test.name)}", async ({{ page }}) => {{',
    ]
    first = True
    for step in test.steps:
        if not first:
            lines.append("")
        first = False
        lines.append(f"  // {step.intent}")
        lines.append(f"  {_action_line(step, base_url)}")
        for effect_line in _expect_lines(step):
            lines.append(f"  {effect_line}")
    for oracle in test.oracles:
        lines.append("")
        lines.append(f"  // Oracle: {oracle.description or oracle.kind}")
        for effect_line in _assertion_lines(oracle):
            lines.append(f"  {effect_line}")
    lines.append("});")
    return "\n".join(lines) + "\n"


def export_all(home: Path, out_dir: Path) -> list[Path]:
    """Write one <test_id>.spec.ts under out_dir for every TestSpec in <home>/tests."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_url = default_base_url(home)
    written: list[Path] = []
    for spec in _load_specs(home):
        path = out_dir / f"{spec.id}.spec.ts"
        path.write_text(to_playwright_ts(spec, base_url), encoding="utf-8")
        written.append(path)
    return written


def default_base_url(home: Path) -> str:
    """Read base_url from <home>/config.json, else default."""
    cfg = Path(home) / "config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if data.get("base_url"):
                return str(data["base_url"])
        except (OSError, ValueError):
            pass
    return DEFAULT_BASE_URL


def _load_specs(home: Path) -> list[TestSpec]:
    base = Path(home) / "tests"
    if not base.is_dir():
        return []
    specs: list[TestSpec] = []
    for f in sorted(base.glob("*.json")):
        try:
            specs.append(TestSpec.model_validate_json(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return specs


def _ts_str(value: str) -> str:
    """Escape a string for a double-quoted TS string literal."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _url(base_url: str, value: str) -> str:
    """Join an app-relative path to a base url unless already absolute."""
    if value.startswith(("http://", "https://")):
        return value
    sep = "" if value.startswith("/") else "/"
    return base_url.rstrip("/") + sep + value


def _locator(target: Fingerprint) -> str:
    """Best available locator: testid -> role+name -> label -> placeholder -> text -> css."""
    for key in TESTID_ATTRS:
        if target.attrs.get(key):
            return f'page.getByTestId("{_ts_str(target.attrs[key])}")'
    if target.role:
        name = target.name or target.text or target.label
        if name:
            return (
                f'page.getByRole("{_ts_str(target.role)}", '
                f'{{ name: "{_ts_str(name)}" }})'
            )
    if target.label:
        return f'page.getByLabel("{_ts_str(target.label)}")'
    placeholder = target.attrs.get("placeholder")
    if placeholder:
        return f'page.getByPlaceholder("{_ts_str(placeholder)}")'
    if target.text:
        return f'page.getByText("{_ts_str(target.text)}")'
    if target.css:
        return f'page.locator("{_ts_str(target.css)}")'
    if target.xpath:
        return f'page.locator("xpath={_ts_str(target.xpath)}")'
    if target.name:
        return f'page.getByText("{_ts_str(target.name)}")'
    raise ValueError(f"cannot build a locator for target {target}")


def _action_line(step: Step, base_url: str) -> str:
    """Render the Playwright statement for one step action."""
    action = step.action
    if action == "goto":
        return f'await page.goto("{_ts_str(_url(base_url, step.value or ""))}");'
    if action == "wait":
        ms = step.value if step.value and step.value.isdigit() else "500"
        return f"await page.waitForTimeout({ms});"
    if action == "press" and step.target is None:
        key = step.value or "Enter"
        return f'await page.keyboard.press("{_ts_str(key)}");'
    if step.target is None:
        return f"// step {step.id}: no target for action '{action}'"
    expr = _locator(step.target)
    if action == "click":
        return f"await {expr}.click();"
    if action == "fill":
        return f'await {expr}.fill("{_ts_str(step.value or "")}");'
    if action == "select":
        return f'await {expr}.selectOption("{_ts_str(step.value or "")}");'
    if action == "check":
        return f"await {expr}.check();"
    if action == "uncheck":
        return f"await {expr}.uncheck();"
    if action == "press":
        return f'await {expr}.press("{_ts_str(step.value or "Enter")}");'
    if action == "hover":
        return f"await {expr}.hover();"
    return f"// unsupported action: {action}"


def _expect_lines(step: Step) -> list[str]:
    """Assertions implied by step.expect (url pattern + explicit assertions)."""
    lines: list[str] = []
    if step.expect.url_pattern:
        lines.append(f"await expect(page).toHaveURL({_url_regex_literal(step.expect.url_pattern)});")
    for assertion in step.expect.assertions:
        lines.extend(_assertion_lines(assertion))
    return lines


def _assertion_lines(assertion: Assertion) -> list[str]:
    """Render a deterministic (non-LLM) assertion, or a comment if not portable."""
    kind = assertion.kind
    params = assertion.params
    if kind == "text_visible":
        text = params.get("text", "")
        return [f'await expect(page.getByText("{_ts_str(text)}")).toBeVisible();']
    if kind == "text_absent":
        text = params.get("text", "")
        return [f'await expect(page.getByText("{_ts_str(text)}")).toBeHidden();']
    if kind == "url_matches":
        pattern = params.get("pattern", "")
        return [f"await expect(page).toHaveURL(new RegExp(\"{_ts_str(pattern)}\"));"]
    if kind == "element_visible":
        raw = params.get("fingerprint")
        if isinstance(raw, dict):
            try:
                target = Fingerprint.model_validate(raw)
            except ValueError:
                target = None
            if target is not None:
                return [f"await expect({_locator(target)}).toBeVisible();"]
    return [f"// assertion '{kind}' not exported: {assertion.description}"]


def _url_regex_literal(pattern: str) -> str:
    """RegExp literal for a normalized path pattern like '/missions/:id'."""
    if not pattern:
        return "/.*/"
    inner: list[str] = []
    for part in re.split(r"(:\w+)", pattern):
        if re.fullmatch(r":\w+", part):
            inner.append("[^/]+")
        else:
            inner.append(re.escape(part).replace("/", "\\/"))
    return "/" + "".join(inner) + "/"