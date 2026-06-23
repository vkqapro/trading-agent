"""Optional git commit/push helpers for workflow jobs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

from src.config import LOGGER, SETTINGS


def maybe_commit_and_push(repo_root: Path, paths: Iterable[Path], message: str) -> bool:
    """Commit and push workflow outputs when AUTO_GIT_PUSH is enabled."""
    if not SETTINGS.auto_git_push:
        LOGGER.info("AUTO_GIT_PUSH disabled; skipping commit/push for: %s", message)
        return False

    relative_paths = [str(path.relative_to(repo_root)) for path in paths if path.exists()]
    if not relative_paths:
        return False

    _run_git(["git", "add", *relative_paths], repo_root)
    status = _run_git(["git", "status", "--short", "--", *relative_paths], repo_root).strip()
    if not status:
        LOGGER.info("No workflow changes to commit for: %s", message)
        return False

    _run_git(["git", "commit", "-m", message], repo_root)
    _run_git(["git", "push", "origin", SETTINGS.git_branch], repo_root)
    return True


def _run_git(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    if result.stdout:
        LOGGER.info(result.stdout.strip())
    if result.stderr:
        LOGGER.info(result.stderr.strip())
    return result.stdout
