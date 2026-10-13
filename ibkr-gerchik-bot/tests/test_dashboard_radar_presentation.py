from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RADAR_UI = (ROOT / "dashboard_react" / "index.html").read_text(encoding="utf-8-sig")
RADAR_ENGINE = (ROOT / "src" / "radar" / "engine.py").read_text(encoding="utf-8")
RADAR_SERVICE = (ROOT / "src" / "radar" / "service.py").read_text(encoding="utf-8")


def test_radar_backend_enum_is_kept_as_a_regime_display_context() -> None:
    assert "trendColor(row.trend)" in RADAR_UI
    assert "text={row.trend}" in RADAR_UI
    assert "text={`${selected.trend} REGIME`}" in RADAR_UI
    assert "GREEN / GREY / RED describe regime context" in RADAR_UI


def test_completed_daily_radar_horizon_uses_authoritative_selected_as_of() -> None:
    assert "As of {selected.as_of} · Completed daily data" in RADAR_UI
    assert "const radarScannerHorizon" in RADAR_UI
    assert "latest_available_date" in RADAR_UI
    assert "anchor_status" in RADAR_UI


def test_trend_score_explanation_is_presentation_only() -> None:
    assert "Completed-daily regime score" in RADAR_UI
    assert "It is not the current scanner trade direction." in RADAR_UI
    assert "['TREND SCORE',score(selected.trend_score)" in RADAR_UI


def test_scanner_result_is_separate_and_current_horizon_is_conditional() -> None:
    assert ">SCANNER SETUP</span>" in RADAR_UI
    assert "Radar uses completed daily data. Scanner uses the current setup/session." in RADAR_UI
    assert "Current partial session" in RADAR_UI
    assert "Evaluated through ${latestAvailableDate} current session" in RADAR_UI
    assert "Scanned at ${String(scanTimestamp)" in RADAR_UI


def test_counter_trend_warning_is_informational_and_green_long_is_not_conflicting() -> None:
    assert "(normalizedTrend==='GREEN' && side==='SHORT') || (normalizedTrend==='RED' && side==='LONG')" in RADAR_UI
    assert "{conflict&&<div" in RADAR_UI
    assert "Radar context: {selected.trend} regime" in RADAR_UI
    assert "Scanner setup: {strategy} {side}" in RADAR_UI
    assert "setup remains READY" in RADAR_UI


def test_scanner_state_and_lifecycle_contracts_are_not_rewritten_by_the_ui_copy() -> None:
    assert "setScannerStates(previous=>({...previous,[selected.symbol]:{status:body.scanner_status||'ERROR',label,signals:body.signals||[],payload:body}}))" in RADAR_UI
    assert "setScannerStates(previous=>({...previous,...next}))" in RADAR_UI
    assert "CANDIDATE" in RADAR_UI
    assert "entry_triggered === true" in RADAR_UI
    assert "approve_setup" not in RADAR_UI


def test_radar_thresholds_and_direction_conflict_authority_remain_unchanged() -> None:
    assert "if score >= settings.trend_green_threshold" in RADAR_ENGINE
    assert "if score <= settings.trend_red_threshold" in RADAR_ENGINE
    assert "trend_green_threshold: float = 60.0" in (ROOT / "src" / "radar" / "models.py").read_text(encoding="utf-8")
    assert "trend_red_threshold: float = 40.0" in (ROOT / "src" / "radar" / "models.py").read_text(encoding="utf-8")
    assert 'annotated["direction_conflict"] = direction_conflict(radar_trend, side)' in RADAR_SERVICE
