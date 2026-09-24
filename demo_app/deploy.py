"""python -m demo_app.deploy <version> — switches version, resets data, rewrites CHANGELOG.md."""
from __future__ import annotations

import pathlib
import sys
import urllib.request
import urllib.error
import json

RELEASES_DIR = pathlib.Path(__file__).parent / "context" / "releases"
CHANGELOG_PATH = pathlib.Path(__file__).parent / "context" / "CHANGELOG.md"
VERSION_ORDER = ["1.0", "1.1", "1.2", "1.3"]


def write_changelog(version: str) -> None:
    """Write CHANGELOG.md with release notes up to and including version, newest first."""
    idx = VERSION_ORDER.index(version)
    versions_to_include = list(reversed(VERSION_ORDER[: idx + 1]))
    lines = ["# SkyOps Changelog\n\n"]
    for v in versions_to_include:
        rel_file = RELEASES_DIR / f"{v}.md"
        if rel_file.exists():
            content = rel_file.read_text(encoding="utf-8").strip()
            lines.append(content + "\n\n---\n\n")
    CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHANGELOG_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {CHANGELOG_PATH}")


def call_admin(path: str, payload: dict, port: int | str = 8000) -> dict:
    base = port if isinstance(port, str) else f"http://127.0.0.1:{port}"
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"Warning: could not reach server at {url}: {e}")
        return {}


def deploy(version: str, port: int | str = 8000) -> None:
    """Switch the running SkyOps to `version`, reset its data and publish that release's changelog."""
    call_admin("/__admin/version", {"version": version}, port)
    call_admin("/__admin/chaos", {"seed": None}, port)
    call_admin("/__admin/reset", {}, port)
    write_changelog(version)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m demo_app.deploy <version>")
        sys.exit(1)
    version = sys.argv[1]
    if version not in VERSION_ORDER:
        print(f"Invalid version: {version}. Must be one of {VERSION_ORDER}")
        sys.exit(1)

    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

    print(f"Deploying version {version}...")
    call_admin("/__admin/version", {"version": version}, port)
    call_admin("/__admin/reset", {}, port)
    write_changelog(version)
    print(f"Version {version} deployed.")


if __name__ == "__main__":
    main()
