"""Unit tests for argus.explore.boundary (fake LLM, pure transformations, no network/browser)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from argus import models
from argus.config import Settings
from argus.explore import boundary as B
from argus.llm import prompts as P
from argus.memory.store import Memory
from argus.models import Assertion, Fingerprint, Step

# --------------------------------------------------------------------------------------
# fixtures: a hand-authored mission wizard test, exactly as `argus author` freezes it
# --------------------------------------------------------------------------------------

class FakeLLM:
    """Return one canned response; records every `json` call."""

    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    async def json(self, **kw) -> dict:
        self.calls.append(kw)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def fp(**kw) -> Fingerprint:
    base = dict(tag="input", role="textbox", name="", text="", label="", attrs={}, context="")
    base.update(kw)
    return Fingerprint(**base)


def make_test(**kw):
    """The `altitude-limit` journey: fill altitude, click Next, assert the rejection message."""
    steps = [
        Step(id="s1", intent="Name the mission", action="fill",
             target=fp(role="textbox", name="Mission name *", label="Mission name *",
                       attrs={"name": "name", "id": "field-name"}, context="Mission details"),
             value="Altitude check ${unique}"),
        Step(id="s2", intent="Continue from mission details", action="click",
             target=fp(tag="button", role="button", name="Continue", context="Mission details")),
        Step(id="s3", intent="Enter an illegal altitude of 500 m", action="fill",
             target=fp(role="spinbutton", name="Altitude (m) *", label="Altitude (m) *",
                       attrs={"type": "number", "name": "altitude", "min": "10", "max": "120"},
                       context="Flight parameters"),
             value="500"),
        Step(id="s4", intent="Set speed to 8 m/s", action="fill",
             target=fp(role="spinbutton", name="Speed (m/s) *", label="Speed (m/s) *",
                       attrs={"type": "number", "name": "speed", "max": "15"},
                       context="Flight parameters"),
             value="8"),
        Step(id="s5", intent="Try to continue from flight parameters", action="click",
             target=fp(tag="button", role="button", name="Continue", context="Flight parameters")),
    ]
    oracles = [
        Assertion(kind="text_visible", params={"text": "Altitude must be at most 120 m"},
                  description="Altitude limit error is shown", rule_ref="R1"),
        Assertion(kind="network_absent",
                  params={"method": "POST", "path": "/api/missions", "status_class": "2xx"},
                  description="No mission is created", rule_ref="R1"),
    ]
    base = dict(id="altitude-limit", name="Altitude above 120 m is rejected",
                goal="The planner must reject an altitude above the 120 m DGCA limit",
                tags=["negative", "compliance"], start_url="/missions/new",
                steps=steps, oracles=oracles, requires_login=True)
    base.update(kw)
    return models.TestSpec(**base)


def a_case(**kw) -> dict:
    case = {"rule_ref": "R1", "field_find": {"role": "spinbutton", "name": "altitude"},
            "value": "120", "expect": "accept", "why": "at most 120 m AGL is still legal"}
    case.update(kw)
    return case


# --------------------------------------------------------------------------------------
# step_field / find_field_index
# --------------------------------------------------------------------------------------

def test_step_field_names_the_control_like_a_human():
    step = make_test().steps[2]
    field = B.step_field(step)
    assert field == {"role": "spinbutton", "name": "altitude (m) *", "type": "number",
                     "alias": "altitude", "context": "flight parameters"}


def test_step_field_is_empty_without_a_target():
    assert B.step_field(Step(id="s1", intent="wait", action="wait")) == {}


def test_find_field_index_matches_role_name_context_and_type():
    steps = make_test().steps
    assert B.find_field_index(steps, {"name": "altitude"}) == 2
    assert B.find_field_index(steps, {"role": "spinbutton", "name": "altitude"}) == 2
    assert B.find_field_index(steps, {"name": "altitude", "context": "flight parameters"}) == 2
    assert B.find_field_index(steps, {"type": "number", "name": "speed"}) == 3      # value-agnostic
    assert B.find_field_index(steps, {"name": "altitude", "context": "mission details"}) is None
    assert B.find_field_index(steps, {"role": "checkbox"}) is None


def test_find_field_index_is_case_insensitive_and_falls_back_to_the_html_alias():
    steps = make_test().steps
    assert B.find_field_index(steps, {"name": "ALTITUDE"}) == 2
    # the html name attribute ("altitude") still finds a control the UI only calls "Ceiling"
    relabelled = [s.model_copy(update={"target": s.target.model_copy(
        update={"name": "Ceiling", "label": "Ceiling", "attrs": {"type": "number", "name": "altitude"}})})
        if s.id == "s3" else s for s in steps]
    assert B.find_field_index(relabelled, {"name": "altitude"}) == 2
    assert B.find_field_index(relabelled, {"name": "ceiling"}) == 2


def test_find_field_index_none_for_empty_or_unmatched_find():
    steps = make_test().steps
    assert B.find_field_index(steps, {}) is None
    assert B.find_field_index(steps, {"name": "   "}) is None
    assert B.find_field_index(steps, {"name": "battery"}) is None


# --------------------------------------------------------------------------------------
# test_digest
# --------------------------------------------------------------------------------------

def test_test_digest_is_semantic_json_with_fields_and_oracles():
    data = json.loads(B.test_digest(make_test()))
    assert data["id"] == "altitude-limit"
    assert data["start_url"] == "/missions/new"
    assert [s["action"] for s in data["steps"]] == ["fill", "click", "fill", "fill", "click"]
    assert data["steps"][2]["field"]["name"] == "altitude (m) *"
    assert data["steps"][2]["value"] == "500"
    assert data["oracles"][0]["rule_ref"] == "R1"
    assert "xpath" not in json.dumps(data)      # no selectors, no ephemeral refs


def test_test_digest_truncates():
    long_test = make_test(goal="x" * 5000)
    assert B.test_digest(long_test, max_chars=200).endswith("... (truncated)")


# --------------------------------------------------------------------------------------
# parse_cases
# --------------------------------------------------------------------------------------

def test_parse_cases_normalises_dedupes_and_keeps_order():
    data = {"cases": [a_case(), a_case(value="121", expect="reject", why="Just above the limit"),
                      a_case()]}                                            # exact duplicate
    cases = B.parse_cases(data)
    assert [(c["value"], c["expect"]) for c in cases] == [("120", "accept"), ("121", "reject")]
    assert cases[1]["why"] == "just above the limit"     # normalised
    assert cases[0]["field_find"] == {"role": "spinbutton", "name": "altitude"}


def test_parse_cases_caps_at_eight():
    data = {"cases": [a_case(value=str(v), expect="accept" if v < 8 else "reject") for v in range(20)]}
    assert len(B.parse_cases(data)) == 8


def test_parse_cases_drops_unusable_entries():
    data = {"cases": [
        {"rule_ref": "R1", "field_find": {"name": "altitude"}, "value": "121", "expect": "reject"},
        {"rule_ref": "R1", "field_find": {"name": "altitude"}, "value": "121", "expect": "maybe"},
        {"rule_ref": "R1", "field_find": {"name": "altitude"}, "expect": "accept"},            # no value
        {"rule_ref": "R1", "field_find": {"name": "altitude"}, "value": None, "expect": "accept"},
        {"rule_ref": "R1", "field_find": {}, "value": "121", "expect": "reject"},              # no field
        {"rule_ref": "R1", "value": "121", "expect": "reject"},                                # no find
        "not a dict",
    ]}
    cases = B.parse_cases(data)
    assert len(cases) == 1
    assert cases[0]["value"] == "121"
    assert cases[0]["rule_ref"] == "R1"


def test_parse_cases_survives_a_fenced_or_string_payload():
    payload = json.dumps({"cases": [a_case()]})
    assert len(B.parse_cases(payload)) == 1
    assert len(B.parse_cases("```json\n" + payload + "\n```")) == 1
    assert B.parse_cases("{not json") == []
    assert B.parse_cases(None) == []


def test_parse_cases_defaults_missing_rule_ref_to_none():
    case = B.parse_cases({"cases": [a_case(rule_ref="")]})[0]
    assert case["rule_ref"] is None
    assert case["why"] == "at most 120 m agl is still legal"


# --------------------------------------------------------------------------------------
# oracles
# --------------------------------------------------------------------------------------

def test_positive_oracles_drops_rejection_markers_and_network_absent():
    base = make_test(oracles=[
        Assertion(kind="text_visible", params={"text": "Altitude must be at most 120 m"}, rule_ref="R1"),
        Assertion(kind="network_absent", params={"method": "POST", "path": "/api/missions",
                                                 "status_class": "2xx"}, rule_ref="R1"),
        Assertion(kind="text_visible", params={"text": "Mission saved"}),
    ])
    keep = B.positive_oracles(base)
    assert [a.params["text"] for a in keep] == ["Mission saved"]


def test_case_oracles_reject_uses_the_rules_own_error_text():
    oracles = B.case_oracles(a_case(value="121", expect="reject"), make_test())
    assert len(oracles) == 1
    assert oracles[0].kind == "text_visible"
    assert oracles[0].params["text"] == "Altitude must be at most 120 m"
    assert oracles[0].rule_ref == "R1"
    assert "121" in oracles[0].description


def test_case_oracles_reject_falls_back_to_an_absent_post():
    base = make_test(oracles=[Assertion(kind="url_matches", params={"pattern": "/dashboard"})])
    oracles = B.case_oracles(a_case(value="121", expect="reject"), base)
    assert oracles[0].kind == "network_absent"
    assert oracles[0].params == {"method": "POST", "path": "/api/missions", "status_class": "2xx"}


def test_case_oracles_reject_reuses_the_base_post_path_when_there_is_one():
    base = make_test(oracles=[Assertion(kind="network_absent",
                                        params={"method": "PUT", "path": "/api/settings",
                                                "status_class": "2xx"})])
    assert B.case_oracles(a_case(value="121", expect="reject"), base)[0].params["path"] == "/api/settings"


def test_case_oracles_accept_keeps_the_base_positive_oracles():
    base = make_test(oracles=[Assertion(kind="network_called",
                                        params={"method": "POST", "path": "/api/missions",
                                                "status_class": "2xx"}),
                              Assertion(kind="text_visible", params={"text": "Altitude must be at most 120 m"})])
    keep = B.case_oracles(a_case(expect="accept"), base)
    assert [o.kind for o in keep] == ["network_called"]


def test_case_oracles_error_text_prefers_the_matching_rule():
    base = make_test(oracles=[
        Assertion(kind="text_visible", params={"text": "Speed must be at most 15 m/s"}, rule_ref="R8"),
        Assertion(kind="text_visible", params={"text": "Altitude must be at most 120 m"}, rule_ref="R1"),
    ])
    assert B.case_oracles(a_case(expect="reject"), base)[0].params["text"] == "Altitude must be at most 120 m"
    assert B.case_oracles(a_case(expect="reject", rule_ref="R8"), base)[0].params["text"] == "Speed must be at most 15 m/s"


# --------------------------------------------------------------------------------------
# case_to_spec
# --------------------------------------------------------------------------------------

def test_case_to_spec_replaces_only_the_matched_value():
    base = make_test()
    spec = B.case_to_spec(a_case(), base)
    assert spec is not None
    assert [s.value for s in spec.steps] == ["Altitude check ${unique}", None, "120", "8", None]
    assert spec.steps[2].intent == "Enter an illegal altitude of 120 m [boundary accept 120]"
    assert spec.steps[0] == base.steps[0]                     # untouched steps are identical
    assert spec.id == "altitude-limit-accept-120"
    assert spec.start_url == "/missions/new"
    assert spec.requires_login is True
    assert spec.origin == "generated"
    assert base.steps[2].value == "500"                        # the base test is never mutated


def test_case_to_spec_deep_copies_targets():
    base = make_test()
    spec = B.case_to_spec(a_case(), base)
    spec.steps[0].target.name = "Mutated"
    assert base.steps[0].target.name == "Mission name *"


def test_case_to_spec_tags_boundary_and_rule_and_oracle_refs():
    reject = B.case_to_spec(a_case(value="121", expect="reject"), make_test())
    assert reject is not None
    assert "boundary" in reject.tags and "R1" in reject.tags and "negative" in reject.tags
    assert all(o.rule_ref == "R1" for o in reject.oracles)

    accept = B.case_to_spec(a_case(), make_test())
    assert accept is not None
    assert "boundary" in accept.tags and "R1" in accept.tags
    assert "negative" not in accept.tags
    assert accept.name == "Altitude above 120 m is rejected [accept 120]"
    assert accept.goal.endswith("(boundary: 120 must be accepted)")


def test_case_to_spec_none_when_the_field_is_not_reachable():
    assert B.case_to_spec(a_case(field_find={"name": "battery %"}), make_test()) is None
    assert B.case_to_spec(a_case(field_find={}), make_test()) is None


def test_case_to_spec_accept_has_no_oracle_when_the_base_exposes_none():
    spec = B.case_to_spec(a_case(), make_test())
    assert spec is not None and spec.oracles == []       # record_baseline then freezes the step contract


# --------------------------------------------------------------------------------------
# propose (fake LLM: one call, tier and purpose)
# --------------------------------------------------------------------------------------

def _settings(**kw) -> Settings:
    kw.setdefault("home", Path("."))
    return Settings(base_url="http://127.0.0.1:8000", llm_enabled=True, **kw)


def test_propose_makes_exactly_one_smart_call_with_rules_and_test():
    llm = FakeLLM({"cases": [a_case(), a_case(value="121", expect="reject")]})
    cases = asyncio.run(B.propose(_settings(), make_test(), llm))
    assert [(c["value"], c["expect"]) for c in cases] == [("120", "accept"), ("121", "reject")]
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["tier"] == "smart"
    assert call["purpose"] == "boundary"
    assert call["system"] == P.BOUNDARY_SYSTEM
    assert "120 m AGL" in call["user"] or "no business rules" in call["user"]


def test_propose_prompt_carries_the_test_and_only_the_rule_lines(tmp_path):
    (tmp_path / "PRODUCT.md").write_text(
        "# SkyOps\n\n## Business Rules\n\n| ID | Rule |\n|----|------|\n"
        "| R1 | Maximum altitude is **120 m AGL** (DGCA regulation). |\n\nSome marketing prose.\n",
        encoding="utf-8")
    llm = FakeLLM({"cases": [a_case()]})
    asyncio.run(B.propose(_settings(context_dir=tmp_path), make_test(), llm))
    user = llm.calls[0]["user"]
    assert "Maximum altitude is **120 m AGL**" in user
    assert "Some marketing prose." not in user
    assert '"field": {' in user and '"value": "500"' in user


def test_prompts_are_json_only_and_format_cleanly():
    for template in (P.BOUNDARY_SYSTEM, P.BOUNDARY_USER):
        assert "{{" not in template and "}}" not in template
    user = P.BOUNDARY_USER.format(rules="R1 | altitude", test='{"steps": []}')
    assert '{"steps": []}' in user
    assert "{rules}" not in user and "{test}" not in user
    assert '"cases"' in P.BOUNDARY_SYSTEM and "at most 8" in P.BOUNDARY_SYSTEM
    assert 'expect' in P.BOUNDARY_SYSTEM and "field_find" in P.BOUNDARY_SYSTEM


# --------------------------------------------------------------------------------------
# generate_boundary_tests: fake browser, fake baseline, real Memory
# --------------------------------------------------------------------------------------

class FakeBrowser:
    async def close(self) -> None:
        return None


class FakeChromium:
    def __init__(self, browser: FakeBrowser):
        self._browser = browser

    def launch(self, **kw):
        async def _go() -> FakeBrowser:
            return self._browser
        return _go()


class FakePlaywright:
    def __init__(self, browser: FakeBrowser):
        self.chromium = FakeChromium(browser)

    async def __aenter__(self) -> "FakePlaywright":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """A memory home holding the base test, with the browser, the login and the baseline faked out."""
    import argus.explore.boundary as B

    Memory(tmp_path).save_test(make_test())
    monkeypatch.setattr(B, "async_playwright", lambda: FakePlaywright(FakeBrowser()))

    async def fake_auth(browser, ctx):
        return str(tmp_path / "auth.json")

    async def fake_baseline(spec, ctx, browser, auth):
        # Mimic record_baseline: keep the oracles that hold on the current build.
        if spec.id.endswith("reject-0"):            # this value is simply wrong on this build
            spec = spec.model_copy(update={"oracles": [
                Assertion(kind="no_page_errors", description="No uncaught errors during the journey")]})
        return spec

    monkeypatch.setattr(B, "ensure_auth", fake_auth)
    monkeypatch.setattr(B, "record_baseline", fake_baseline)
    monkeypatch.setattr("argus.llm.client.LLMClient", lambda s: FakeLLM({
        "cases": [a_case(),                                            # 120 -> accept
                  a_case(value="121", expect="reject"),               # 121 -> reject (holds)
                  a_case(value="0", expect="reject"),                 # never holds on this build
                  a_case(value="120", expect="accept",
                         field_find={"name": "altitude"}),             # same test, looser find
                  a_case(field_find={"name": "battery"}, value="29", expect="reject")]  # unmapped
    }))
    return _settings(home=tmp_path)


def test_generate_boundary_tests_keeps_only_what_holds(wired, tmp_path):
    tests = asyncio.run(B.generate_boundary_tests(wired, "altitude-limit", log=lambda *a: None))
    assert [t.id for t in tests] == ["altitude-limit-accept-120", "altitude-limit-reject-121"]
    assert all(t.history[-1].kind == "created" for t in tests)
    assert "Rule-boundary case from altitude-limit" in tests[0].history[-1].summary
    assert "R1 120 must be accepted" in tests[0].history[-1].summary
    saved = {t.id for t in Memory(tmp_path).list_tests(None)}
    assert saved == {"altitude-limit", "altitude-limit-accept-120", "altitude-limit-reject-121"}


def test_generate_boundary_tests_unknown_test_raises(wired):
    with pytest.raises(LookupError):
        asyncio.run(B.generate_boundary_tests(wired, "nope", log=lambda *a: None))


def test_generate_boundary_tests_without_llm_returns_empty(wired):
    wired.llm_enabled = False
    assert asyncio.run(B.generate_boundary_tests(wired, "altitude-limit", log=lambda *a: None)) == []


def test_generate_boundary_tests_survives_a_failing_llm(wired, monkeypatch):
    monkeypatch.setattr("argus.llm.client.LLMClient",
                        lambda s: FakeLLM(RuntimeError("provider down")))
    assert asyncio.run(B.generate_boundary_tests(wired, "altitude-limit", log=lambda *a: None)) == []
