# Graph Report - ibkr-gerchik-bot  (2026-09-27)

## Corpus Check
- 264 files · ~40,204,621 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 53 file(s) not represented in the graph (top: .cmd 20, .log 15, (none) 5)

## Summary
- 4780 nodes · 10458 edges · 166 communities (123 shown, 43 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 540 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e1afd6b8`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- src/main.py
- DecisionCandidate
- InefficiencyReclaimStore
- forecast.py
- false_breakout_one_bar.py
- order_requests.py
- test_order_flow.py
- make_zone
- _connect_broker_with_startup_retry
- project/support.js
- Level
- Trading Bot Dashboard/support.js
- ScreenerParams
- strategy/inefficiency_reclaim.py
- data_access.py
- NasdaqDataClient
- SlackCommandProcessor
- order_journal.py
- server.py
- backtest/inefficiency_reclaim.py
- test_decision_models.py
- components.py
- Direction
- TradeSignal
- src/config.py
- premarket.py
- AutonomousStockWorker
- ValidatorTests
- AccountState
- test_p0_fixes.py
- flex_statement.py
- scanners/inefficiency_reclaim.py
- dashboard/app.py
- levels_export.py
- project/ibkr-gerchik-bot/dashboard/app.py
- Trade Log
- rebound.py
- MarketDataService
- Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py
- autonomous_stock_preflight.py
- _bars
- false_breakout_continuation.py
- NewsRiskFilter
- BarStoreMergeTests
- Autonomous LLM Agent — Remediation Re-Review
- load_stock_symbols
- test_execute_requests.py
- candles_with_levels
- Levels Log
- test_autonomous_llm_remediation.py
- IBKR Gerchik Bot
- NewsService
- Research Log
- crypto/tradingview_webhook.py
- test_irs_scheduler.py
- decision_log.py
- DisplacementAndZoneTests
- symbol_onboarding.py
- What You Must Do When Invoked
- ensure_irs_history
- install_windows_scheduled_tasks.ps1
- Gerchik Trading Bot — Architecture Audit
- OKXClient
- IBKRClient
- test_ibkr_paper_autonomous.py
- AgentMode
- level_strength.py
- session_utils.py
- manual_order.py
- broker_reconcile.py
- breakout.py
- Weekly Log
- DashboardDataAccessTests
- market_data_collector.py
- _BrokerStub
- InefficiencyReclaimPaperExecutor
- should_trigger_kill_switch
- InefficiencyReclaimSettings
- crypto/config.py
- _BrokerStub
- forex/tradingview_webhook.py
- third_touch.py
- stocks/tradingview_webhook.py
- PaperPortfolio
- Autonomous LLM Gerchik Agent — Adversarial Safety Review
- _BrokerStub
- _MarketDataServiceStub
- _FakeEvent
- add_bmsb_signals
- symbols.py
- AutonomousGerchikAgent
- api_inefficiency_reclaim
- SlackAlerter
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
- validator.py
- execute_requests.py
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
- candles.py
- OrderManager
- test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference
- identity.py
- Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab
- check_llm_shadow_ready.py
- BrokerStub
- test_autonomous_guard_rejects_unknown_stale_and_wide_quotes
- Findings and disposition
- JobSessionUtilsTests
- 40. Architecture Diagrams
- 7. Market Data Architecture
- 14. Gerchik Strategy Engine
- 23. Risk Management
- 45. Important Findings
- IBKR Paper Autonomous implementation report
- IBKR Paper Autonomous validation runbook
- IBKRDependencyError
- _json_value
- Shadow runtime preparation report
- Decision Lab provider diagnostics report
- LLM Shadow runtime validation
- policy.py
- _NewsRiskFilterStub
- Unified Dashboard Stack Implementation Report
- _BrokerStub
- analysis.py
- api_bars
- test_unified_stack_lifecycle.py
- report_autonomous_stock_worker_start.py

## God Nodes (most connected - your core abstractions)
1. `Trade Log` - 500 edges
2. `Research Log` - 476 edges
3. `Levels Log` - 141 edges
4. `Level` - 125 edges
5. `TradeSignal` - 84 edges
6. `IBKRClient` - 71 edges
7. `DecisionAudit` - 71 edges
8. `AutonomousGerchikAgent` - 62 edges
9. `AgentMode` - 60 edges
10. `MarketDataService` - 56 edges

## Surprising Connections (you probably didn't know these)
- `show_df()` --indirect_call--> `value()`  [INFERRED]
  dashboard/components.py → src/journal/flex_statement.py
- `_decision_lab_warnings()` --indirect_call--> `value()`  [INFERRED]
  dashboard_react/server.py → src/journal/flex_statement.py
- `api_decision_lab_status()` --indirect_call--> `value()`  [INFERRED]
  dashboard_react/server.py → src/journal/flex_statement.py
- `api_watchlist()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py
- `api_market_screener()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py

## Import Cycles
- None detected.

## Communities (166 total, 43 thin omitted)

### Community 0 - "src/main.py"
Cohesion: 0.03
Nodes (79): Namespace, ensure_directories(), Create runtime and memory directories expected by the application., _build_summary(), _build_ticker_section(), _build_workflow_fallback_summary(), _format_reason_lines(), _load_today_job_blockers() (+71 more)

### Community 1 - "DecisionCandidate"
Cohesion: 0.07
Nodes (56): BaseException, provider_check(), _provider_check(), Autonomous Gerchik decision orchestrator. The agent owns reasoning and…, Durable SQLite audit trail for autonomous decisions., Bounded autonomous decision layer for the existing Gerchik engine. The package…, DecisionAction, DecisionCandidate (+48 more)

### Community 2 - "InefficiencyReclaimStore"
Cohesion: 0.14
Nodes (13): ImmutableZoneError, InefficiencyReclaimStore, _json(), _json_default(), Any, Connection, datetime, Decimal (+5 more)

### Community 3 - "forecast.py"
Cohesion: 0.12
Nodes (25): _bars_freshness(), calculate_open_risk(), _candidate(), ForecastScenario, _number(), projected_level_candidates(), Any, DataFrame (+17 more)

### Community 4 - "false_breakout_one_bar.py"
Cohesion: 0.11
Nodes (51): PatternName, SignalSide, body_size(), upper_wick(), _complex_score(), detect_false_breakout(), DataFrame, Series (+43 more)

### Community 5 - "order_requests.py"
Cohesion: 0.12
Nodes (31): _acquire_lock(), claim_pending(), _apply(), list_requests(), load(), _load(), _mutate(), _now() (+23 more)

### Community 6 - "test_order_flow.py"
Cohesion: 0.13
Nodes (10): OrderResult, _BrokerStub, _chart_history_ready(), _manual_candidate_signal(), _MarketDataStub, _MultiSessionMarketDataStub, _NewsFilterStub, OrderFlowTests (+2 more)

### Community 8 - "_connect_broker_with_startup_retry"
Cohesion: 0.16
Nodes (5): _connect_broker_with_startup_retry(), Create and connect an IBKR client, waiting through temporary startup contention., _AlwaysBusyBrokerStub, _FlakyBrokerStub, IBKRStartupRetryTests

### Community 9 - "project/support.js"
Cohesion: 0.07
Nodes (61): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), getDC(), Dispatcher(), createExternalModules() (+53 more)

### Community 10 - "Level"
Cohesion: 0.06
Nodes (51): detect_breakout(), _level_zone(), Detect a Gerchik-style two-step confirmed breakout on intraday bars., _atr_distances(), _build_zone(), calculate_atr(), _clean_gap_atr_pct_between_levels(), _clean_gap_between_levels() (+43 more)

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
Nodes (45): render_intraday(), mark_levels(), all_decision_attempts(), bars_index(), crypto_bars_index(), decisions_for_symbol(), decisions_to_frame(), _filter_watchlist_symbols() (+37 more)

### Community 15 - "NasdaqDataClient"
Cohesion: 0.10
Nodes (21): date, NasdaqDataConfig, Optional Nasdaq market-data fallback for historical candles., _cloud_precision(), _cloud_range(), _daily_cloud_range(), _duration_days(), _duration_start_date() (+13 more)

### Community 16 - "SlackCommandProcessor"
Cohesion: 0.11
Nodes (8): Path, Safe Slack command polling and dispatch., Parsed Slack command., Poll a Slack channel for whitelisted bot commands., SlackCommand, SlackCommandProcessor, Tests for safe Slack command parsing., SlackCommandTests

### Community 17 - "order_journal.py"
Cohesion: 0.09
Nodes (46): _base_row(), _closed_positions_by_symbol(), _f(), _fill_price_from_result(), _infer_bracket_exit_from_bars(), load_order_journal(), load_reviews(), _merge_reviews() (+38 more)

### Community 18 - "server.py"
Cohesion: 0.06
Nodes (74): api_crypto(), api_crypto_add(), api_crypto_analyze(), api_crypto_bars(), api_crypto_bulk_add(), api_crypto_manual_orders(), api_crypto_order_simulate(), api_crypto_remove() (+66 more)

### Community 19 - "backtest/inefficiency_reclaim.py"
Cohesion: 0.14
Nodes (23): BacktestCosts, BacktestTrade, _bar_contains_time(), build_backtest_report(), _entry_fill(), _exit_touches(), _maximum_drawdown(), Any (+15 more)

### Community 20 - "test_decision_models.py"
Cohesion: 0.09
Nodes (29): _first(), _float_or_none(), mapping_to_candidate(), datetime, Adapters from deterministic strategy signals into decision contracts., Adapt a calculated non-stock trade plan to the common decision contract., Adapt a fully normalized ``TradeSignal`` without recalculating it., _signal_timestamp() (+21 more)

### Community 21 - "components.py"
Cohesion: 0.12
Nodes (42): render_dashboard(), render_header(), render_navigation(), bias_card(), blocked_news_panel(), _clean(), _esc(), feature_card() (+34 more)

### Community 22 - "Direction"
Cohesion: 0.13
Nodes (27): _historical_analysis_candidate(), Remove live-only risk gates from an anchor-date analysis result., ConfirmationType, Direction, OrderPlan, Enum, str, ScoreBreakdown (+19 more)

### Community 23 - "TradeSignal"
Cohesion: 0.07
Nodes (40): build_partial_targets(), DecisionSink, Record a decision into ``sink`` if one is provided; otherwise do nothing., record(), detect_false_breakout_complex(), DecisionSink, Router-compatible wrapper returning a TradeSignal for complex false breakouts., detect_false_breakout_one_bar() (+32 more)

### Community 24 - "src/config.py"
Cohesion: 0.09
Nodes (29): Logger, BrokerConfig, _csv_env(), _csv_env_file_or_fallback(), _csv_env_with_fallback(), _dedupe_preserve_order(), _env_bool(), _env_float() (+21 more)

### Community 25 - "premarket.py"
Cohesion: 0.09
Nodes (31): _duration_to_trading_rows(), _level_center(), _level_daily_window(), _level_zone(), _premarket_monitor_idea(), Premarket scanning job., Return a monitor-only idea when the symbol has enough clean level room., Build the premarket research plan and level journal. (+23 more)

### Community 26 - "AutonomousStockWorker"
Cohesion: 0.06
Nodes (54): api_decision_lab_status(), api_decision_lab_test_provider(), _provider_test_state(), Run the shared configured-provider WAIT diagnostic without trading state., _set_provider_test_state(), ProviderHealthResult, Sanitized result suitable for a worker heartbeat or dashboard response., PreflightResult (+46 more)

### Community 28 - "AccountState"
Cohesion: 0.19
Nodes (7): AccountState, evaluate_hard_gates(), Decimal, Quote, _regime_aligned(), StrategyContext, PlanningAndGateTests

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
Cohesion: 0.14
Nodes (32): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+24 more)

### Community 33 - "levels_export.py"
Cohesion: 0.16
Nodes (19): _center(), _clean_gap_annotations(), _coerce_float(), export_premarket_levels_report(), _gap_to_upper_level(), _level_key(), _level_price(), _optimization_reason() (+11 more)

### Community 34 - "project/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.20
Nodes (23): load_tracked_positions(), _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity() (+15 more)

### Community 35 - "Trade Log"
Cohesion: 0.00
Nodes (500): End Of Day (2026-04-27T22:05:40), End Of Day (2026-04-27T22:06:16), End Of Day (2026-04-27T22:13:29), End Of Day (2026-04-28T16:10:04), End Of Day (2026-04-29T16:10:04), End Of Day (2026-04-30T16:10:04), End Of Day (2026-04-30T16:11:49), End Of Day (2026-04-30T16:39:25) (+492 more)

### Community 36 - "rebound.py"
Cohesion: 0.13
Nodes (24): calculate_stop_loss(), round_number_guard(), _approached_from_above(), _approached_from_below(), _atr_used(), _build_long_rebound(), _build_short_rebound(), _current_session_bars() (+16 more)

### Community 37 - "MarketDataService"
Cohesion: 0.08
Nodes (15): main(), One-off backfill of OHLCV bars for the dashboard. Connects to TWS / IB Gateway…, MarketDataService, DataFrame, datetime, Fetch a timeframe for an incremental strategy cache., Provide reusable market data retrieval wrappers., Allow delayed data when the account lacks a live subscription. (+7 more)

### Community 38 - "Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.20
Nodes (23): panel_header(), _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity() (+15 more)

### Community 39 - "autonomous_stock_preflight.py"
Cohesion: 0.11
Nodes (18): check(), main(), Read-only preflight for ``ibkr_paper_autonomous``. The default invocation reads…, _provider_evidence(), Any, Path, Read-only readiness checks for the persistent autonomous stock worker. This…, Run mode-aware, read-only readiness checks. Broker checks use only… (+10 more)

### Community 40 - "_bars"
Cohesion: 0.19
Nodes (15): ChartHistorySpec, _bars(), ChartHistoryTests, get_4h_bars(), get_daily_bars(), get_weekly_bars(), load(), save() (+7 more)

### Community 41 - "false_breakout_continuation.py"
Cohesion: 0.17
Nodes (17): _context(), _continuation_score(), _continuation_stop(), _current_session_uptrend_from_level(), detect_false_breakout_continuation(), _is_prior_false_breakdown(), _normalize_bars(), DataFrame (+9 more)

### Community 42 - "NewsRiskFilter"
Cohesion: 0.18
Nodes (6): _matching_headlines(), NewsRiskFilter, Evaluate symbol-specific and macro news risk before trading., NewsBlockingTests, Tests for news blocking., StubNewsService

### Community 44 - "Autonomous LLM Agent — Remediation Re-Review"
Cohesion: 0.06
Nodes (35): 10. Paper Protection Scheduling, 11. Paper Recovery, 12. Account Identity, 13. Audit Fail-Closed Behavior, 14. Broker Order Provenance, 15. Mode Isolation, 16. News Isolation, 17. Test Coverage (+27 more)

### Community 45 - "load_stock_symbols"
Cohesion: 0.19
Nodes (17): api_watchlist_add(), api_watchlist_remove(), _onboard_client_id(), _strategy_payload(), _client_id(), _load_symbols(), main(), Path (+9 more)

### Community 46 - "test_execute_requests.py"
Cohesion: 0.11
Nodes (9): ExecuteWorkerTests, FakeBroker, FakeOrderManager, OrderRequestQueueTests, _paper(), Tests for the dashboard order-request queue and the execution worker., Minimal OrderResult-like object the fake broker returns., _Recorder (+1 more)

### Community 47 - "candles_with_levels"
Cohesion: 0.14
Nodes (18): candles_with_levels(), capital_gauges(), _focus_price(), forecast_rr_scatter(), forecast_sensitivity(), _labeled_trade_level_ids(), _level_price(), mark_forecast_position() (+10 more)

### Community 48 - "Levels Log"
Cohesion: 0.01
Nodes (141): Daily Levels (2026-04-27T22:02:49), Daily Levels (2026-04-27T22:03:01), Daily Levels (2026-04-27T22:04:21), Daily Levels (2026-04-27T22:12:41), Daily Levels (2026-04-27T22:36:13), Daily Levels (2026-04-27T22:45:49), Daily Levels (2026-04-27T22:47:57), Daily Levels (2026-04-27T22:53:40) (+133 more)

### Community 49 - "test_autonomous_llm_remediation.py"
Cohesion: 0.15
Nodes (12): SimpleNamespace, _candidate(), _config(), test_audit_failure_fails_closed_before_paper_mutation(), test_decision_queue_is_bounded_and_shadow_deduplicates_pending_work(), decide(), test_live_reconciliation_marks_uncertain_submission_without_retry(), test_news_is_omitted_from_autonomous_snapshot_when_disabled() (+4 more)

### Community 50 - "IBKR Gerchik Bot"
Cohesion: 0.07
Nodes (27): Configuration, Current Limitations, Dashboard, Data And Jobs, Execution And Replay, Hard Filters, Inefficiency Reclaim Strategy, State And Audit (+19 more)

### Community 51 - "NewsService"
Cohesion: 0.14
Nodes (7): fx_pair_components(), normalize_symbol(), Settings, NewsService, Fetch symbol, macro, and earnings context from NewsAPI.ai / Event Registry., _build_research_symbols(), SymbolHandlingTests

### Community 52 - "Research Log"
Cohesion: 0.00
Nodes (476): Premarket Research (2026-04-27T22:02:49), Premarket Research (2026-04-27T22:03:01), Premarket Research (2026-04-27T22:04:21), Premarket Research (2026-04-27T22:12:41), Premarket Research (2026-04-27T22:36:13), Premarket Research (2026-04-27T22:45:49), Premarket Research (2026-04-27T22:47:57), Premarket Research (2026-04-27T22:53:40) (+468 more)

### Community 53 - "crypto/tradingview_webhook.py"
Cohesion: 0.19
Nodes (27): BackgroundTasks, api_crypto_tradingview_webhook(), _append_execution(), _as_float(), enqueue_tradingview_webhook(), _execution_from_signal(), _extract_okx_order_id(), _find_execution() (+19 more)

### Community 54 - "test_irs_scheduler.py"
Cohesion: 0.09
Nodes (29): api_services(), _autonomous_stock_worker_status(), _irs_schedule_status(), _iso_age_seconds(), _latest_job_log_status(), _lock_status(), _market_collector_window_status(), _next_manifest_run() (+21 more)

### Community 55 - "decision_log.py"
Cohesion: 0.19
Nodes (16): _acquire_daily_decisions_lock(), _apply_to_decisions(), _build_attempt(), _decision_rank(), load(), _load_daily_decisions(), _lock_is_stale(), persist() (+8 more)

### Community 56 - "DisplacementAndZoneTests"
Cohesion: 0.27
Nodes (3): DisplacementAndZoneTests, make_bar(), RobustATRTests

### Community 57 - "symbol_onboarding.py"
Cohesion: 0.09
Nodes (45): main(), Path, _symbol_from_intraday_file(), bar_metadata(), bar_path(), _bar_store_lock(), _ensure_dir(), index_snapshot() (+37 more)

### Community 58 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 59 - "ensure_irs_history"
Cohesion: 0.17
Nodes (17): ensure_irs_history(), history_specs(), IRSHistorySpec, _mark_skipped_remaining(), _paced_get_bars(), Any, Exception, Incremental historical-data hydration for IRS timeframes. (+9 more)

### Community 61 - "Gerchik Trading Bot — Architecture Audit"
Cohesion: 0.05
Nodes (42): 10. Pre-Open, 11. Stock Screener, 12. Crypto Screener, 13. Intraday Engine, 15. Strategy Stocks, 16. Strategy Crypto, 17. Inefficiency Reclaim, 18. Forecast Engine (+34 more)

### Community 62 - "OKXClient"
Cohesion: 0.22
Nodes (5): OKXClient, Any, DataFrame, Place a SPOT order, optionally with attached market-exit TP/SL. Private…, OKXSpotOrderPayloadTests

### Community 63 - "IBKRClient"
Cohesion: 0.07
Nodes (22): _account_values(), IBKRClient, _normalized_broker_account_id(), Any, Resize an existing open child order, used for partial-fill protection., Thin broker adapter responsible for connectivity and core order actions., Connect to IBKR TWS or IB Gateway with retry logic., Return only a supported brokerage account identity. (+14 more)

### Community 64 - "test_ibkr_paper_autonomous.py"
Cohesion: 0.17
Nodes (16): fixture, parametrize, Broker, candidate(), config(), context(), OrderManager, paper_runtime() (+8 more)

### Community 65 - "AgentMode"
Cohesion: 0.22
Nodes (19): _decision_lab_warnings(), DecisionAgentConfig, Configuration for the bounded LLM decision layer. ``mode=off`` is the safe…, AgentMode, test_live_configuration_requires_verified_allowlisted_account(), _candidate(), _config(), _context() (+11 more)

### Community 66 - "level_strength.py"
Cohesion: 0.27
Nodes (8): apply_strength_scores(), _fallback_score(), filter_strong_levels(), Level strength scoring helpers., Return the precomputed strength score, falling back to the legacy field., score_level(), LevelStrengthTests, Tests for level strength scoring.

### Community 67 - "session_utils.py"
Cohesion: 0.04
Nodes (92): NowProvider, SleepProvider, Slack webhook notifications., Interactive Brokers connectivity built on top of ib_insync., append_markdown_log(), Append a timestamped Markdown section to a log file., News-driven trade blocking logic., News API access layer. (+84 more)

### Community 68 - "manual_order.py"
Cohesion: 0.10
Nodes (29): api_crypto_order_place(), Submit a manual Strategy Crypto order to OKX Demo only., _client_order_id(), _decimal(), _decimal_text(), execute_manual_demo_order(), _floor_to_step(), _instrument() (+21 more)

### Community 69 - "broker_reconcile.py"
Cohesion: 0.07
Nodes (32): api_orders_journal(), _execution_closed_record(), _execution_matches_request(), _now(), _order_ids(), _parse_dt(), _place_requests(), Any (+24 more)

### Community 70 - "breakout.py"
Cohesion: 0.16
Nodes (22): calculate_take_profit(), reward_risk_ratio(), _acts_as_resistance(), _acts_as_support(), _atr_used(), _bar_index(), _breakout_is_overextended(), _build_long_signal() (+14 more)

### Community 71 - "Weekly Log"
Cohesion: 0.07
Nodes (26): Weekly Log, Weekly Metrics (2026-04-23T22:20:31), Weekly Metrics (2026-04-23T23:20:28), Weekly Review (2026-04-23T23:46:43), Weekly Review (2026-05-01T16:25:02), Weekly Review (2026-05-13T22:21:19), Weekly Review (2026-05-15T16:25:02), Weekly Review (2026-05-22T16:25:02) (+18 more)

### Community 73 - "market_data_collector.py"
Cohesion: 0.13
Nodes (9): _collector_session_bounds(), _collector_session_is_open(), _next_collector_open(), datetime, Independent intraday bar collector for dashboard continuity., Return the intraday collection window for the date represented by ``now``., Collect bars without account synchronization, signals, or orders., run_market_data_collector() (+1 more)

### Community 75 - "InefficiencyReclaimPaperExecutor"
Cohesion: 0.06
Nodes (15): InefficiencyReclaimPaperExecutor, IRSBroker, IRSExecutionPolicy, IRSExecutionResult, IRSExpiryCancellationResult, IRSReconnectResult, Any, datetime (+7 more)

### Community 76 - "should_trigger_kill_switch"
Cohesion: 0.31
Nodes (6): _equity_symbols(), Trading kill switch conditions., Block all trading when safety conditions are breached., should_trigger_kill_switch(), KillSwitchTests, Tests for kill switch behavior.

### Community 77 - "InefficiencyReclaimSettings"
Cohesion: 0.40
Nodes (3): InefficiencyReclaimSettings, Validated environment-facing settings for the isolated IRS subsystem., Build the pure strategy config without making that module read env.

### Community 78 - "crypto/config.py"
Cohesion: 0.36
Nodes (7): CryptoConfig, _env_bool(), _env_csv(), _env_float(), _env_int(), _env_str(), Crypto bot configuration. API secrets should live in the local .env file only.…

### Community 80 - "forex/tradingview_webhook.py"
Cohesion: 0.27
Nodes (16): _append_execution(), _as_float(), _as_int(), _expected_bot_id(), ForexTradingViewWebhookError, handle_forex_tradingview_webhook(), load_forex_tradingview_state(), _normalize_forex_symbol() (+8 more)

### Community 81 - "third_touch.py"
Cohesion: 0.33
Nodes (6): detect_third_touch(), DataFrame, Series, Third touch setup detection., Detect the third qualified interaction with a level., _touch_indices()

### Community 82 - "stocks/tradingview_webhook.py"
Cohesion: 0.27
Nodes (17): _append_execution(), _as_float(), _as_int(), _expected_bot_id(), _first_float(), handle_stock_tradingview_webhook(), load_stock_tradingview_state(), _normalize_stock_symbol() (+9 more)

### Community 83 - "PaperPortfolio"
Cohesion: 0.16
Nodes (13): _now(), _number(), PaperPortfolio, Path, Durable, isolated paper portfolio for ``paper_autonomous`` mode., Tighten protection only; this never loosens or removes a stop., Apply deterministic stop/target exits without consulting an LLM., A crash-safe ledger that never calls a broker or mutates bot state. (+5 more)

### Community 84 - "Autonomous LLM Gerchik Agent — Adversarial Safety Review"
Cohesion: 0.04
Nodes (47): 10. Duplicate / Concurrency Analysis, 11. Ownership and Same-Symbol Collisions, 12. Broker vs Local Reconciliation, 13. LLM Latency Impact, 14. Provider Failure Behavior, 15. Response Validation, 16. Risk-Gate Bypass Analysis, 17. Price / Stale-Data Revalidation (+39 more)

### Community 88 - "add_bmsb_signals"
Cohesion: 0.40
Nodes (5): add_bmsb_signals(), main(), DataFrame, BMSB Strategy 1: Bull Market Support Band conversion. Long-only strategy on…, Add daily signals from completed weekly BMSB values.

### Community 89 - "symbols.py"
Cohesion: 0.15
Nodes (10): add_crypto_symbol(), _dedupe(), load_crypto_symbol_inputs(), Path, Normalize TradingView/OKX crypto symbols into OKX instrument ids., Remove every configured spelling that resolves to ``symbol``. Comments and…, Persist a crypto symbol input unless its normalized instrument exists. Returns…, remove_crypto_symbol() (+2 more)

### Community 90 - "AutonomousGerchikAgent"
Cohesion: 0.09
Nodes (25): AgentResult, AgentRuntimeContext, AutonomousGerchikAgent, default_agent(), Any, Run deterministic Paper protection independently of the LLM., Return the process-local agent for the current config. The cache keeps shadow-…, Validate a provider without exposing broker/account data or trading. (+17 more)

### Community 91 - "api_inefficiency_reclaim"
Cohesion: 0.21
Nodes (11): api_inefficiency_reclaim(), api_update_inefficiency_reclaim_settings(), _parse_irs_min_daily_history_rows(), _parse_irs_minimum_display_score(), Path, Atomically update one allowlisted environment setting., Run the disabled-by-default IRS analysis scan over persisted bars., _set_irs_min_daily_history_rows() (+3 more)

### Community 92 - "SlackAlerter"
Cohesion: 0.15
Nodes (4): Path, Send alerts to Slack with graceful degradation when disabled., SlackAlerter, SlackHeartbeatTests

### Community 93 - "DecisionAudit"
Cohesion: 0.16
Nodes (9): DecisionAudit, _json(), _now(), Connection, Path, Small SQLite repository; all writes are explicit and queryable., Count successful broker/simulated execution links after an ISO boundary., Atomically claim one executable identity using SQLite locking. (+1 more)

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

### Community 112 - "validator.py"
Cohesion: 0.16
Nodes (13): calculate_position_size(), position_value_ok(), Position sizing logic., Size a position so the loss to stop equals the allowed risk budget., _optional_float(), Universal trade validation rules., Explainable validator wrapper that preserves deterministic reason tracking., Validate a trade candidate against hard trading rules. (+5 more)

### Community 117 - "execute_requests.py"
Cohesion: 0.29
Nodes (17): _effective_dry_run(), _f(), _market_order_tif(), _position_symbol_key(), _process_close(), _process_one(), process_pending_once(), _process_place() (+9 more)

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

### Community 135 - "candles.py"
Cohesion: 0.27
Nodes (17): average_range(), close_above_level(), close_below_level(), close_location(), full_range(), has_compression(), is_abnormal_candle(), is_bearish() (+9 more)

### Community 136 - "OrderManager"
Cohesion: 0.23
Nodes (4): OrderManager, Place a human-initiated (dashboard) order. This bypasses the *autonomous*…, Execute validated trades and record the resulting actions., ManualOrderTests

### Community 137 - "test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference"
Cohesion: 0.20
Nodes (4): test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference(), place_market_bracket_order(), _intraday_bars(), DataFrame

### Community 138 - "identity.py"
Cohesion: 0.29
Nodes (6): broker_order_ref(), execution_key(), order_fingerprint(), Stable identities shared by autonomous persistence and broker provenance., Return the mode/agent/candidate namespace key used by SQLite claims., Create a compact deterministic orderRef suitable for IBKR order fields.

### Community 139 - "Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab, Source Nodes

### Community 140 - "check_llm_shadow_ready.py"
Cohesion: 0.47
Nodes (5): _check(), main(), Path, Read-only configuration preflight for the first LLM Shadow experiment. This…, _read_only_schema()

### Community 141 - "BrokerStub"
Cohesion: 0.05
Nodes (4): notify_inefficiency_reclaim_scan(), BrokerStub, AlerterStub, IRSNotificationTests

### Community 143 - "Findings and disposition"
Cohesion: 0.10
Nodes (20): Autonomous LLM Gerchik Agent — Safety Remediation Report, Executive result, F-CRIT-01 — No atomic candidate claim or execution reservation, F-CRIT-02 — Broker acceptance can precede durable execution identity, F-CRIT-03 — No authoritative broker reconciliation at the autonomous execution boundary, F-HIGH-01 — No post-LLM quote and risk revalidation, F-HIGH-02 — Unknown account identity can bypass the configured allowlist, F-HIGH-03 — Synchronous provider latency can delay protective management (+12 more)

### Community 144 - "JobSessionUtilsTests"
Cohesion: 0.15
Nodes (6): job_loop_lock(), _lock_is_stale(), Path, Prevent overlapping session loops across scheduled invocations., JobSessionUtilsTests, TestCase

### Community 146 - "40. Architecture Diagrams"
Cohesion: 0.40
Nodes (5): 40. Architecture Diagrams, Background jobs / scheduler flow, Complete system architecture, Important module dependency map, Market data flow

### Community 148 - "7. Market Data Architecture"
Cohesion: 0.50
Nodes (4): 7. Market Data Architecture, Crypto, Forex, Stocks

### Community 153 - "IBKR Paper Autonomous implementation report"
Cohesion: 0.33
Nodes (5): Conditions for manual validation, Delivered controls, IBKR Paper Autonomous implementation report, Scope and verdict, Verification performed

### Community 154 - "IBKR Paper Autonomous validation runbook"
Cohesion: 0.33
Nodes (5): Controlled observation, Evidence to retain, IBKR Paper Autonomous validation runbook, Immediate stop conditions, Preconditions

### Community 156 - "IBKRDependencyError"
Cohesion: 0.33
Nodes (5): _ensure_event_loop(), IBKRDependencyError, RuntimeError, ib_insync/eventkit expects a current event loop on newer Python versions., Raised when ib_insync is unavailable.

### Community 158 - "Shadow runtime preparation report"
Cohesion: 0.20
Nodes (9): Added validation assets, Completed implementation: News timing isolation, Conditions that must remain true, Effective configuration contract, Known limitations and unresolved evidence, Readiness, Runtime data flow to verify later, Shadow runtime preparation report (+1 more)

### Community 159 - "Decision Lab provider diagnostics report"
Cohesion: 0.17
Nodes (11): API contract, Decision Lab provider diagnostics report, Files changed, `GET /api/decision-lab/status`, Known limitations, Manual verification steps, `POST /api/decision-lab/test-provider`, Safety boundary (+3 more)

### Community 160 - "LLM Shadow runtime validation"
Cohesion: 0.20
Nodes (9): 1. Scope and prerequisites, 2. Exact process-scoped Shadow configuration, 3. Read-only preflight, 4. Exact first-run sequence, 5. Expected News timing and isolation, 6. Read-only observation points, 6a. Decision Lab -> Test LLM Connection, 7. Stop conditions (+1 more)

### Community 162 - "policy.py"
Cohesion: 0.18
Nodes (17): PositionAction, Enum, str, AutonomousRiskGate, daily_loss_exceeded(), evaluate_position_action(), PositionActionDecision, Any (+9 more)

### Community 165 - "Unified Dashboard Stack Implementation Report"
Cohesion: 0.15
Nodes (12): 10. Limitations and controlled-validation status, 11. Operator workflow, 1. Existing and new launcher behavior, 2. Worker architecture and entry point, 3. Startup order, 4. Heartbeat, state, and duplicate prevention, 5. Closed-market behavior, 6. Mode-aware preflight and retry behavior (+4 more)

### Community 167 - "analysis.py"
Cohesion: 0.12
Nodes (39): load_crypto_symbols(), analyze_symbol(), collect_crypto_bars(), configured_crypto_symbols(), load_crypto_state(), _quiet_shared_level_logs(), Crypto Gerchik-style analysis using the shared stock strategy logic., Keep crypto batch scans from flooding the shared stock bot log file. (+31 more)

### Community 170 - "api_bars"
Cohesion: 0.22
Nodes (4): api_bars(), _live_daily_bars(), daily_bars_from_intraday(), Aggregate saved intraday bars into daily OHLCV rows. The resulting ``date``…

### Community 171 - "test_unified_stack_lifecycle.py"
Cohesion: 0.52
Nodes (6): test_dashboard_service_contract_exposes_worker_fields_without_account_id(), test_hard_reset_reuses_complete_stack_and_documents_protection(), test_start_launcher_contains_complete_stack_in_required_order(), test_startup_report_is_read_only_and_requires_heartbeat_lock_ownership(), test_stop_script_has_exact_worker_signal_and_required_shutdown_order(), _text()

### Community 172 - "report_autonomous_stock_worker_start.py"
Cohesion: 0.60
Nodes (4): _age(), _alive(), main(), Report resident-worker startup evidence for the Windows launcher. This is read-…

## Knowledge Gaps
- **1445 isolated node(s):** `schema_migrations`, `scanner_runs`, `BrokerConfig`, `RiskConfig`, `StrategyConfig` (+1440 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 2329 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **43 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Level` connect `Level` to `src/main.py`, `level_strength.py`, `session_utils.py`, `forecast.py`, `false_breakout_one_bar.py`, `breakout.py`, `rebound.py`, `false_breakout_continuation.py`, `TradeSignal`, `decision_log.py`, `premarket.py`, `test_p0_fixes.py`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Why does `TradeSignal` connect `TradeSignal` to `src/main.py`, `DecisionCandidate`, `false_breakout_one_bar.py`, `test_order_flow.py`, `OrderManager`, `test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference`, `Level`, `test_autonomous_guard_rejects_unknown_stale_and_wide_quotes`, `test_decision_models.py`, `rebound.py`, `false_breakout_continuation.py`, `test_execute_requests.py`, `test_autonomous_llm_remediation.py`, `test_ibkr_paper_autonomous.py`, `session_utils.py`, `breakout.py`, `AutonomousGerchikAgent`, `execute_requests.py`, `test_news_timing_isolation.py`?**
  _High betweenness centrality (0.025) - this node is a cross-community bridge._
- **Why does `IBKRClient` connect `IBKRClient` to `src/main.py`, `session_utils.py`, `MarketDataService`, `broker_reconcile.py`, `autonomous_stock_preflight.py`, `OrderManager`, `_connect_broker_with_startup_retry`, `NewsRiskFilter`, `test_execute_requests.py`, `NewsService`, `test_ibkr_account_identity.py`, `IBKRDependencyError`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Are the 56 inferred relationships involving `Level` (e.g. with `_level_center()` and `_level_zone()`) actually correct?**
  _`Level` has 56 INFERRED edges - model-reasoned connections that need verification._
- **Are the 34 inferred relationships involving `TradeSignal` (e.g. with `AutonomousGerchikAgent` and `_signal_timestamp()`) actually correct?**
  _`TradeSignal` has 34 INFERRED edges - model-reasoned connections that need verification._
- **What connects `schema_migrations`, `scanner_runs`, `BrokerConfig` to the rest of the system?**
  _1445 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `src/main.py` be split into smaller, more focused modules?**
  _Cohesion score 0.034040734938043014 - nodes in this community are weakly interconnected._