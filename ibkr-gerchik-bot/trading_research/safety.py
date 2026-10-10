"""Fail-closed boundaries for Trading Research file and SQLite access."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import REPOSITORY_ROOT, configured_research_root


class ResearchSafetyError(PermissionError):
    """Raised when a research operation would escape its permitted boundary."""


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def research_root(*, repository_root: Path | str = REPOSITORY_ROOT) -> Path:
    """Resolve and validate the only directory in which research may write."""
    resolved_root = _resolved(configured_research_root())
    production_root = _resolved(repository_root)
    if _is_within(resolved_root, production_root):
        raise ResearchSafetyError(
            "The research workspace must be outside the production repository; "
            f"received {resolved_root}"
        )
    return resolved_root


def assert_research_write_path(
    path: Path | str,
    *,
    repository_root: Path | str = REPOSITORY_ROOT,
) -> Path:
    """Return a canonical write path only when it remains in the workspace.

    ``Path.resolve`` follows existing symlinks/junctions, so a path such as
    ``workspace/link-to-repo/output.json`` fails the containment check instead
    of silently escaping the research workspace.
    """
    allowed_root = research_root(repository_root=repository_root)
    supplied = Path(path).expanduser()
    candidate = _resolved(supplied if supplied.is_absolute() else allowed_root / supplied)
    if candidate == allowed_root or not _is_within(candidate, allowed_root):
        raise ResearchSafetyError(
            "Research writes must resolve below C:\\AI\\TradingResearch; "
            f"rejected {candidate}"
        )
    return candidate


def assert_read_only_source(path: Path | str, *, repository_root: Path | str = REPOSITORY_ROOT) -> Path:
    """Validate an existing production-data source without opening it for write."""
    candidate = _resolved(path)
    root = _resolved(repository_root)
    if not _is_within(candidate, root):
        raise ResearchSafetyError(f"Research may read only production sources below {root}: {candidate}")
    return candidate


def write_json_artifact(
    path: Path | str,
    payload: Any,
    *,
    repository_root: Path | str = REPOSITORY_ROOT,
) -> Path:
    """Write one JSON artifact after enforcing the workspace boundary."""
    destination = assert_research_write_path(path, repository_root=repository_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return destination


def open_sqlite_readonly(path: Path | str, *, repository_root: Path | str = REPOSITORY_ROOT) -> sqlite3.Connection:
    """Open an existing SQLite database in immutable read-only mode.

    The SQLite URI mode protects the source database even if a caller later
    attempts DML.  ``PRAGMA query_only`` adds a second connection-level guard.
    """
    source = assert_read_only_source(path, repository_root=repository_root)
    if not source.is_file():
        raise ResearchSafetyError(f"SQLite source does not exist or is not a file: {source}")
    connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection

