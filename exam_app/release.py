from __future__ import annotations

import argparse
from pathlib import Path

from exam_app.state import RELEASES, ReleaseError, store

BASE_DIR = Path(__file__).resolve().parent.parent
RELEASE_DIR = BASE_DIR / "exam" / "releases"
CHANGELOG_PATH = BASE_DIR / "exam" / "context" / "CHANGELOG.md"


def write_changelog(release: str = store.release) -> Path:
    """Write release notes through the selected release, newest first."""
    if release not in RELEASES:
        raise ReleaseError(f"Unknown release: {release}")
    target_index = RELEASES.index(release)
    sections = ["# Changelog", ""]
    for item in reversed(RELEASES[: target_index + 1]):
        sections.append(f"## {item}")
        note_path = RELEASE_DIR / f"{item}.md"
        note = note_path.read_text(encoding="utf-8").strip() if note_path.exists() else ""
        if note:
            sections.extend((note, ""))
    CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHANGELOG_PATH.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    return CHANGELOG_PATH


def switch_release(release: str) -> None:
    """Switch the process-local exam state and refresh its changelog."""
    store.switch_release(release)
    write_changelog(release)


def main() -> None:
    """Activate a release from the command line."""
    parser = argparse.ArgumentParser(description="Switch the MediQueue exam release")
    parser.add_argument("release", choices=RELEASES)
    args = parser.parse_args()
    switch_release(args.release)
    print(f"Activated {args.release}; reset data and wrote {CHANGELOG_PATH}")


if __name__ == "__main__":
    main()
