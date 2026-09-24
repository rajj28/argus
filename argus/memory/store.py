"""JSON-backed persistent memory (`.argus/`): tests + version history, stability,
decisions, atlas and runs. Every write is atomic (tmp file + `os.replace`), UTF-8,
pretty-printed. Memory is *verified-only*: callers commit heals/decisions only after a
non-BUG verdict.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from argus.models import Fingerprint, RunReport, TestChange, TestSpec, Verdict

# Attribute set used by stability learning and the direct-fingerprint comparison.
_COMPARED_ATTRS = (
    "id", "name_attr", "testid", "class", "text", "name", "label",
    "xpath", "css", "role", "tag", "context", "placeholder", "href",
)
_DIRECT_FIELDS = {"tag", "role", "name", "text", "label", "context", "xpath", "css"}
_TESTID_KEYS = ("data-testid", "data-test", "data-test-id", "data-cy", "data-qa")


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write `data` as pretty JSON atomically (temporary file + `os.replace`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _attr_value(fp: Fingerprint, attr: str) -> str:
    """Read one compared attribute from a Fingerprint (direct field or attrs entry)."""
    if attr == "testid":
        for key in _TESTID_KEYS:
            if fp.attrs.get(key):
                return fp.attrs[key]
        return ""
    if attr in _DIRECT_FIELDS:
        return str(getattr(fp, attr, "") or "")
    key = "name" if attr == "name_attr" else attr
    return fp.attrs.get(key, "")


def _direct_compare(old: Fingerprint, new: Fingerprint) -> dict[str, bool]:
    """Attr -> unchanged (True) by direct field/attrs comparison."""
    return {a: _attr_value(old, a) == _attr_value(new, a) for a in _COMPARED_ATTRS}


def _changed_map(old: Fingerprint, new: Fingerprint) -> dict[str, bool]:
    """Attr -> unchanged; defer to `argus.healing.similarity` when it exists."""
    try:
        from argus.healing.similarity import changed_attributes  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError):
        return _direct_compare(old, new)
    return changed_attributes(old, new)


class Memory:
    """JSON files under `home` (`.argus` by default)."""

    def __init__(self, home: Path | str):
        self.home = Path(home)
        self.tests_dir = self.home / "tests"
        self.history_dir = self.tests_dir / "_history"
        self._knowledge = self._load_json(self.home / "knowledge.json", {})
        self._knowledge.setdefault("stability", {})
        self._knowledge.setdefault("decisions", {})
        self._atlas = self._load_json(self.home / "atlas.json", {})

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _save_knowledge(self) -> None:
        _atomic_write_json(self.home / "knowledge.json", self._knowledge)

    # ------------------------------------------------------------------ tests
    def list_tests(self, status: str | None = "active") -> list[TestSpec]:
        if not self.tests_dir.exists():
            return []
        tests: list[TestSpec] = []
        for path in sorted(self.tests_dir.glob("*.json")):
            try:
                test = TestSpec.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if status is None or test.status == status:
                tests.append(test)
        return tests

    def load_test(self, test_id: str) -> TestSpec:
        path = self.tests_dir / f"{test_id}.json"
        return TestSpec.model_validate_json(path.read_text(encoding="utf-8"))

    def save_test(self, test: TestSpec, change: TestChange | None = None) -> TestSpec:
        """Save a test; with a `change`, archive the previous version and bump version."""
        path = self.tests_dir / f"{test.id}.json"
        if change is None:
            _atomic_write_json(path, test.model_dump(mode="json"))
            return test
        try:
            old = self.load_test(test.id)
        except (OSError, ValueError):
            old = None
        old_version = old.version if old else test.version
        if old is not None:
            _atomic_write_json(
                self.history_dir / f"{test.id}.v{old_version}.json", old.model_dump(mode="json")
            )
        new_version = old_version + 1 if old is not None else test.version
        change = change.model_copy(update={"version": new_version})
        history = list(test.history)
        history.append(change)
        test = test.model_copy(update={"version": new_version, "history": history})
        _atomic_write_json(path, test.model_dump(mode="json"))
        return test

    # ------------------------------------------------------------ stability
    def stability(self) -> dict[str, float]:
        """Beta(1,1) posterior mean of "unchanged" per attribute; unseen attrs default 1.0."""
        counts = self._knowledge.get("stability", {})
        out: dict[str, float] = {}
        for attr in _COMPARED_ATTRS:
            c = counts.get(attr, {})
            same = int(c.get("same", 0))
            changed = int(c.get("changed", 0))
            if same + changed == 0:
                out[attr] = 1.0  # unseen attribute: assume stable
            else:
                out[attr] = (same + 1.0) / (same + changed + 2.0)
        return out

    def learn_from_heal(self, old: Fingerprint, new: Fingerprint) -> dict[str, bool]:
        """Learn per-attribute stability from a verified heal; returns attr -> unchanged."""
        unchanged = _changed_map(old, new)
        stability = self._knowledge.setdefault("stability", {})
        for attr, is_same in unchanged.items():
            bucket = stability.setdefault(attr, {"same": 0, "changed": 0})
            key = "same" if is_same else "changed"
            bucket[key] = int(bucket.get(key, 0)) + 1
        self._save_knowledge()
        return unchanged

    # ------------------------------------------------------------- decisions
    def get_decision(self, sig: str) -> Verdict | None:
        raw = self._knowledge.get("decisions", {}).get(sig)
        if not raw:
            return None
        try:
            return Verdict.model_validate(raw)
        except ValueError:
            return None

    def put_decision(self, sig: str, verdict: Verdict) -> None:
        self._knowledge.setdefault("decisions", {})[sig] = verdict.model_dump(mode="json")
        self._save_knowledge()

    # ------------------------------------------------------------------ atlas
    def atlas(self) -> dict:
        return dict(self._atlas)

    def save_atlas(self, atlas: dict) -> None:
        self._atlas = atlas
        _atomic_write_json(self.home / "atlas.json", atlas)

    # ------------------------------------------------------------------- runs
    def new_run_dir(self) -> tuple[str, Path]:
        """Create `runs/<YYYYmmdd-HHMMSS>-<4hex>/screenshots` and return (run_id, dir)."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for _ in range(10):
            run_id = f"{stamp}-{secrets.token_hex(2)}"
            run_dir = self.home / "runs" / run_id
            try:
                (run_dir / "screenshots").mkdir(parents=True, exist_ok=True)
                return run_id, run_dir
            except OSError:
                continue
        raise OSError(f"could not create a run directory under {self.home / 'runs'}")

    def save_run(self, report: RunReport) -> None:
        run_dir = self.home / "runs" / report.run_id
        (run_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        _atomic_write_json(run_dir / "report.json", report.model_dump(mode="json"))

    def list_runs(self) -> list[RunReport]:
        if not (self.home / "runs").exists():
            return []
        runs: list[RunReport] = []
        for path in sorted((self.home / "runs").glob("*/report.json")):
            try:
                runs.append(RunReport.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        runs.sort(key=lambda r: r.started_at)
        return runs