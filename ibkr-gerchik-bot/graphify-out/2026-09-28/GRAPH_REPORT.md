# Graph Report - ibkr-gerchik-bot  (2026-09-28)

## Corpus Check
- 287 files · ~45,236,531 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 57 file(s) not represented in the graph (top: .cmd 20, .log 19, (none) 5)

## Summary
- 5101 nodes · 11390 edges · 189 communities (135 shown, 54 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 571 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e1afd6b8`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- src/main.py
- strategy_sources.py
- InefficiencyReclaimStore
- forecast.py
- false_breakout_one_bar.py
- execute_requests.py
- SlackAlerter
- make_zone
- _BrokerStub
- project/support.js
- Level
- Trading Bot Dashboard/support.js
- ScreenerParams
- strategy/inefficiency_reclaim.py
- data_access.py
- NasdaqDataClient
- SlackCommandProcessor
- order_journal.py
- get
- backtest/inefficiency_reclaim.py
- Direction
- components.py
- show_df
- strategy_router.py
- src/config.py
- test_autonomous_llm_remediation.py
- AutonomousStockWorker
- eod.py
- premarket.py
- test_p0_fixes.py
- flex_statement.py
- scanners/inefficiency_reclaim.py
- dashboard/app.py
- levels_export.py
- project/ibkr-gerchik-bot/dashboard/app.py
- Trade Log
- rebound.py
- MarketDataServiceTests
- Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py
- build_provider
- _bars
- false_breakout_continuation.py
- test_strategy_prompt_layer.py
- BarStoreMergeTests
- Autonomous LLM Agent — Remediation Re-Review
- load_stock_symbols
- order_requests.py
- candles_with_levels
- Levels Log
- signal_models.py
- IBKR Gerchik Bot
- detect_false_breakout_one_bar
- Research Log
- crypto/tradingview_webhook.py
- test_irs_scheduler.py
- decision_log.py
- InefficiencyReclaimPaperExecutor
- level_strength.py
- What You Must Do When Invoked
- ensure_irs_history
- install_windows_scheduled_tasks.ps1
- Gerchik Trading Bot — Architecture Audit
- DecisionAction
- IBKRClient
- test_ibkr_paper_autonomous.py
- test_execute_requests.py
- AgentMode
- session_utils.py
- manual_order.py
- broker_reconcile.py
- breakout.py
- Weekly Log
- DashboardDataAccessTests
- market_data_collector.py
- .test_intraday_kill_switch_mode_continues_collecting_without_trading
- AlerterStub
- should_trigger_kill_switch
- strategy_control.py
- crypto/config.py
- _BrokerStub
- forex/tradingview_webhook.py
- third_touch.py
- NewsRiskFilter
- DecisionCandidate
- Autonomous LLM Gerchik Agent — Adversarial Safety Review
- MarketDataService
- _MarketDataServiceStub
- _FakeEvent
- add_bmsb_signals
- normalize_okx_instrument
- TradeSignal
- api_inefficiency_reclaim
- test_decision_provider.py
- DecisionAudit
- DESIGN.md
- graphify reference: extra exports and benchmark
- 001_inefficiency_reclaim_up.sql
- GoldenFixtureTests
- dashboard/__init__.py
- run_dashboard_stop_services.ps1
- backtest/__init__.py
- crypto/__init__.py
- forex/__init__.py
- src/__init__.py
- jobs/__init__.py
- journal/__init__.py
- risk/__init__.py
- scanners/__init__.py
- stocks/__init__.py
- storage/__init__.py
- strategy/__init__.py
- policy.py
- Multi-Strategy LLM Implementation Report
- graphify reference: query, path, explain
- Trading Operations Dashboard
- Trading Strategy
- test_news_timing_isolation.py
- CODING AGENTS: READ THIS FIRST
- test_ibkr_account_identity.py
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- Weekly Review
- AGENTS.md
- extraction-spec.md
- irs/README.md
- Autonomous LLM Gerchik Agent Architecture
- server.py
- NewsService
- Decision Lab Strategy Control
- Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab
- CryptoOrderManager
- test_strategy_api.py
- Findings and disposition
- ProviderHealthResult
- stocks/tradingview_webhook.py
- 40. Architecture Diagrams
- StrategyAnalysisController
- 7. Market Data Architecture
- 14. Gerchik Strategy Engine
- 23. Risk Management
- 45. Important Findings
- IBKR Paper Autonomous implementation report
- IBKR Paper Autonomous validation runbook
- BrokerStub
- _AlwaysBusyBrokerStub
- Shadow runtime preparation report
- Decision Lab provider diagnostics report
- LLM Shadow runtime validation
- test_autonomous_stock_worker.py
- OKXClient
- nearest_level_details
- _NewsRiskFilterStub
- Unified Dashboard Stack Implementation Report
- worker.py
- strategy_definitions.py
- _BrokerStub
- DisplacementAndZoneTests
- test_unified_stack_lifecycle.py
- report_autonomous_stock_worker_start.py
- Decision Lab backend decoupling
- Decision Lab: Strategy-Aware Prompt Layer
- analysis.py
- InefficiencyReclaimSettings
- AccountState
- Decision Lab decision inspector
- _json_value
- test_run_job_flow.py
- ValidatorTests
- Strategy Prompt Layer Implementation Report
- OrderRequestQueueTests
- _BrokerStub
- _FlakyBrokerStub
- _FakeAgent

## God Nodes (most connected - your core abstractions)
1. `Trade Log` - 514 edges
2. `Research Log` - 492 edges
3. `Levels Log` - 148 edges
4. `Level` - 125 edges
5. `TradeSignal` - 86 edges
6. `DecisionAudit` - 81 edges
7. `IBKRClient` - 71 edges
8. `AutonomousGerchikAgent` - 69 edges
9. `AgentMode` - 66 edges
10. `DecisionCandidate` - 58 edges

## Surprising Connections (you probably didn't know these)
- `test_worker_source_contains_no_legacy_session_job_calls()` --uses--> `AutonomousStockWorker`  [INFERRED]
  tests/test_autonomous_stock_worker.py → src/jobs/autonomous_stock_worker.py
- `show_df()` --indirect_call--> `value()`  [INFERRED]
  dashboard/components.py → src/journal/flex_statement.py
- `_decision_lab_warnings()` --indirect_call--> `value()`  [INFERRED]
  dashboard_react/server.py → src/journal/flex_statement.py
- `api_decision_lab_status()` --indirect_call--> `value()`  [INFERRED]
  dashboard_react/server.py → src/journal/flex_statement.py
- `api_watchlist()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py

## Import Cycles
- None detected.

## Communities (189 total, 54 thin omitted)

### Community 0 - "src/main.py"
Cohesion: 0.05
Nodes (54): Namespace, maybe_commit_and_push(), Path, Optional git commit/push helpers for workflow jobs., Commit and push workflow outputs when AUTO_GIT_PUSH is enabled., _run_git(), active_confirmation_symbols(), Any (+46 more)

### Community 1 - "strategy_sources.py"
Cohesion: 0.10
Nodes (38): Manual/AUTO Decision Lab controller owned by the backend worker., _snapshot_payload(), current_source_snapshot_payload(), _daily_snapshot_frame(), default_sources(), _gerchik_snapshots(), _iso(), _monitor_snapshots() (+30 more)

### Community 2 - "InefficiencyReclaimStore"
Cohesion: 0.14
Nodes (13): ImmutableZoneError, InefficiencyReclaimStore, _json(), _json_default(), Any, Connection, datetime, Decimal (+5 more)

### Community 3 - "forecast.py"
Cohesion: 0.12
Nodes (25): _bars_freshness(), calculate_open_risk(), _candidate(), ForecastScenario, _number(), projected_level_candidates(), Any, DataFrame (+17 more)

### Community 4 - "false_breakout_one_bar.py"
Cohesion: 0.11
Nodes (50): PatternName, SignalSide, body_size(), _complex_score(), detect_false_breakout(), DataFrame, Series, Gerchik-style complex 3+ bar false breakout detection helpers with… (+42 more)

### Community 5 - "execute_requests.py"
Cohesion: 0.29
Nodes (17): _effective_dry_run(), _f(), _market_order_tif(), _position_symbol_key(), _process_close(), _process_one(), process_pending_once(), _process_place() (+9 more)

### Community 6 - "SlackAlerter"
Cohesion: 0.06
Nodes (23): Path, Slack webhook notifications., Send alerts to Slack with graceful degradation when disabled., SlackAlerter, OrderResult, OrderManager, Place a human-initiated (dashboard) order. This bypasses the *autonomous*…, Execute validated trades and record the resulting actions. (+15 more)

### Community 9 - "project/support.js"
Cohesion: 0.07
Nodes (61): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), getDC(), Dispatcher(), createExternalModules() (+53 more)

### Community 10 - "Level"
Cohesion: 0.07
Nodes (50): detect_breakout(), Detect a Gerchik-style two-step confirmed breakout on intraday bars., _atr_distances(), _build_zone(), calculate_atr(), _clean_gap_atr_pct_between_levels(), _clean_gap_between_levels(), _cluster_levels() (+42 more)

### Community 11 - "Trading Bot Dashboard/support.js"
Cohesion: 0.07
Nodes (61): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), getDC(), Dispatcher(), createExternalModules() (+53 more)

### Community 12 - "ScreenerParams"
Cohesion: 0.08
Nodes (43): _add_metrics(), _apply_anchor_date(), _approach_profile(), _bar_position(), _business_day_offset(), _date_value(), _detect_signals(), _event_dates() (+35 more)

### Community 13 - "strategy/inefficiency_reclaim.py"
Cohesion: 0.15
Nodes (50): _diagnose_no_candidate(), _add_rth_hours(), Bar, _bounded(), build_explanation(), build_inefficiency_zone(), build_low_overlap_zone(), build_signal_id() (+42 more)

### Community 14 - "data_access.py"
Cohesion: 0.12
Nodes (44): render_intraday(), mark_levels(), panel_header(), all_decision_attempts(), bars_index(), crypto_bars_index(), decisions_for_symbol(), decisions_to_frame() (+36 more)

### Community 15 - "NasdaqDataClient"
Cohesion: 0.10
Nodes (21): date, NasdaqDataConfig, Optional Nasdaq market-data fallback for historical candles., _cloud_precision(), _cloud_range(), _daily_cloud_range(), _duration_days(), _duration_start_date() (+13 more)

### Community 16 - "SlackCommandProcessor"
Cohesion: 0.12
Nodes (6): Path, Parsed Slack command., Poll a Slack channel for whitelisted bot commands., SlackCommand, SlackCommandProcessor, SlackCommandTests

### Community 17 - "order_journal.py"
Cohesion: 0.09
Nodes (46): _base_row(), _closed_positions_by_symbol(), _f(), _fill_price_from_result(), _infer_bracket_exit_from_bars(), load_order_journal(), load_reviews(), _merge_reviews() (+38 more)

### Community 18 - "get"
Cohesion: 0.07
Nodes (36): api_bars(), _live_daily_bars(), api_crypto(), api_crypto_bars(), api_crypto_manual_orders(), api_crypto_symbol(), api_crypto_tradingview_state(), api_dashboard() (+28 more)

### Community 19 - "backtest/inefficiency_reclaim.py"
Cohesion: 0.14
Nodes (23): BacktestCosts, BacktestTrade, _bar_contains_time(), build_backtest_report(), _entry_fill(), _exit_touches(), _maximum_drawdown(), Any (+15 more)

### Community 20 - "Direction"
Cohesion: 0.13
Nodes (27): _historical_analysis_candidate(), Remove live-only risk gates from an anchor-date analysis result., ConfirmationType, Direction, OrderPlan, Enum, str, ScoreBreakdown (+19 more)

### Community 21 - "components.py"
Cohesion: 0.14
Nodes (34): render_dashboard(), render_navigation(), bias_card(), blocked_news_panel(), _clean(), _esc(), feature_card(), fmt() (+26 more)

### Community 22 - "show_df"
Cohesion: 0.14
Nodes (30): render_header(), render_reports(), render_trades(), human_age(), market_session(), metric_grid(), DataFrame, datetime (+22 more)

### Community 23 - "strategy_router.py"
Cohesion: 0.09
Nodes (30): calculate_stop_loss(), build_partial_targets(), calculate_take_profit(), reward_risk_ratio(), _detect_enabled_candidates(), _level_zone(), _note_value(), DataFrame (+22 more)

### Community 24 - "src/config.py"
Cohesion: 0.11
Nodes (24): Logger, BrokerConfig, _csv_env(), _csv_env_file_or_fallback(), _csv_env_with_fallback(), _dedupe_preserve_order(), _env_bool(), _env_float() (+16 more)

### Community 25 - "test_autonomous_llm_remediation.py"
Cohesion: 0.09
Nodes (13): SimpleNamespace, _candidate(), _config(), test_audit_failure_fails_closed_before_paper_mutation(), test_autonomous_guard_rejects_unknown_stale_and_wide_quotes(), test_live_reconciliation_marks_uncertain_submission_without_retry(), test_news_is_omitted_from_autonomous_snapshot_when_disabled(), test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference() (+5 more)

### Community 26 - "AutonomousStockWorker"
Cohesion: 0.10
Nodes (17): AutonomousStockWorker, _heartbeat_blocked_reason(), _heartbeat_provider_status(), _iso(), _pid_alive(), datetime, Path, Persistent application worker for Decision Lab analysis and health state. The… (+9 more)

### Community 27 - "eod.py"
Cohesion: 0.15
Nodes (15): _build_summary(), _build_ticker_section(), _build_workflow_fallback_summary(), _format_reason_lines(), _load_today_job_blockers(), _load_today_workflow_snapshot(), _normalize_reasons(), _payload_matches_today() (+7 more)

### Community 28 - "premarket.py"
Cohesion: 0.05
Nodes (78): main(), Path, _symbol_from_intraday_file(), bar_metadata(), bar_path(), _bar_store_lock(), _ensure_dir(), index_snapshot() (+70 more)

### Community 29 - "test_p0_fixes.py"
Cohesion: 0.09
Nodes (14): detect_false_breakout(), DataFrame, False breakout detection — base module, intentionally disabled. This module…, Detect a level breach followed by immediate rejection. .. deprecated:: This…, _make_level(), DataFrame, Tests covering P0 fixes for Gerchik-style trading logic. P0-A:…, Verify that breakout and rebound honour the configured RR minimum. We patch… (+6 more)

### Community 30 - "flex_statement.py"
Cohesion: 0.07
Nodes (33): Element, HTMLParser, main(), Import the latest TWS trade report without requiring a live IBKR socket., archive_statement(), commit_flex_checkpoint(), discover_trade_report(), fetch_flex_statement() (+25 more)

### Community 31 - "scanners/inefficiency_reclaim.py"
Cohesion: 0.11
Nodes (29): BarLoader, QuoteLoader, _account_state(), candidate_row(), classify_market_regime(), classify_market_trend_direction(), data_quality_diagnostics(), frame_to_bars() (+21 more)

### Community 32 - "dashboard/app.py"
Cohesion: 0.21
Nodes (21): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+13 more)

### Community 33 - "levels_export.py"
Cohesion: 0.16
Nodes (19): _center(), _clean_gap_annotations(), _coerce_float(), export_premarket_levels_report(), _gap_to_upper_level(), _level_key(), _level_price(), _optimization_reason() (+11 more)

### Community 34 - "project/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.21
Nodes (21): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+13 more)

### Community 35 - "Trade Log"
Cohesion: 0.00
Nodes (514): End Of Day (2026-04-27T22:05:40), End Of Day (2026-04-27T22:06:16), End Of Day (2026-04-27T22:13:29), End Of Day (2026-04-28T16:10:04), End Of Day (2026-04-29T16:10:04), End Of Day (2026-04-30T16:10:04), End Of Day (2026-04-30T16:11:49), End Of Day (2026-04-30T16:39:25) (+506 more)

### Community 36 - "rebound.py"
Cohesion: 0.12
Nodes (39): round_number_guard(), average_range(), close_above_level(), close_below_level(), close_location(), full_range(), has_compression(), is_abnormal_candle() (+31 more)

### Community 37 - "MarketDataServiceTests"
Cohesion: 0.14
Nodes (6): _BrokerStub, _DurationBrokerStub, MarketDataServiceTests, _NasdaqProviderStub, DataFrame, _QuoteBrokerStub

### Community 38 - "Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.21
Nodes (21): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+13 more)

### Community 39 - "build_provider"
Cohesion: 0.10
Nodes (21): check(), main(), Read-only preflight for ``ibkr_paper_autonomous``. The default invocation reads…, build_provider(), Build one configured provider; never silently fail over to another model., Provider aliases; concrete transport remains in :mod:`src.decision.provider`., _provider_evidence(), Any (+13 more)

### Community 40 - "_bars"
Cohesion: 0.19
Nodes (15): ChartHistorySpec, _bars(), ChartHistoryTests, get_4h_bars(), get_daily_bars(), get_weekly_bars(), load(), save() (+7 more)

### Community 41 - "false_breakout_continuation.py"
Cohesion: 0.19
Nodes (16): _context(), _continuation_score(), _continuation_stop(), _current_session_uptrend_from_level(), detect_false_breakout_continuation(), _is_prior_false_breakdown(), _normalize_bars(), DataFrame (+8 more)

### Community 42 - "test_strategy_prompt_layer.py"
Cohesion: 0.15
Nodes (30): ApplicabilityResult, evaluate_candidate_applicability(), _has_quantity(), _position_symbol(), Deterministic candidate applicability gates before any provider call., _candidate_payload(), compile_decision_prompt(), CompiledDecisionPrompt (+22 more)

### Community 44 - "Autonomous LLM Agent — Remediation Re-Review"
Cohesion: 0.06
Nodes (35): 10. Paper Protection Scheduling, 11. Paper Recovery, 12. Account Identity, 13. Audit Fail-Closed Behavior, 14. Broker Order Provenance, 15. Mode Isolation, 16. News Isolation, 17. Test Coverage (+27 more)

### Community 45 - "load_stock_symbols"
Cohesion: 0.12
Nodes (24): api_crypto_strategy(), api_strategy(), api_watchlist_add(), api_watchlist_bulk_add(), api_watchlist_remove(), _onboard_client_id(), _parse_stock_symbol_upload(), _queue_bulk_stock_onboarding() (+16 more)

### Community 46 - "order_requests.py"
Cohesion: 0.14
Nodes (27): _acquire_lock(), claim_pending(), _apply(), list_requests(), load(), _load(), _mutate(), _now() (+19 more)

### Community 47 - "candles_with_levels"
Cohesion: 0.14
Nodes (18): candles_with_levels(), capital_gauges(), _focus_price(), forecast_rr_scatter(), forecast_sensitivity(), _labeled_trade_level_ids(), _level_price(), mark_forecast_position() (+10 more)

### Community 48 - "Levels Log"
Cohesion: 0.01
Nodes (148): Daily Levels (2026-04-27T22:02:49), Daily Levels (2026-04-27T22:03:01), Daily Levels (2026-04-27T22:04:21), Daily Levels (2026-04-27T22:12:41), Daily Levels (2026-04-27T22:36:13), Daily Levels (2026-04-27T22:45:49), Daily Levels (2026-04-27T22:47:57), Daily Levels (2026-04-27T22:53:40) (+140 more)

### Community 49 - "signal_models.py"
Cohesion: 0.11
Nodes (25): _first(), _float_or_none(), mapping_to_candidate(), datetime, Adapters from deterministic strategy signals into decision contracts., Adapt a calculated non-stock trade plan to the common decision contract., Adapt a fully normalized ``TradeSignal`` without recalculating it., _signal_timestamp() (+17 more)

### Community 50 - "IBKR Gerchik Bot"
Cohesion: 0.07
Nodes (27): Configuration, Current Limitations, Dashboard, Data And Jobs, Execution And Replay, Hard Filters, Inefficiency Reclaim Strategy, State And Audit (+19 more)

### Community 51 - "detect_false_breakout_one_bar"
Cohesion: 0.12
Nodes (14): DecisionSink, Record a decision into ``sink`` if one is provided; otherwise do nothing., record(), detect_false_breakout_complex(), DecisionSink, Router-compatible wrapper returning a TradeSignal for complex false breakouts., detect_false_breakout_one_bar(), DecisionSink (+6 more)

### Community 52 - "Research Log"
Cohesion: 0.00
Nodes (492): Premarket Research (2026-04-27T22:02:49), Premarket Research (2026-04-27T22:03:01), Premarket Research (2026-04-27T22:04:21), Premarket Research (2026-04-27T22:12:41), Premarket Research (2026-04-27T22:36:13), Premarket Research (2026-04-27T22:45:49), Premarket Research (2026-04-27T22:47:57), Premarket Research (2026-04-27T22:53:40) (+484 more)

### Community 53 - "crypto/tradingview_webhook.py"
Cohesion: 0.19
Nodes (27): BackgroundTasks, api_crypto_tradingview_webhook(), _append_execution(), _as_float(), enqueue_tradingview_webhook(), _execution_from_signal(), _extract_okx_order_id(), _find_execution() (+19 more)

### Community 54 - "test_irs_scheduler.py"
Cohesion: 0.09
Nodes (31): api_services(), _autonomous_stock_worker_status(), _crypto_event_date(), _irs_schedule_status(), _iso_age_seconds(), _latest_job_log_status(), _lock_status(), _market_collector_window_status() (+23 more)

### Community 55 - "decision_log.py"
Cohesion: 0.19
Nodes (16): _acquire_daily_decisions_lock(), _apply_to_decisions(), _build_attempt(), _decision_rank(), load(), _load_daily_decisions(), _lock_is_stale(), persist() (+8 more)

### Community 56 - "InefficiencyReclaimPaperExecutor"
Cohesion: 0.06
Nodes (15): InefficiencyReclaimPaperExecutor, IRSBroker, IRSExecutionPolicy, IRSExecutionResult, IRSExpiryCancellationResult, IRSReconnectResult, Any, datetime (+7 more)

### Community 57 - "level_strength.py"
Cohesion: 0.27
Nodes (8): apply_strength_scores(), _fallback_score(), filter_strong_levels(), Level strength scoring helpers., Return the precomputed strength score, falling back to the legacy field., score_level(), LevelStrengthTests, Tests for level strength scoring.

### Community 58 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 59 - "ensure_irs_history"
Cohesion: 0.17
Nodes (17): ensure_irs_history(), history_specs(), IRSHistorySpec, _mark_skipped_remaining(), _paced_get_bars(), Any, Exception, Incremental historical-data hydration for IRS timeframes. (+9 more)

### Community 61 - "Gerchik Trading Bot — Architecture Audit"
Cohesion: 0.05
Nodes (42): 10. Pre-Open, 11. Stock Screener, 12. Crypto Screener, 13. Intraday Engine, 15. Strategy Stocks, 16. Strategy Crypto, 17. Inefficiency Reclaim, 18. Forecast Engine (+34 more)

### Community 62 - "DecisionAction"
Cohesion: 0.06
Nodes (58): BaseException, provider_check(), _check(), main(), _provider_check(), Path, Read-only configuration preflight for the first LLM Shadow experiment. This…, _read_only_schema() (+50 more)

### Community 63 - "IBKRClient"
Cohesion: 0.06
Nodes (31): _account_values(), _ensure_event_loop(), IBKRClient, IBKRDependencyError, _normalized_broker_account_id(), Any, RuntimeError, Interactive Brokers connectivity built on top of ib_insync. (+23 more)

### Community 64 - "test_ibkr_paper_autonomous.py"
Cohesion: 0.17
Nodes (16): fixture, parametrize, Broker, candidate(), config(), context(), OrderManager, paper_runtime() (+8 more)

### Community 65 - "test_execute_requests.py"
Cohesion: 0.14
Nodes (8): ExecuteWorkerTests, FakeBroker, FakeOrderManager, _paper(), Tests for the dashboard order-request queue and the execution worker., Minimal OrderResult-like object the fake broker returns., _Recorder, TickRoundingTests

### Community 66 - "AgentMode"
Cohesion: 0.17
Nodes (24): _decision_lab_warnings(), DecisionAgentConfig, Configuration for the bounded LLM decision layer. ``mode=off`` is the safe…, default_agent(), Any, Return the process-local agent for the current config. The cache keeps shadow-…, AgentMode, test_live_configuration_requires_verified_allowlisted_account() (+16 more)

### Community 67 - "session_utils.py"
Cohesion: 0.04
Nodes (96): NowProvider, SleepProvider, append_markdown_log(), ensure_directories(), Create runtime and memory directories expected by the application., Append a timestamped Markdown section to a log file., datetime, Intraday scanning and position-management loop. (+88 more)

### Community 68 - "manual_order.py"
Cohesion: 0.19
Nodes (16): _client_order_id(), _decimal(), _decimal_text(), execute_manual_demo_order(), _floor_to_step(), _instrument(), load_manual_order_state(), _now() (+8 more)

### Community 69 - "broker_reconcile.py"
Cohesion: 0.08
Nodes (31): _execution_closed_record(), _execution_matches_request(), _now(), _order_ids(), _parse_dt(), _place_requests(), Any, datetime (+23 more)

### Community 70 - "breakout.py"
Cohesion: 0.25
Nodes (19): _acts_as_resistance(), _acts_as_support(), _atr_used(), _bar_index(), _breakout_is_overextended(), _build_long_signal(), _build_short_signal(), _current_session_bars() (+11 more)

### Community 71 - "Weekly Log"
Cohesion: 0.07
Nodes (26): Weekly Log, Weekly Metrics (2026-04-23T22:20:31), Weekly Metrics (2026-04-23T23:20:28), Weekly Review (2026-04-23T23:46:43), Weekly Review (2026-05-01T16:25:02), Weekly Review (2026-05-13T22:21:19), Weekly Review (2026-05-15T16:25:02), Weekly Review (2026-05-22T16:25:02) (+18 more)

### Community 73 - "market_data_collector.py"
Cohesion: 0.13
Nodes (9): _collector_session_bounds(), _collector_session_is_open(), _next_collector_open(), datetime, Independent intraday bar collector for dashboard continuity., Return the intraday collection window for the date represented by ``now``., Collect bars without account synchronization, signals, or orders., run_market_data_collector() (+1 more)

### Community 75 - "AlerterStub"
Cohesion: 0.33
Nodes (3): notify_inefficiency_reclaim_scan(), AlerterStub, IRSNotificationTests

### Community 76 - "should_trigger_kill_switch"
Cohesion: 0.31
Nodes (6): _equity_symbols(), Trading kill switch conditions., Block all trading when safety conditions are breached., should_trigger_kill_switch(), KillSwitchTests, Tests for kill switch behavior.

### Community 77 - "strategy_control.py"
Cohesion: 0.15
Nodes (41): claim_queued_run(), control_path(), control_payload(), create_run(), _file_lock(), list_runs(), load_control(), _load_runs() (+33 more)

### Community 78 - "crypto/config.py"
Cohesion: 0.36
Nodes (7): CryptoConfig, _env_bool(), _env_csv(), _env_float(), _env_int(), _env_str(), Crypto bot configuration. API secrets should live in the local .env file only.…

### Community 80 - "forex/tradingview_webhook.py"
Cohesion: 0.27
Nodes (16): _append_execution(), _as_float(), _as_int(), _expected_bot_id(), ForexTradingViewWebhookError, handle_forex_tradingview_webhook(), load_forex_tradingview_state(), _normalize_forex_symbol() (+8 more)

### Community 81 - "third_touch.py"
Cohesion: 0.33
Nodes (6): detect_third_touch(), DataFrame, Series, Third touch setup detection., Detect the third qualified interaction with a level., _touch_indices()

### Community 82 - "NewsRiskFilter"
Cohesion: 0.18
Nodes (6): _matching_headlines(), NewsRiskFilter, Evaluate symbol-specific and macro news risk before trading., NewsBlockingTests, Tests for news blocking., StubNewsService

### Community 83 - "DecisionCandidate"
Cohesion: 0.16
Nodes (14): DecisionCandidate, _now(), _number(), PaperPortfolio, Path, Durable, isolated paper portfolio for ``paper_autonomous`` mode., Tighten protection only; this never loosens or removes a stop., Apply deterministic stop/target exits without consulting an LLM. (+6 more)

### Community 84 - "Autonomous LLM Gerchik Agent — Adversarial Safety Review"
Cohesion: 0.04
Nodes (47): 10. Duplicate / Concurrency Analysis, 11. Ownership and Same-Symbol Collisions, 12. Broker vs Local Reconciliation, 13. LLM Latency Impact, 14. Provider Failure Behavior, 15. Response Validation, 16. Risk-Gate Bypass Analysis, 17. Price / Stale-Data Revalidation (+39 more)

### Community 85 - "MarketDataService"
Cohesion: 0.15
Nodes (9): main(), One-off backfill of OHLCV bars for the dashboard. Connects to TWS / IB Gateway…, MarketDataService, DataFrame, datetime, Fetch a timeframe for an incremental strategy cache., Provide reusable market data retrieval wrappers., Allow delayed data when the account lacks a live subscription. (+1 more)

### Community 88 - "add_bmsb_signals"
Cohesion: 0.40
Nodes (5): add_bmsb_signals(), main(), DataFrame, BMSB Strategy 1: Bull Market Support Band conversion. Long-only strategy on…, Add daily signals from completed weekly BMSB values.

### Community 89 - "normalize_okx_instrument"
Cohesion: 0.12
Nodes (15): Resolve user-friendly crypto input to an OKX instrument id. People often type…, _resolve_crypto_add_instrument(), add_crypto_symbol(), _dedupe(), default_instrument_type(), load_crypto_symbol_inputs(), normalize_okx_instrument(), Path (+7 more)

### Community 90 - "TradeSignal"
Cohesion: 0.07
Nodes (30): AgentResult, AgentRuntimeContext, AutonomousGerchikAgent, Autonomous Gerchik decision orchestrator. The agent owns reasoning and…, Process one candidate through durable, fail-closed authorization., Run deterministic Paper protection independently of the LLM., Validate a provider without exposing broker/account data or trading., Resolve uncertain Live reservations without ever resubmitting. (+22 more)

### Community 91 - "api_inefficiency_reclaim"
Cohesion: 0.20
Nodes (12): api_inefficiency_reclaim(), api_inefficiency_reclaim_settings(), api_update_inefficiency_reclaim_settings(), _parse_irs_min_daily_history_rows(), _parse_irs_minimum_display_score(), Path, Atomically update one allowlisted environment setting., Run the disabled-by-default IRS analysis scan over persisted bars. (+4 more)

### Community 92 - "test_decision_provider.py"
Cohesion: 0.15
Nodes (19): build_system_prompt(), DecisionProviderError, _extract_json_content(), _extract_raw_content(), HttpDecisionProvider, Any, RuntimeError, A provider could not produce a valid decision. (+11 more)

### Community 93 - "DecisionAudit"
Cohesion: 0.12
Nodes (12): DecisionAudit, _json(), _now(), Connection, Path, Small SQLite repository; all writes are explicit and queryable., Persist downstream policy outcome without overwriting model output., Sanitize both sensitive keys and account-like strings for the UI. (+4 more)

### Community 94 - "DESIGN.md"
Cohesion: 0.15
Nodes (12): Brand & Style, Buttons, Cards, Colors, Components, Data Visualization, Elevation & Depth, Input Fields (+4 more)

### Community 95 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 96 - "001_inefficiency_reclaim_up.sql"
Cohesion: 0.18
Nodes (19): fills, idx_irs_news_signal_time, idx_irs_news_symbol_time, idx_irs_rejections_reason, idx_irs_risk_signal_time, idx_irs_setups_state_expiry, idx_irs_setups_symbol, idx_irs_zones_symbol_status (+11 more)

### Community 100 - "run_dashboard_stop_services.ps1"
Cohesion: 0.60
Nodes (4): Get-OwnedPython(), Signal-AutonomousWorker(), Stop-ById(), Stop-OwnedPython()

### Community 112 - "policy.py"
Cohesion: 0.10
Nodes (29): PositionAction, str, AutonomousRiskGate, daily_loss_exceeded(), evaluate_position_action(), PositionActionDecision, Any, Deterministic gates around model choices. The model can choose an action, but… (+21 more)

### Community 117 - "Multi-Strategy LLM Implementation Report"
Cohesion: 0.18
Nodes (10): Architecture, Classification matrix, Current source with a complete deterministic plan, Failure and safety behavior, Implemented components, Manual and AUTO semantics, Multi-Strategy LLM Implementation Report, Requirement-by-requirement implementation report (+2 more)

### Community 118 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 119 - "Trading Operations Dashboard"
Cohesion: 0.33
Nodes (5): Candle data, Install, Run, Trading Operations Dashboard, Workspaces

### Community 120 - "Trading Strategy"
Cohesion: 0.33
Nodes (5): Buy-Side Gate, Core Rules, Intraday Rules, Review Process, Trading Strategy

### Community 121 - "test_news_timing_isolation.py"
Cohesion: 0.19
Nodes (14): _Broker, _DecisionAgent, _MarketData, _News, _OrderManager, _run(), _signal(), test_off_mode_keeps_legacy_macro_news_gate() (+6 more)

### Community 122 - "CODING AGENTS: READ THIS FIRST"
Cohesion: 0.40
Nodes (4): About the design files, Bundle contents, CODING AGENTS: READ THIS FIRST, What you should do — IMPORTANT

### Community 123 - "test_ibkr_account_identity.py"
Cohesion: 0.22
Nodes (10): FakeIB, identity(), test_duplicate_du_summary_rows_are_deduplicated(), test_managed_du_and_conflicting_summary_du_fail_closed(), test_managed_du_and_matching_summary_du_pass(), test_one_du_managed_account_with_all_summary_rows_is_paper_verified(), test_one_managed_u_account_is_live_not_paper(), test_only_all_summary_rows_without_managed_account_fail() (+2 more)

### Community 124 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 125 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 126 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

### Community 134 - "Autonomous LLM Gerchik Agent Architecture"
Cohesion: 0.09
Nodes (22): Audit database, Autonomous LLM Gerchik Agent Architecture, Crypto contract and execution separation, Dashboard API and tab, Decision contracts, Deterministic risk gate, Execution reservation state machine, Explicit News policy (+14 more)

### Community 135 - "server.py"
Cohesion: 0.09
Nodes (39): api_crypto_add(), api_crypto_analyze(), api_crypto_bulk_add(), api_crypto_order_place(), api_crypto_order_simulate(), api_crypto_remove(), api_decision_lab_disable_preset(), api_decision_lab_duplicate_preset() (+31 more)

### Community 136 - "NewsService"
Cohesion: 0.13
Nodes (7): fx_pair_components(), normalize_symbol(), Settings, NewsService, Fetch symbol, macro, and earnings context from NewsAPI.ai / Event Registry., _build_research_symbols(), SymbolHandlingTests

### Community 137 - "Decision Lab Strategy Control"
Cohesion: 0.40
Nodes (4): Decision Lab Strategy Control, MANUAL and AUTO, Safety boundary, Source selector

### Community 139 - "Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab, Source Nodes

### Community 140 - "CryptoOrderManager"
Cohesion: 0.21
Nodes (9): CryptoOrderManager, CryptoOrderRequest, OKX crypto order planning and safe simulated execution. Crypto orders…, Expose the shared stock risk settings used for crypto sizing., Floor generic precision so rounding can never exceed buying power., _reward_risk(), _round_crypto_quantity(), stock_risk_settings() (+1 more)

### Community 141 - "test_strategy_api.py"
Cohesion: 0.13
Nodes (18): api_decision_lab_control(), api_decision_lab_prompt_presets(), api_decision_lab_run(), api_decision_lab_runs(), api_decision_lab_strategies(), disable_dashboard_cache(), Return control plus the worker's persisted source snapshot. The status/read…, Queue one bounded MANUAL run for the persistent worker. This endpoint first… (+10 more)

### Community 143 - "Findings and disposition"
Cohesion: 0.10
Nodes (20): Autonomous LLM Gerchik Agent — Safety Remediation Report, Executive result, F-CRIT-01 — No atomic candidate claim or execution reservation, F-CRIT-02 — Broker acceptance can precede durable execution identity, F-CRIT-03 — No authoritative broker reconciliation at the autonomous execution boundary, F-HIGH-01 — No post-LLM quote and risk revalidation, F-HIGH-02 — Unknown account identity can bypass the configured allowlist, F-HIGH-03 — Synchronous provider latency can delay protective management (+12 more)

### Community 144 - "ProviderHealthResult"
Cohesion: 0.23
Nodes (19): api_decision_lab_status(), api_decision_lab_test_provider(), _provider_test_state(), Run the shared configured-provider WAIT diagnostic without trading state., _set_provider_test_state(), ProviderHealthResult, Sanitized result suitable for a worker heartbeat or dashboard response., _config() (+11 more)

### Community 145 - "stocks/tradingview_webhook.py"
Cohesion: 0.25
Nodes (18): api_stock_tradingview_webhook(), _append_execution(), _as_float(), _as_int(), _expected_bot_id(), _first_float(), handle_stock_tradingview_webhook(), load_stock_tradingview_state() (+10 more)

### Community 146 - "40. Architecture Diagrams"
Cohesion: 0.40
Nodes (5): 40. Architecture Diagrams, Background jobs / scheduler flow, Complete system architecture, Important module dependency map, Market data flow

### Community 147 - "StrategyAnalysisController"
Cohesion: 0.17
Nodes (13): pending_manual_runs(), AnalysisExecutionContext, completed_scan_callback(), get_strategy_analysis_controller(), Path, Persist one coherent source snapshot, then schedule eligible work., Schedule queued MANUAL runs from the persisted latest snapshot., Create and schedule one AUTO analysis from current source data. This consumes… (+5 more)

### Community 148 - "7. Market Data Architecture"
Cohesion: 0.50
Nodes (4): 7. Market Data Architecture, Crypto, Forex, Stocks

### Community 153 - "IBKR Paper Autonomous implementation report"
Cohesion: 0.33
Nodes (5): Conditions for manual validation, Delivered controls, IBKR Paper Autonomous implementation report, Scope and verdict, Verification performed

### Community 154 - "IBKR Paper Autonomous validation runbook"
Cohesion: 0.33
Nodes (5): Controlled observation, Evidence to retain, IBKR Paper Autonomous validation runbook, Immediate stop conditions, Preconditions

### Community 158 - "Shadow runtime preparation report"
Cohesion: 0.20
Nodes (9): Added validation assets, Completed implementation: News timing isolation, Conditions that must remain true, Effective configuration contract, Known limitations and unresolved evidence, Readiness, Runtime data flow to verify later, Shadow runtime preparation report (+1 more)

### Community 159 - "Decision Lab provider diagnostics report"
Cohesion: 0.17
Nodes (11): API contract, Decision Lab provider diagnostics report, Files changed, `GET /api/decision-lab/status`, Known limitations, Manual verification steps, `POST /api/decision-lab/test-provider`, Safety boundary (+3 more)

### Community 160 - "LLM Shadow runtime validation"
Cohesion: 0.20
Nodes (9): 1. Scope and prerequisites, 2. Exact process-scoped Shadow configuration, 3. Read-only preflight, 4. Exact first-run sequence, 5. Expected News timing and isolation, 6. Read-only observation points, 6a. Decision Lab -> Test LLM Connection, 7. Stop conditions (+1 more)

### Community 161 - "test_autonomous_stock_worker.py"
Cohesion: 0.29
Nodes (14): PreflightResult, Safe, dashboard-ready readiness evidence with no account identifier., Path, _ready(), test_allowlist_mismatch_is_sanitized_in_worker_heartbeat(), test_failed_preflight_blocks_entries_without_starting_jobs(), test_stopping_worker_releases_only_its_own_lock(), test_worker_auto_cadence_skips_duplicate_source_fingerprint() (+6 more)

### Community 162 - "OKXClient"
Cohesion: 0.17
Nodes (7): OKXClient, OKXInstrument, Any, DataFrame, Small OKX REST client for public candles and future private order work., Place a SPOT order, optionally with attached market-exit TP/SL. Private…, OKXSpotOrderPayloadTests

### Community 163 - "nearest_level_details"
Cohesion: 0.21
Nodes (10): _coerce_float(), nearest_level_details(), datetime, Path, _quote_reference_price(), Excel exports for intraday scan outcomes., Write one intraday scan result workbook and return its path., Return the most relevant watchlist level for reporting. (+2 more)

### Community 165 - "Unified Dashboard Stack Implementation Report"
Cohesion: 0.15
Nodes (12): 10. Limitations and controlled-validation status, 11. Operator workflow, 1. Existing and new launcher behavior, 2. Worker architecture and entry point, 3. Startup order, 4. Heartbeat, state, and duplicate prevention, 5. Closed-market behavior, 6. Mode-aware preflight and retry behavior (+4 more)

### Community 167 - "worker.py"
Cohesion: 0.19
Nodes (18): load_crypto_symbols(), collect_crypto_bars(), configured_crypto_symbols(), validate_symbols(), _analysis_summary(), main(), CLI entry point for the separate crypto bot., _symbols() (+10 more)

### Community 168 - "strategy_definitions.py"
Cohesion: 0.22
Nodes (10): api_decision_lab_strategy_definitions(), current_strategy_parameters(), get_strategy_definition(), list_strategy_definitions(), Code-owned, versioned strategy semantics for Decision Lab prompts. Definitions…, Resolve parameters from the same runtime constants/calculators used by the app., _signal(), SignalDefinition (+2 more)

### Community 170 - "DisplacementAndZoneTests"
Cohesion: 0.27
Nodes (3): DisplacementAndZoneTests, make_bar(), RobustATRTests

### Community 171 - "test_unified_stack_lifecycle.py"
Cohesion: 0.27
Nodes (11): api_system_logout(), index(), Start the fixed dashboard-service shutdown script for the UI logout action., test_dashboard_service_contract_exposes_worker_fields_without_account_id(), test_hard_reset_reuses_complete_stack_and_documents_protection(), test_logout_endpoint_only_launches_the_fixed_stop_script(), test_logout_uses_fixed_stop_script_without_arbitrary_command_input(), test_start_launcher_contains_complete_stack_in_required_order() (+3 more)

### Community 172 - "report_autonomous_stock_worker_start.py"
Cohesion: 0.60
Nodes (4): _age(), _alive(), main(), Report resident-worker startup evidence for the Windows launcher. This is read-…

### Community 173 - "Decision Lab backend decoupling"
Cohesion: 0.25
Nodes (7): AUTO flow, Boundary, Decision Lab backend decoupling, Existing source data, Manual flow, Runtime scripts and locks, Safety and verification boundary

### Community 174 - "Decision Lab: Strategy-Aware Prompt Layer"
Cohesion: 0.29
Nodes (6): Applicability gate, Decision Lab: Strategy-Aware Prompt Layer, Presets and compiler, Provider boundary, Read-only UI/API behavior, Source definitions

### Community 175 - "analysis.py"
Cohesion: 0.23
Nodes (18): analyze_symbol(), load_crypto_state(), _quiet_shared_level_logs(), Crypto Gerchik-style analysis using the shared stock strategy logic., Keep crypto batch scans from flooding the shared stock bot log file., run_crypto_analysis(), crypto_bar_path(), crypto_index_snapshot() (+10 more)

### Community 176 - "InefficiencyReclaimSettings"
Cohesion: 0.40
Nodes (3): InefficiencyReclaimSettings, Validated environment-facing settings for the isolated IRS subsystem., Build the pure strategy config without making that module read env.

### Community 177 - "AccountState"
Cohesion: 0.19
Nodes (7): AccountState, evaluate_hard_gates(), Decimal, Quote, _regime_aligned(), StrategyContext, PlanningAndGateTests

### Community 178 - "Decision Lab decision inspector"
Cohesion: 0.29
Nodes (6): Action menus and strategy provenance, Audit schema and API, Decision Lab decision inspector, Failure handling, Production prompt and provenance, Sanitization and retention

### Community 182 - "test_run_job_flow.py"
Cohesion: 0.20
Nodes (6): _NewsRiskFilterStub, RunJobFlowTests, fake_run_intraday(), fake_run_open(), fake_run_premarket(), _stock_position()

### Community 184 - "Strategy Prompt Layer Implementation Report"
Cohesion: 0.40
Nodes (4): Changed components, Scope, Strategy Prompt Layer Implementation Report, Verification

## Knowledge Gaps
- **1514 isolated node(s):** `schema_migrations`, `scanner_runs`, `BrokerConfig`, `RiskConfig`, `StrategyConfig` (+1509 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 2462 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **54 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MarketDataService` connect `MarketDataService` to `src/main.py`, `session_utils.py`, `MarketDataServiceTests`, `SlackAlerter`, `market_data_collector.py`, `NasdaqDataClient`, `NewsRiskFilter`, `ensure_irs_history`, `premarket.py`, `IBKRClient`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Why does `TradeSignal` connect `TradeSignal` to `src/main.py`, `strategy_sources.py`, `test_execute_requests.py`, `session_utils.py`, `false_breakout_one_bar.py`, `execute_requests.py`, `SlackAlerter`, `breakout.py`, `rebound.py`, `false_breakout_continuation.py`, `Level`, `test_ibkr_paper_autonomous.py`, `test_news_timing_isolation.py`, `signal_models.py`, `detect_false_breakout_one_bar`, `strategy_router.py`, `test_autonomous_llm_remediation.py`, `IBKRClient`?**
  _High betweenness centrality (0.025) - this node is a cross-community bridge._
- **Why does `IRSConfig` connect `strategy/inefficiency_reclaim.py` to `make_zone`, `DisplacementAndZoneTests`, `InefficiencyReclaimSettings`, `AccountState`, `Direction`, `src/config.py`, `InefficiencyReclaimPaperExecutor`, `scanners/inefficiency_reclaim.py`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Are the 56 inferred relationships involving `Level` (e.g. with `_level_center()` and `_level_zone()`) actually correct?**
  _`Level` has 56 INFERRED edges - model-reasoned connections that need verification._
- **Are the 34 inferred relationships involving `TradeSignal` (e.g. with `AutonomousGerchikAgent` and `_signal_timestamp()`) actually correct?**
  _`TradeSignal` has 34 INFERRED edges - model-reasoned connections that need verification._
- **What connects `schema_migrations`, `scanner_runs`, `BrokerConfig` to the rest of the system?**
  _1514 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `src/main.py` be split into smaller, more focused modules?**
  _Cohesion score 0.04556962025316456 - nodes in this community are weakly interconnected._