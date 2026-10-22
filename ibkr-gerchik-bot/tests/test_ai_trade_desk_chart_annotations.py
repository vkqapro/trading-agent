from pathlib import Path


def test_market_screener_and_trade_desk_use_one_signal_chart_renderer():
    source = Path("dashboard_react/index.html").read_text(encoding="utf-8")

    assert "function ScreenerSignalChart" in source
    assert source.count("<ScreenerSignalChart") == 2
    assert "function buildStrategySignalAnnotations" not in source
    assert "strategy === 'LP1'" in source
    assert "strategy === 'LP2'" in source
    assert "strategy === 'PRB1'" in source
    assert "strategy === 'PRB2'" in source
    assert "tradeMarkers={signalMarkers}" in source
    assert "highlightBars={signalHighlightedBars}" in source


def test_trade_desk_uses_exact_reviewed_observation_identity_without_symbol_fallback():
    source = Path("dashboard_react/index.html").read_text(encoding="utf-8")

    assert "function reviewedProposalScreenerSignal" in source
    assert "reviewed_observation_id" in source
    assert "reviewed_source_candidate_id" in source
    assert "reviewed_source_scan_id" in source
    assert "candidate.symbol || candidate.ticker" in source
    assert "reviewed observation identity does not match this proposal" in source
    assert "persisted reviewed observation has no exact scanner formation anchor" in source
    assert "const chartSource = reviewedProposalScreenerSignal(detail);" in source
    assert "signal={showSignal?chartSource.signal:null}" in source
    assert "unavailableReason={showSignal?chartSource.warning:''}" in source
    assert "chartSignal =" not in source


def test_trade_desk_keeps_screener_chart_controls_and_proposal_overlays():
    source = Path("dashboard_react/index.html").read_text(encoding="utf-8")

    assert "<TfPills value={tf} onChange={setTf}/>" in source
    assert '<Toggle on={showSignal} label="Signal"' in source
    assert '<Toggle on={showTrade} label="Trade zones"' in source
    assert '<Toggle on={showRaw} label="Raw levels"' in source
    assert '<Toggle on={showStrategy} label="BMSB strategy"' in source
    assert '<Toggle on={showGaussian} label="Gaussian channel"' in source
    assert "label:'TARGET ZONE'" in source
    assert "label:'RISK ZONE'" in source
    assert 'lineLabelPosition="middle"' in source
