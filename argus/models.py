"""Argus data contracts.

Every module (browser, healing, runner, triage, memory, explore, report, chaos) speaks
in these pydantic models. Keep this file the single source of truth: if you need a new
field, add it here with a default so older JSON on disk still loads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------------------
# Perception: what the browser layer extracts from a live page
# --------------------------------------------------------------------------------------

class BBox(BaseModel):
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0


class ElementInfo(BaseModel):
    """One element of a PageSnapshot. `ref` is ephemeral (valid only for that snapshot)."""
    ref: str                                  # "e12" -> window.__argus.refs[12]
    tag: str
    role: str = ""                            # explicit or implicit ARIA role
    name: str = ""                            # accessible name (approximation)
    text: str = ""                            # visible innerText, trimmed to 80 chars
    attrs: dict[str, str] = Field(default_factory=dict)  # id, name, type, class, href, placeholder,
    #                                          aria-label, title, alt, value, data-testid, for, required, min, max...
    label: str = ""                           # associated <label> text for form controls
    context: str = ""                         # nearest row / card heading / dialog / form / landmark text
    neighbor_text: str = ""                   # text of nearby elements (Similo "neighbor texts")
    xpath: str = ""                           # absolute xpath at snapshot time
    css: str = ""                             # reasonably unique css path at snapshot time
    bbox: Optional[BBox] = None               # page coordinates
    visible: bool = True
    enabled: bool = True
    editable: bool = False
    checked: Optional[bool] = None
    interactive: bool = True                  # False for context anchors (headings, alerts)
    in_dialog: bool = False


class PageSnapshot(BaseModel):
    url: str
    title: str = ""
    elements: list[ElementInfo] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)   # visible h1..h3
    alerts: list[str] = Field(default_factory=list)     # role=alert/status, aria-live, validation msgs, toasts
    text_digest: str = ""                               # first ~1500 chars of visible body text
    signature: str = ""                                 # structural hash of (role,name) of interactive elements
    viewport: dict[str, int] = Field(default_factory=lambda: {"w": 1280, "h": 800})

    def by_ref(self, ref: str) -> Optional[ElementInfo]:
        for e in self.elements:
            if e.ref == ref:
                return e
        return None


class Fingerprint(BaseModel):
    """Persistent, multi-attribute identity of a target element (Similo-style).

    Built from an ElementInfo at record time; healed fingerprints replace it after a
    verified run. Never store ephemeral refs here.
    """
    tag: str = ""
    role: str = ""
    name: str = ""
    text: str = ""
    attrs: dict[str, str] = Field(default_factory=dict)
    label: str = ""
    context: str = ""
    neighbor_text: str = ""
    xpath: str = ""
    css: str = ""
    bbox: Optional[BBox] = None

    @classmethod
    def from_element(cls, e: ElementInfo) -> "Fingerprint":
        return cls(tag=e.tag, role=e.role, name=e.name, text=e.text, attrs=dict(e.attrs),
                   label=e.label, context=e.context, neighbor_text=e.neighbor_text,
                   xpath=e.xpath, css=e.css, bbox=e.bbox)

    def describe(self) -> str:
        """Short human/LLM readable description, e.g. `button "Next" (in: Mission details)`."""
        label = self.name or self.text or self.label or self.attrs.get("placeholder", "") or self.attrs.get("id", "")
        ctx = f" (in: {self.context[:60]})" if self.context else ""
        return f'{self.role or self.tag} "{label[:60]}"{ctx}'


# --------------------------------------------------------------------------------------
# Effects: what an action did (auto-captured, no LLM) - the falsifiable step contract
# --------------------------------------------------------------------------------------

class NetCall(BaseModel):
    method: str
    path: str                                 # normalized: /api/missions/:id
    status: int = 0                           # 0 = failed / no response
    resource_type: str = ""                   # fetch / xhr / document


class Effects(BaseModel):
    url_before: str = ""
    url_after: str = ""
    url_changed: bool = False
    title_after: str = ""
    new_headings: list[str] = Field(default_factory=list)
    new_alerts: list[str] = Field(default_factory=list)
    network: list[NetCall] = Field(default_factory=list)      # fetch/xhr + document navigations
    console_errors: list[str] = Field(default_factory=list)
    page_errors: list[str] = Field(default_factory=list)      # uncaught exceptions
    dialogs: list[str] = Field(default_factory=list)          # alert/confirm texts (auto-accepted)
    dom_changed: bool = False                                  # snapshot signature changed


# --------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------

AssertionKind = Literal[
    "text_visible",       # params: {text}
    "text_absent",        # params: {text}
    "url_matches",        # params: {pattern}  (regex on path+query)
    "element_visible",    # params: {fingerprint: Fingerprint-json}
    "network_called",     # params: {method, path, status_class: "2xx"|"4xx"|"5xx"|"any"}
    "network_absent",     # params: {method, path, status_class}
    "no_page_errors",     # params: {}
    "value_equals",       # params: {fingerprint, value}
    "llm_check",          # params: {question}  - semantic check, used sparingly (costs a call)
]


class Assertion(BaseModel):
    kind: AssertionKind
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""                     # "Mission appears in the missions list"
    rule_ref: Optional[str] = None            # business rule id from PRODUCT.md, e.g. "R1"


ActionKind = Literal["goto", "click", "fill", "select", "check", "uncheck", "press", "hover", "wait"]


class StepExpect(BaseModel):
    """Expected effects, auto-captured from the baseline (green) run."""
    url_changed: Optional[bool] = None
    url_pattern: Optional[str] = None         # normalized path after the step, e.g. /missions/:id
    network: list[NetCall] = Field(default_factory=list)       # calls that must happen (method+path, status class)
    headings_any: list[str] = Field(default_factory=list)      # at least one of these headings appears
    assertions: list[Assertion] = Field(default_factory=list)  # explicit extra checks after the step


class Step(BaseModel):
    id: str
    intent: str                               # "Click 'Next' to open drone selection"
    action: ActionKind
    target: Optional[Fingerprint] = None      # None for goto/press(page)/wait
    value: Optional[str] = None               # fill/select/press value; supports ${unique}, ${creds.user}
    expect: StepExpect = Field(default_factory=StepExpect)
    optional: bool = False                    # e.g. dismiss a banner if present
    page_hint: str = ""                       # heading/title of the page where the step happens
    save_as: Optional[str] = None             # store the concrete filled value in run vars
    #                                           (assertions may reference ${vars.<name>})


class TestChange(BaseModel):
    version: int
    at: str = Field(default_factory=now_iso)
    kind: Literal["created", "healed", "updated", "retired", "reactivated"]
    summary: str
    diff: str = ""                            # unified-ish text diff of steps
    verdict_ref: Optional[str] = None         # run_id/test_id that caused it


class TestSpec(BaseModel):
    id: str
    name: str
    goal: str                                 # business goal in plain English
    tags: list[str] = Field(default_factory=list)
    start_url: str                            # path relative to app base url, e.g. "/login"
    steps: list[Step] = Field(default_factory=list)
    oracles: list[Assertion] = Field(default_factory=list)   # business postconditions at the end
    version: int = 1
    status: Literal["active", "retired", "draft"] = "active"
    origin: Literal["generated", "recorded", "manual"] = "generated"
    history: list[TestChange] = Field(default_factory=list)
    requires_login: bool = False


# --------------------------------------------------------------------------------------
# Results, observations, verdicts
# --------------------------------------------------------------------------------------

ObservationKind = Literal[
    "locator_healed",     # target found by similarity/LLM/vision instead of replay
    "step_reordered",     # executed a later step out of order (flow reordered)
    "step_added",         # replanner inserted a new step (new required field / new screen)
    "step_missing",       # step's target no longer exists and was skipped
    "effect_mismatch",    # expected url/network/heading did not happen
    "assertion_failed",   # explicit assertion / oracle failed
    "console_error",
    "page_error",         # uncaught JS exception
    "network_error",      # 5xx / failed request (4xx recorded as effect_mismatch unless unexpected)
    "goal_unreachable",   # could not continue the flow
    "timeout",
]


class Observation(BaseModel):
    kind: ObservationKind
    step_id: Optional[str] = None
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)   # candidates+scores, screenshots, net entries...
    tier: Optional[int] = None
    confidence: Optional[float] = None


VerdictCategory = Literal[
    "PASS",               # nothing changed
    "COSMETIC_DRIFT",     # selectors/layout/text moved; healed; business outcome intact
    "INTENDED_CHANGE",    # behaviour/flow changed, justified by product context / changelog
    "FEATURE_REMOVED",    # flow retired on purpose (changelog) -> test retired
    "BUG",                # regression: business outcome broken or hard error signals
    "NEEDS_REVIEW",       # not enough evidence either way; a human decides (decision is remembered)
    "INFRA",              # environment problem (app down, timeouts before first step)
]


class Verdict(BaseModel):
    category: VerdictCategory
    confidence: float = 1.0
    rationale: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    changelog_refs: list[str] = Field(default_factory=list)
    decided_by: Literal["rules", "llm", "human", "memory"] = "rules"
    action: Literal["none", "test_healed", "test_updated", "test_retired", "bug_reported", "review_requested"] = "none"


class LLMCallRecord(BaseModel):
    purpose: str                              # "heal", "replan", "triage", "generate", "explore"
    tier: str                                 # fast / smart / vision
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0                     # actual (free models = 0)
    list_cost_usd: float = 0.0                # what it would cost at reference paid prices
    latency_ms: int = 0
    cached: bool = False
    ok: bool = True


StepStatus = Literal["passed", "healed", "reordered", "added", "skipped", "failed"]


class StepResult(BaseModel):
    step_id: str
    intent: str = ""
    status: StepStatus
    tier: Optional[int] = None                # 0 replay, 1 similarity heal, 2 out-of-order, 3 LLM heal,
    #                                           4 LLM replan, 5 vision
    score: Optional[float] = None
    duration_ms: int = 0
    screenshot: Optional[str] = None          # relative path inside run dir
    observations: list[Observation] = Field(default_factory=list)
    effects: Optional[Effects] = None


class TestResult(BaseModel):
    test_id: str
    test_name: str = ""
    test_version: int = 1
    status: Literal["passed", "failed", "error", "retired"]
    verdict: Verdict
    steps: list[StepResult] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)   # test-level (oracles, console...)
    duration_ms: int = 0
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    updated_to_version: Optional[int] = None
    bug_report: Optional[str] = None          # markdown repro + evidence if BUG


class RunTotals(BaseModel):
    tests: int = 0
    passed: int = 0
    failed: int = 0
    healed_steps: int = 0
    replayed_steps: int = 0
    llm_calls: int = 0
    llm_calls_cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    list_cost_usd: float = 0.0
    naive_tokens_estimate: int = 0            # tokens an LLM-per-action agent would have spent
    verdicts: dict[str, int] = Field(default_factory=dict)
    tiers: dict[str, int] = Field(default_factory=dict)            # "0": 40, "1": 3, ...
    duration_ms: int = 0


class RunReport(BaseModel):
    run_id: str
    started_at: str = Field(default_factory=now_iso)
    app_url: str
    label: str = ""                           # free text, e.g. "after deploy v1.2"
    results: list[TestResult] = Field(default_factory=list)
    totals: RunTotals = Field(default_factory=RunTotals)
    learned: dict[str, Any] = Field(default_factory=dict)          # weight changes, new decisions
