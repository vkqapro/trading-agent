from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

import trading_research.safety as safety
from trading_research.safety import (
    ResearchSafetyError,
    assert_research_write_path,
    open_sqlite_readonly,
    write_json_artifact,
)
from trading_research import cli


def test_workspace_write_succeeds_and_production_and_traversal_writes_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    production = tmp_path / "production"
    production.mkdir()
    workspace = tmp_path / "research"
    monkeypatch.setattr(safety, "configured_research_root", lambda: workspace)
    written = write_json_artifact("workspace/exports/result.json", {"ok": True}, repository_root=production)
    assert written == (workspace / "workspace" / "exports" / "result.json").resolve()
    assert written.read_text(encoding="utf-8")

    with pytest.raises(ResearchSafetyError):
        assert_research_write_path(production / "strategy.py", repository_root=production)
    with pytest.raises(ResearchSafetyError):
        assert_research_write_path("../production/strategy.py", repository_root=production)
    monkeypatch.setattr(safety, "configured_research_root", lambda: production)
    with pytest.raises(ResearchSafetyError):
        assert_research_write_path("output.json", repository_root=production)


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    production = tmp_path / "production"
    production.mkdir()
    workspace = tmp_path / "research"
    workspace.mkdir()
    monkeypatch.setattr(safety, "configured_research_root", lambda: workspace)
    escape = tmp_path / "outside"
    escape.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to(escape, target_is_directory=True)
    except OSError:
        pytest.skip("This platform does not permit a test symlink without elevated privilege")
    with pytest.raises(ResearchSafetyError):
        assert_research_write_path(link / "report.json", repository_root=production)


def test_sqlite_reader_is_true_read_only(tmp_path: Path) -> None:
    production = tmp_path / "production"
    production.mkdir()
    database = production / "journal.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
        connection.execute("INSERT INTO trades(symbol) VALUES ('AAPL')")

    connection = open_sqlite_readonly(database, repository_root=production)
    try:
        assert connection.execute("SELECT symbol FROM trades").fetchone()[0] == "AAPL"
        for statement in (
            "INSERT INTO trades(symbol) VALUES ('MSFT')",
            "UPDATE trades SET symbol='MSFT' WHERE id=1",
            "DELETE FROM trades WHERE id=1",
        ):
            with pytest.raises(sqlite3.OperationalError):
                connection.execute(statement)
    finally:
        connection.close()


def test_cli_requires_bytecode_write_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    assert cli.main(["list-strategies"]) == 2
