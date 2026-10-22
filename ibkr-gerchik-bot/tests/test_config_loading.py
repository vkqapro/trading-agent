from pathlib import Path


def test_fallback_env_loader_loads_valid_entries_without_output(tmp_path: Path, monkeypatch, capsys):
    from src.config import _load_env_file_fallback

    env_file = tmp_path / ".env"
    env_file.write_text(
        "AI_TRADE_DESK_AI_TOKEN=redacted-fixture-token\n"
        "QUOTED_VALUE=\"value with spaces\" # comment\n"
        "export PAPER_TRADING=true\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("AI_TRADE_DESK_AI_TOKEN", raising=False)
    monkeypatch.delenv("QUOTED_VALUE", raising=False)
    monkeypatch.delenv("PAPER_TRADING", raising=False)
    assert _load_env_file_fallback(env_file) is True
    assert __import__("os").environ["AI_TRADE_DESK_AI_TOKEN"] == "redacted-fixture-token"
    assert __import__("os").environ["QUOTED_VALUE"] == "value with spaces"
    assert __import__("os").environ["PAPER_TRADING"] == "true"
    assert capsys.readouterr().out == ""
    assert "redacted-fixture-token" not in capsys.readouterr().err


def test_fallback_env_loader_preserves_explicit_environment_precedence(tmp_path: Path, monkeypatch):
    from src.config import _load_env_file_fallback

    env_file = tmp_path / ".env"
    env_file.write_text("EXPLICIT_VALUE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("EXPLICIT_VALUE", "from-process")

    assert _load_env_file_fallback(env_file, override=False) is True
    assert __import__("os").environ["EXPLICIT_VALUE"] == "from-process"


def test_missing_ai_token_is_detected_without_logging_or_exporting(monkeypatch, capsys):
    from tools.trading_work_operator import OperatorError, _configured_ai_token

    monkeypatch.delenv("AI_TRADE_DESK_AI_TOKEN", raising=False)
    try:
        _configured_ai_token()
    except OperatorError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("missing AI token should be rejected")
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
