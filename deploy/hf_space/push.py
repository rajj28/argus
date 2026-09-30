"""Publish the Argus Space `rajj28/argus` from this working repo.

    HF_TOKEN=hf_... .venv/Scripts/python.exe deploy/hf_space/push.py [--dry-run]

Creates the Space if it does not exist (Docker SDK), uploads the repo minus secrets and local
artefacts, then puts `deploy/hf_space/Dockerfile` at the Space root as `Dockerfile` and
`deploy/hf_space/README.md` as the root `README.md` (the Spaces card with the YAML header).
The token is read from the environment only; `.env` is never read, printed or uploaded.
This script is operator tooling - it is never run automatically.
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPACE_ID = "rajj28/argus"

# Secrets, local runtime state and heavy/generated artefacts - none of this belongs in a Space.
EXCLUDES: tuple[str, ...] = (
    ".env", ".env.*",
    ".venv/*", ".venv",
    ".git/*", ".git",
    ".argus", ".argus/*", ".argus-*/*", ".argus_*/*",
    ".logs", ".logs/*", ".logs-*",
    ".pytest_cache/*",
    "node_modules/*", "**/node_modules/*",
    "**/__pycache__", "**/__pycache__/*", "**/*.pyc",
    "*.egg-info", "*.egg-info/*", "argus_qa.egg-info/*",
    "sandbox/out/*",
    "exam/results/raw_*.json",                    # raw per-question dumps (scorecard + budget stay)
    "exam/results/scorecard_before_fixes.*",
    "deploy/.env",
    "**/*.log",
    ".DS_Store", "Thumbs.db",
    "*.pdf", "*.zip", ".qoder/*",
    "runs/*", "argus/web/static/_review/*",        # recorded evidence; the console serves site_data/

)

IGNORE_PATTERNS: list[str] = [*EXCLUDES, "Dockerfile"]   # the root Dockerfile is uploaded separately


def _walk() -> list[Path]:
    """Repo files that survive EXCLUDES (relative to the repo root)."""
    kept: list[Path] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ("deploy/hf_space/Dockerfile",):
            continue                                # becomes the root Dockerfile instead
        if any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDES):
            continue
        kept.append(path)
    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list what would be uploaded and exit")
    args = parser.parse_args(argv)

    files = _walk()
    if args.dry_run:
        for path in files:
            print(path.relative_to(REPO_ROOT).as_posix())
        print(f"\n{len(files)} file(s); Dockerfile and README.md are taken from deploy/hf_space/")
        return 0

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("push: huggingface_hub is not installed (pip install huggingface_hub)", file=sys.stderr)
        return 1

    import os
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("push: set HF_TOKEN (a write token for rajj28)", file=sys.stderr)
        return 1

    api = HfApi(token=token)
    api.create_repo(repo_id=SPACE_ID, repo_type="space", space_sdk="docker", exist_ok=True)
    print(f"push: uploading {len(files)} file(s) to spaces/{SPACE_ID}")
    api.upload_folder(folder_path=REPO_ROOT, path_in_repo=".", repo_id=SPACE_ID,
                      repo_type="space", ignore_patterns=IGNORE_PATTERNS)
    api.upload_file(path_or_fileobj=REPO_ROOT / "deploy" / "hf_space" / "Dockerfile",
                    path_in_repo="Dockerfile", repo_id=SPACE_ID, repo_type="space")
    api.upload_file(path_or_fileobj=REPO_ROOT / "deploy" / "hf_space" / "README.md",
                    path_in_repo="README.md", repo_id=SPACE_ID, repo_type="space")
    print(f"push: done - https://huggingface.co/spaces/{SPACE_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
