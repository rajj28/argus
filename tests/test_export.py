"""Tests for argus/export/playwright_export.py (no network, no LLM)."""
from __future__ import annotations

import json
from pathlib import Path

from argus.export.playwright_export import default_base_url, export_all, to_playwright_ts
from argus import models
from argus.models import Assertion, Fingerprint, Step

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://skyops.example.com"


def _load_spec(name: str) -> models.TestSpec:
    path = FIXTURES / "sample_tests" / f"{name}.json"
    return models.TestSpec.model_validate_json(path.read_text(encoding="utf-8"))


def test_login_export_locators():
    ts = to_playwright_ts(_load_spec("login_flow"), BASE)
    assert 'test("Operator login"' in ts
    assert 'await page.goto("https://skyops.example.com/login")' in ts
    assert 'page.getByTestId("email").fill("${creds.user}")' in ts
    assert 'page.getByRole("textbox", { name: "Password" }).fill("${creds.password}")' in ts
    assert 'page.getByRole("button", { name: "Sign in" }).click()' in ts
    assert "await expect(page).toHaveURL(/\\/login/)" in ts
    assert 'await expect(page.getByText("Your missions")).toBeVisible()' in ts
    assert "// Type the operator email" in ts
    assert "// Submit the sign-in form" in ts


def test_mission_export_locators():
    ts = to_playwright_ts(_load_spec("mission_flow"), BASE)
    assert 'page.getByRole("link", { name: "New mission" }).click()' in ts
    assert 'page.getByRole("textbox", { name: "Mission name" }).fill("Perimeter sweep")' in ts
    assert 'page.getByRole("combobox", { name: "Priority" }).selectOption("high")' in ts
    assert 'page.getByRole("checkbox", { name: "Send briefing by email" }).check()' in ts
    assert 'await page.keyboard.press("Enter")' in ts
    assert r"toHaveURL(/\/missions\/[^/]+/)" in ts
    assert "await page.waitForTimeout(800)" in ts
    assert 'await expect(page.getByText("Mission created")).toBeVisible()' in ts
    assert 'new RegExp("/missions/\\\\d+")' in ts
    assert 'await expect(page.getByText("Perimeter sweep")).toBeVisible()' in ts


def _fb_spec() -> models.TestSpec:
    """A synthetic spec exercising the remaining locator fallbacks."""
    steps = [
        Step(id="s1", intent="placeholder input", action="fill",
             target=Fingerprint(tag="input", role="textbox", attrs={"placeholder": "Search fleets"}),
             value="heron"),
        Step(id="s2", intent="text fallback", action="click",
             target=Fingerprint(tag="div", text="Apply filter", css=".filter")),
        Step(id="s3", intent="css fallback", action="click",
             target=Fingerprint(tag="div", css="#sidebar .toggle")),
        Step(id="s4", intent="label fallback", action="fill",
             target=Fingerprint(tag="input", role="", label="Drone id"), value="d7"),
        Step(id="s5", intent="uncheck a box", action="uncheck",
             target=Fingerprint(tag="input", role="checkbox", name="Notify me")),
        Step(id="s6", intent="hover a tile", action="hover",
             target=Fingerprint(tag="button", role="button", name="Map")),
        Step(id="s7", intent="fill via css key", action="fill",
             target=Fingerprint(tag="input", attrs={"placeholder": ""}, css="input[name='q']"),
             value="x"),
    ]
    oracles = [Assertion(kind="text_absent", params={"text": "Loading"}, description="loader gone")]
    return models.TestSpec(id="fallback", name="Fallback chain", goal="cover every locator hint",
                    start_url="/", steps=steps, oracles=oracles)


def test_export_fallback_chain():
    ts = to_playwright_ts(_fb_spec(), BASE)
    assert 'page.getByPlaceholder("Search fleets").fill("heron")' in ts
    assert 'page.getByText("Apply filter").click()' in ts
    assert 'page.locator("#sidebar .toggle").click()' in ts
    assert 'page.getByLabel("Drone id").fill("d7")' in ts
    assert 'page.getByRole("checkbox", { name: "Notify me" }).uncheck()' in ts
    assert 'page.getByRole("button", { name: "Map" }).hover()' in ts
    assert 'page.locator("input[name=\'q\']").fill("x")' in ts
    assert 'await expect(page.getByText("Loading")).toBeHidden()' in ts
    assert "// placeholder input" in ts
    assert "// Oracle: loader gone" in ts


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "tests").mkdir(parents=True)
    for spec in (_load_spec("login_flow"), _load_spec("mission_flow")):
        (home / "tests" / f"{spec.id}.json").write_text(spec.model_dump_json(), encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"base_url": BASE}), encoding="utf-8")
    return home


def test_export_all_writes_one_file_per_test(tmp_path):
    out = tmp_path / "out"
    files = export_all(_home(tmp_path), out)
    assert {p.name for p in files} == {"login_flow.spec.ts", "mission_flow.spec.ts"}
    ts = (out / "login_flow.spec.ts").read_text(encoding="utf-8")
    assert "https://skyops.example.com/login" in ts
    assert 'page.getByTestId("email")' in ts
    mission = (out / "mission_flow.spec.ts").read_text(encoding="utf-8")
    assert 'page.getByRole("link", { name: "New mission" })' in mission


def test_export_all_empty_home(tmp_path):
    assert export_all(tmp_path / "missing", tmp_path / "out") == []


def test_default_base_url_from_config(tmp_path):
    assert default_base_url(_home(tmp_path)) == BASE
    assert default_base_url(tmp_path / "missing") == "http://localhost:8000"