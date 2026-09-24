"""Memory store tests (no network): round-trips, versioning/archives, stability math,
decisions, atlas, runs, atomic writes."""
from __future__ import annotations

import re

import pytest

from argus.memory.store import Memory
from argus.models import Fingerprint, RunReport, Verdict
from argus.models import TestChange as Change
from argus.models import TestSpec as Spec


def _test(test_id: str = "t1", version: int = 1, status: str = "active") -> Spec:
    return Spec(
        id=test_id,
        name="Sample test",
        goal="Complete a flow",
        start_url="/start",
        steps=[],
        oracles=[],
        version=version,
        status=status,  # type: ignore[arg-type]
    )


def _heal_change(kind: str = "healed", summary: str = "locator healed") -> Change:
    return Change(version=1, kind=kind, summary=summary)  # type: ignore[arg-type]


# --------------------------------------------------------------------- roundtrip
def test_save_load_roundtrip(tmp_path):
    memory = Memory(tmp_path)
    test = _test()
    memory.save_test(test)
    assert memory.load_test("t1") == test
    assert (tmp_path / "tests" / "t1.json").exists()


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Memory(tmp_path).load_test("nope")


def test_list_tests_filters_by_status(tmp_path):
    memory = Memory(tmp_path)
    memory.save_test(_test("a", status="active"))
    memory.save_test(_test("b", status="retired"))
    memory.save_test(_test("c", status="active"))
    assert [t.id for t in memory.list_tests()] == ["a", "c"]
    assert [t.id for t in memory.list_tests(None)] == ["a", "b", "c"]


# ----------------------------------------------------------------- versioning
def test_save_test_with_change_archives_and_bumps(tmp_path):
    memory = Memory(tmp_path)
    old = _test()
    memory.save_test(old)
    new = memory.save_test(old, change=_heal_change())
    assert new.version == 2
    assert len(new.history) == 1
    assert new.history[0].version == 2
    assert new.history[0].kind == "healed"

    archive = tmp_path / "tests" / "_history" / "t1.v1.json"
    assert archive.exists()
    assert Spec.model_validate_json(archive.read_text(encoding="utf-8")) == old

    on_disk = Spec.model_validate_json((tmp_path / "tests" / "t1.json").read_text(encoding="utf-8"))
    assert on_disk.version == 2
    assert len(on_disk.history) == 1


def test_sequential_versions_archive_each(tmp_path):
    memory = Memory(tmp_path)
    test = _test()
    memory.save_test(test)  # v1, no archive yet
    test = memory.save_test(test, change=_heal_change("healed", "a"))  # v2, archive v1
    test = memory.save_test(test, change=_heal_change("updated", "b"))  # v3, archive v2
    assert test.version == 3
    assert (tmp_path / "tests" / "_history" / "t1.v1.json").exists()
    assert (tmp_path / "tests" / "_history" / "t1.v2.json").exists()


def test_save_test_without_change_does_not_bump(tmp_path):
    memory = Memory(tmp_path)
    memory.save_test(_test())
    memory.save_test(_test())
    assert memory.load_test("t1").version == 1
    history_dir = tmp_path / "tests" / "_history"
    assert list(history_dir.glob("*")) == [] if history_dir.exists() else True


# ------------------------------------------------------------------- stability
def test_stability_defaults_to_one(tmp_path):
    stability = Memory(tmp_path).stability()
    assert stability["role"] == 1.0
    assert stability["name"] == 1.0
    assert stability["xpath"] == 1.0
    assert len(stability) == 14


def test_learn_from_heal_identical_updates_same(tmp_path):
    memory = Memory(tmp_path)
    old = Fingerprint(tag="button", name="Save", attrs={"id": "save-btn"})
    new = Fingerprint(tag="button", name="Save", attrs={"id": "save-btn"})
    unchanged = memory.learn_from_heal(old, new)
    assert unchanged["name"] is True and unchanged["id"] is True and unchanged["text"] is True
    assert memory.stability()["name"] == pytest.approx((1 + 1) / (1 + 0 + 2))


def test_learn_from_heal_differs_and_persists(tmp_path):
    memory = Memory(tmp_path)
    same = Fingerprint(tag="button", name="Save", attrs={"id": "save-btn"})
    memory.learn_from_heal(same, same)
    changed = Fingerprint(tag="button", name="Save Document", attrs={"id": "save-btn"})
    unchanged = memory.learn_from_heal(same, changed)
    assert unchanged["name"] is False
    assert unchanged["id"] is True
    stability = memory.stability()
    assert stability["name"] == pytest.approx((1 + 1) / (1 + 1 + 2))  # 0.5
    assert stability["id"] == pytest.approx((2 + 1) / (2 + 0 + 2))  # 0.75
    assert Memory(tmp_path).stability()["id"] == pytest.approx((2 + 1) / (2 + 0 + 2))


def test_learn_from_heal_testid_via_attrs(tmp_path):
    memory = Memory(tmp_path)
    old = Fingerprint(role="button", attrs={"data-testid": "save"})
    new = Fingerprint(role="button", attrs={"data-test": "save"})
    assert memory.learn_from_heal(old, new)["testid"] is True


def test_learn_from_heal_name_attr(tmp_path):
    old = Fingerprint(tag="input", attrs={"name": "email"})
    new = Fingerprint(tag="input", attrs={"name": "email"})
    assert Memory(tmp_path).learn_from_heal(old, new)["name_attr"] is True


# ------------------------------------------------------------------ decisions
def test_decisions_roundtrip_and_persist(tmp_path):
    memory = Memory(tmp_path)
    verdict = Verdict(
        category="INTENDED_CHANGE",
        confidence=0.8,
        rationale="changelog: v1.2 changed flow",
        changelog_refs=["v1.2 changed flow"],
        decided_by="llm",
        action="test_updated",
    )
    memory.put_decision("sig-1", verdict)
    assert memory.get_decision("sig-1") == verdict
    assert memory.get_decision("missing") is None
    assert Memory(tmp_path).get_decision("sig-1").category == "INTENDED_CHANGE"


# ----------------------------------------------------------------------- atlas
def test_atlas_roundtrip(tmp_path):
    memory = Memory(tmp_path)
    atlas = {"states": {"s1": {"url_pattern": "/a", "visits": 3}}, "transitions": [{"from": "s1", "to": "s2"}]}
    memory.save_atlas(atlas)
    assert memory.atlas() == atlas
    assert Memory(tmp_path).atlas() == atlas


# ------------------------------------------------------------------------ runs
def test_new_run_dir_creates_screenshots(tmp_path):
    memory = Memory(tmp_path)
    run_id, run_dir = memory.new_run_dir()
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", run_id)
    assert run_dir == tmp_path / "runs" / run_id
    assert (run_dir / "screenshots").is_dir()


def test_save_run_and_list_runs_sorted_by_time(tmp_path):
    memory = Memory(tmp_path)
    r1 = RunReport(run_id="20260101-000000-aaaa", app_url="http://x", started_at="2026-01-01T00:00:00")
    r3 = RunReport(run_id="20260103-000000-ccaa", app_url="http://x", started_at="2026-01-03T00:00:00")
    r2 = RunReport(run_id="20260102-000000-bbaa", app_url="http://x", started_at="2026-01-02T00:00:00")
    for report in (r1, r3, r2):
        memory.save_run(report)
    runs = memory.list_runs()
    assert [r.run_id for r in runs] == [
        "20260101-000000-aaaa",
        "20260102-000000-bbaa",
        "20260103-000000-ccaa",
    ]
    assert runs[0].app_url == "http://x"


# ---------------------------------------------------------------- atomicity
def test_no_tmp_leftovers(tmp_path):
    memory = Memory(tmp_path)
    memory.save_test(_test())
    memory.save_test(_test(), change=_heal_change())
    memory.save_run(RunReport(run_id="x", app_url="http://x"))
    assert list(tmp_path.rglob("*.tmp")) == []