"""Unit tests for argus.explore (no network, no LLM)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from argus import models
from argus.config import Settings
from argus.explore.explorer import heuristic_value, input_kind, pick_option
from argus.explore.generator import (
    _deterministic_tests,
    _login_spec,
    atlas_digest,
    propose,
    to_spec,
)
from argus.llm import prompts as P


# --------------------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------------------

def make_el(**kw) -> models.ElementInfo:
    base = dict(ref="e0", tag="input", role="textbox", name="", text="", label="",
                attrs={}, visible=True, enabled=True, editable=True, interactive=True)
    base.update(kw)
    return models.ElementInfo(**base)


def make_fp(**kw) -> models.Fingerprint:
    base = dict(tag="button", role="button", name="Save", text="", label="", attrs={})
    base.update(kw)
    return models.Fingerprint(**base)


def el_entry(eid: str, fp: models.Fingerprint, enabled: bool = True) -> dict:
    return {"id": eid, "fp": fp.model_dump(mode="json"), "desc": fp.describe(), "enabled": enabled}


def state(sid: str, url: str, entry_url: str, path=None, elements=(), headings=("Page",),
          title: str = "Page") -> dict:
    return {"id": sid, "url": url, "entry_url": entry_url, "path": path or [],
            "title": title, "headings": list(headings), "alerts": [],
            "signature": f"sig-{sid}", "elements": list(elements), "forms": []}


def atlas(*states, transitions=(), meta=None) -> dict:
    return {"meta": meta or {"app": "http://localhost", "start_url": "/dashboard",
                             "start_state": "S1", "counts": {}},
            "states": {s["id"]: s for s in states},
            "transitions": list(transitions), "login": None}


def path_step(action: str, fp: models.Fingerprint, value=None) -> dict:
    return {"action": action, "target": fp.model_dump(mode="json"), "value": value,
            "intent": f"{action} '{fp.name}'"}


# --------------------------------------------------------------------------------------
# heuristic filler
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("attrs,role,tag,expected", [
    ({"type": "email"}, "textbox", "input", "email"),
    ({"type": "password"}, "textbox", "input", "password"),
    ({"type": "tel"}, "textbox", "input", "tel"),
    ({"type": "url"}, "textbox", "input", "url"),
    ({"type": "date"}, "textbox", "input", "date"),
    ({"type": "number", "min": "10", "max": "120"}, "spinbutton", "input", "number"),
    ({}, "textbox", "input", "text"),
    ({}, "", "textarea", "textarea"),
    ({"type": "radio"}, "radio", "input", "radio"),
    ({"type": "checkbox"}, "checkbox", "input", "checkbox"),
    ({"type": "search"}, "searchbox", "input", "search"),
    ({}, "combobox", "select", "select"),
])
def test_input_kind(attrs, role, tag, expected):
    assert input_kind(make_el(role=role, tag=tag, attrs=attrs)) == expected


def test_heuristic_email_password_and_defaults():
    creds = {"password": "flysafe123"}
    assert heuristic_value(make_el(attrs={"type": "email"}), creds) == "qa.bot@example.com"
    assert heuristic_value(make_el(attrs={"type": "password"}), creds) == "flysafe123"
    assert heuristic_value(make_el(attrs={"type": "password"}), {}) == "changeme123"
    assert heuristic_value(make_el(attrs={"type": "tel"}), {}) == "+919876543210"
    assert heuristic_value(make_el(attrs={"type": "url"}), {}) == "https://example.com"
    assert heuristic_value(make_el(tag="textarea"), {}) == "Automated exploration note"


def test_heuristic_name_uses_unique_and_plain_text():
    named = make_el(name="Mission name", attrs={"placeholder": "e.g. Morning patrol run"})
    assert heuristic_value(named, {}) == "Argus ${unique}"
    generic = make_el(name="Customer code", attrs={"placeholder": "Anything"})
    assert heuristic_value(generic, {}) == "Argus test"


def test_heuristic_number_midpoint_and_date():
    alt = make_el(attrs={"type": "number", "min": "10", "max": "120"})
    assert heuristic_value(alt, {}) == "65"
    assert heuristic_value(make_el(attrs={"type": "number"}), {}) == "5"
    assert heuristic_value(make_el(attrs={"type": "date"}), {}) == \
        (date.today() + timedelta(days=1)).isoformat()


def test_pick_option_skips_placeholders_and_empty_values():
    options = [
        {"value": "", "label": "Select site…"},
        {"value": "", "label": "Anything"},
        {"value": "Pune Depot", "label": "Pune Depot"},
        {"value": "Mumbai Port", "label": "Mumbai Port"},
    ]
    assert pick_option(options) == "Pune Depot"
    assert pick_option([{"value": "", "label": "Select type…"}]) is None
    assert pick_option([]) is None


# --------------------------------------------------------------------------------------
# digest
# --------------------------------------------------------------------------------------

def test_atlas_digest_shape():
    details = make_fp(tag="input", role="textbox", name="Mission name",
                      attrs={"required": "", "type": "text"})
    st = state("S3", "/missions/new", "http://x/missions/new",
               path=[path_step("fill", details, "Argus ${unique}")],
               elements=[el_entry("S3.e1", details)],
               headings=("Plan a mission",), title="Plan a mission")
    a = atlas(st, transitions=[{
        "from": "S2", "to": "S3", "action": "click", "intent": "New mission",
        "target": make_fp(name="New mission").model_dump(mode="json"),
        "network": ["POST /api/missions/validate 200"],
    }])
    text = atlas_digest(a)
    assert 'S3 /missions/new "Plan a mission" path: [fill \'Mission name\']' in text
    assert 'S3.e1 textbox "Mission name" {required}' in text
    assert 'S2 --click "New mission"--> S3 [POST /api/missions/validate 200]' in text


def test_atlas_digest_marks_disabled_and_truncates():
    disabled = make_fp(role="radio", name="Hawk-7 — Battery too low", tag="input",
                       attrs={"type": "radio"})
    st = state("S6", "/missions/new", "http://x/missions/new",
               elements=([el_entry("S6.e0", disabled, enabled=False)]
                         + [el_entry(f"S6.e{i}", make_fp(name=f"Item {i}")) for i in range(1, 200)]))
    full = atlas_digest(atlas(st))
    assert '{disabled}' in full
    short = atlas_digest(atlas(st), max_chars=140)
    assert "… (truncated)" in short
    assert len(short) < len(full)
    assert len(short) < 200


# --------------------------------------------------------------------------------------
# to_spec
# --------------------------------------------------------------------------------------

def test_to_spec_maps_fingerprints_and_oracles():
    fp = make_fp(name="Save settings", attrs={"data-js": "save-settings-btn"})
    st = state("S2", "/settings", "http://x/settings", elements=[el_entry("S2.e0", fp)])
    a = atlas(st)
    raw = {
        "name": "Update the display name", "goal": "Persist the display name",
        "tags": ["smoke"], "requires_login": True,
        "steps": [{"el": "S2.e0", "action": "click", "intent": "Save settings"}],
        "oracles": [{"kind": "url_matches", "params": {"pattern": "/settings"},
                     "description": "Stays on settings"}],
    }
    spec = to_spec(raw, a, 0)
    assert spec is not None
    assert spec.id == "update-the-display-name"
    assert spec.start_url == "/settings"
    assert spec.requires_login is True
    assert len(spec.steps) == 1
    assert spec.steps[0].action == "click"
    assert spec.steps[0].target == fp
    assert spec.steps[0].id == "s1"
    assert "smoke" in spec.tags
    assert spec.oracles[0].kind == "url_matches"


def test_to_spec_prepends_state_path():
    name_fp = make_fp(tag="input", role="textbox", name="Mission name")
    next_fp = make_fp(name="Next")
    launch_fp = make_fp(name="Launch mission", attrs={"data-js": "wizard-launch"})
    st = state("S6", "/missions/new", "http://x/missions/new",
               path=[path_step("fill", name_fp, "Argus ${unique}"),
                     path_step("click", next_fp)],
               elements=[el_entry("S6.e3", launch_fp)],
               headings=("Review & launch",), title="Plan a mission")
    a = atlas(st)
    raw = {
        "name": "Launch a mission", "goal": "Create a mission",
        "steps": [{"el": "S6.e3", "action": "click", "intent": "click 'Launch mission'"}],
    }
    spec = to_spec(raw, a, 0)
    assert spec is not None
    assert spec.start_url == "/missions/new"
    assert [s.action for s in spec.steps] == ["fill", "click", "click"]
    assert spec.steps[0].target == name_fp
    assert spec.steps[2].target == launch_fp
    assert [s.id for s in spec.steps] == ["s1", "s2", "s3"]


def test_to_spec_no_prepend_when_first_state_is_start_page():
    fp = make_fp(name="New mission")
    st = state("S1", "/dashboard", "http://x/dashboard", elements=[el_entry("S1.e0", fp)])
    a = atlas(st, meta={"start_url": "/dashboard", "start_state": "S1", "counts": {}})
    raw = {"name": "Open the new mission flow", "goal": "…",
           "steps": [{"el": "S1.e0", "action": "click", "intent": "New mission"}]}
    spec = to_spec(raw, a, 0)
    assert spec is not None
    assert spec.start_url == "/dashboard"
    assert [s.action for s in spec.steps] == ["click"]


def test_to_spec_bare_page_keeps_its_own_start_url():
    fp = make_fp(name="Export CSV")
    st = state("S4", "/logs", "http://x/logs", elements=[el_entry("S4.e0", fp)])
    a = atlas(st)
    raw = {"name": "Open the logs page", "goal": "…",
           "steps": [{"el": "S4.e0", "action": "click", "intent": "Open logs"}]}
    spec = to_spec(raw, a, 0)
    assert spec is not None
    assert spec.start_url == "/logs"
    assert len(spec.steps) == 1
    assert spec.steps[0].target == fp


def test_to_spec_negative_tag_from_name():
    fp = make_fp(tag="input", role="spinbutton", name="Altitude")
    st = state("S7", "/missions/new", "http://x/missions/new", elements=[el_entry("S7.e0", fp)])
    a = atlas(st)
    raw = {"name": "Altitude above 120 m is rejected", "goal": "R1",
           "steps": [{"el": "S7.e0", "action": "fill", "value": "150", "intent": "Set altitude"}]}
    spec = to_spec(raw, a, 0)
    assert spec is not None
    assert "negative" in spec.tags


def test_to_spec_keeps_explicit_negative_tag_and_requires_login_default():
    fp = make_fp(name="Thing")
    st = state("S2", "/things", "http://x/things", elements=[el_entry("S2.e0", fp)])
    a = atlas(st)
    raw = {"name": "Normal flow", "tags": ["negative", "critical"],
           "steps": [{"el": "S2.e0", "action": "click", "intent": "Go"}]}
    spec = to_spec(raw, a, 0)
    assert spec is not None
    assert "negative" in spec.tags
    assert spec.requires_login is True  # default for an authenticated app


def test_to_spec_drops_unknown_element_and_llm_check_oracle():
    fp = make_fp(name="Launch")
    st = state("S9", "/missions/new", "http://x/missions/new", elements=[el_entry("S9.e0", fp)])
    a = atlas(st)
    raw = {
        "name": "Launch", "goal": "…",
        "steps": [{"el": "S9.e0", "action": "click", "intent": "Go"},
                  {"el": "S9.zz", "action": "click", "intent": "Bogus"},
                  {"el": "S1.e0", "action": "fill", "value": "x"}],
        "oracles": [{"kind": "llm_check", "params": {"question": "is it fine?"}},
                    {"kind": "url_matches", "params": {"pattern": "/missions/new"},
                     "description": "Next panel"}],
    }
    spec = to_spec(raw, a, 0)
    assert spec is not None
    assert len(spec.steps) == 1
    assert [o.kind for o in spec.oracles] == ["url_matches"]


def test_to_spec_none_when_no_valid_steps():
    a = atlas(state("S1", "/dashboard", "http://x/dashboard"))
    raw = {"name": "Broken", "steps": [{"el": "S99.e0", "action": "click"}]}
    assert to_spec(raw, a, 0) is None


# --------------------------------------------------------------------------------------
# propose (offline fake LLM)
# --------------------------------------------------------------------------------------

class FakeLLM:
    """Return canned responses in order; records each `json` call."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def json(self, **kw) -> dict | str:
        self.calls.append(kw)
        if not self.responses:
            raise AssertionError("FakeLLM exhausted its responses")
        return self.responses.pop(0)


def _propose_settings() -> Settings:
    return Settings(home=Path("."), base_url="http://localhost:8000",
                    build="1.0", llm_enabled=True)


def test_propose_returns_wrapped_tests():
    fp = make_fp(name="Save settings", attrs={"data-js": "save-settings-btn"})
    st = state("S2", "/settings", "http://x/settings", elements=[el_entry("S2.e0", fp)])
    llm = FakeLLM([{"tests": [{"name": "A", "goal": "…"},
                              {"name": "B", "goal": "…"}]}])
    out = asyncio.run(propose(_propose_settings(), atlas(st), llm))
    assert [t["name"] for t in out] == ["A", "B"]
    assert len(llm.calls) == 1
    assert llm.calls[0]["tier"] == "smart"
    assert llm.calls[0]["purpose"] == "generate"


def test_propose_retries_on_fragment_then_succeeds():
    fp = make_fp(name="New mission")
    st = state("S1", "/dashboard", "http://x/dashboard", elements=[el_entry("S1.e0", fp)])
    llm = FakeLLM([{"el": "S0.e0", "action": "fill"},   # truncated fragment: no tests wrapper
                   {"tests": [{"name": "plan", "goal": "…"}]}])
    out = asyncio.run(propose(_propose_settings(), atlas(st), llm))
    assert [t["name"] for t in out] == ["plan"]
    assert len(llm.calls) == 2
    assert "was NOT a single" in llm.calls[1]["user"]


def test_propose_gives_up_after_three_bad_attempts():
    fp = make_fp(name="Thing")
    st = state("S2", "/things", "http://x/things", elements=[el_entry("S2.e0", fp)])
    llm = FakeLLM([{}, {"foo": 1}, '{"broken json":'])
    out = asyncio.run(propose(_propose_settings(), atlas(st), llm))
    assert out == []
    assert len(llm.calls) == 3


def test_propose_parses_string_json_response():
    fp = make_fp(name="Thing")
    st = state("S2", "/things", "http://x/things", elements=[el_entry("S2.e0", fp)])
    llm = FakeLLM(['```json\n{"tests": [{"name": "wrapped", "goal": "…"}]}\n```'])
    out = asyncio.run(propose(_propose_settings(), atlas(st), llm))
    assert [t["name"] for t in out] == ["wrapped"]


def test_generate_user_format_keeps_placeholders_and_escapes_braces():
    user = P.GENERATE_USER.format(product="SkyOps rules", atlas="S1 /dashboard")
    assert '{"kind":"url_matches"' in user          # dict-literal braces double-escaped
    assert "${unique}" in user                      # ${unique} survives .format()
    assert "${vars.mission_name}" in user
    assert "${creds.user}" in P.GENERATE_SYSTEM and "${creds.password}" in P.GENERATE_SYSTEM
    assert "{{" not in user                         # no residual double-braces
    assert "{product}" not in user and "{atlas}" not in user


def test_to_spec_keeps_element_state_oracle_with_rule_ref():
    disabled = make_fp(role="radio", name="Hawk-7 — Battery too low", tag="input",
                       attrs={"type": "radio"})
    next_fp = make_fp(name="Next")
    st = state("S7", "/missions/new", "http://x/missions/new",
               elements=[el_entry("S7.e0", disabled), el_entry("S7.e1", next_fp)])
    a = atlas(st)
    raw = {
        "name": "Low-battery drone is disabled", "goal": "R2", "tags": ["negative"],
        "steps": [{"el": "S7.e0", "action": "click", "intent": "Never interact"},
                  {"el": "S7.e1", "action": "click", "intent": "Next"}],
        "oracles": [{"kind": "element_state",
                     "params": {"fingerprint": disabled.model_dump(mode="json"),
                                "enabled": False},
                     "description": "drone radio stays disabled", "rule_ref": "R2"}],
    }
    spec = to_spec(raw, a, 0)
    assert spec is not None
    o = spec.oracles[0]
    assert o.kind == "element_state"
    assert o.rule_ref == "R2"
    assert o.params["enabled"] is False
    assert o.params["fingerprint"]["name"] == "Hawk-7 — Battery too low"
    email = make_fp(tag="input", role="textbox", name="Email address",
                    attrs={"type": "email"})
    pw = make_fp(tag="input", role="textbox", name="Password", attrs={"type": "password"})
    submit = make_fp(tag="button", role="button", name="Log in")
    login = state("S0", "/login", "http://x/login",
                  elements=[el_entry("S0.e0", email), el_entry("S0.e1", pw),
                            el_entry("S0.e2", submit)])
    a = atlas(login)
    a["login"] = login
    spec = _login_spec(a)
    assert spec is not None
    assert spec.id == "login"
    assert "auth" in spec.tags
    assert spec.requires_login is False
    assert spec.start_url == "/login"
    assert [s.action for s in spec.steps] == ["fill", "fill", "click"]
    assert spec.steps[0].value == "${creds.user}"
    assert spec.steps[1].value == "${creds.password}"
    assert spec.oracles[0].kind == "url_matches"
    assert spec.oracles[0].params["pattern"] == "/dashboard"


def test_login_spec_none_without_a_login_form():
    a = atlas(state("S3", "/dashboard", "http://x/dashboard"))
    a["login"] = state("S0", "/login", "http://x/login")
    assert _login_spec(a) is None


def test_deterministic_tests_smoke_and_wizard():
    missions = state("S2", "/missions", "http://x/missions", headings=("Missions",),
                     title="Missions")
    name_fp = make_fp(tag="input", role="textbox", name="Mission name")
    next_fp = make_fp(name="Next")
    launch_fp = make_fp(name="Launch mission", attrs={"data-js": "wizard-launch"})
    review = state("S8", "/missions/new", "http://x/missions/new",
                   path=[path_step("fill", name_fp, "Argus ${unique}"),
                         path_step("click", next_fp)],
                   elements=[el_entry("S8.e4", launch_fp)],
                   headings=("Review & launch",))
    a = atlas(missions, review)
    tests = _deterministic_tests(a)
    ids = {t.id for t in tests}
    assert ids == {"visit_missions", "wizard_launch"}

    smoke = next(t for t in tests if t.id == "visit_missions")
    assert smoke.start_url == "/missions"
    assert smoke.requires_login is True
    assert smoke.oracles[0].kind == "text_visible"
    assert smoke.oracles[0].params["text"] == "Missions"

    launch = next(t for t in tests if t.id == "wizard_launch")
    assert launch.start_url == "/missions/new"
    assert [s.action for s in launch.steps] == ["fill", "click", "click"]
    assert "critical" in launch.tags
    assert any(o.kind == "network_called" and o.params["path"] == "/api/missions"
               for o in launch.oracles)
    assert any(o.kind == "url_matches" for o in launch.oracles)
    assert not any(s.action == "goto" for s in smoke.steps)