# Graph Report - ibkr-gerchik-bot  (2026-08-07)

## Corpus Check
- 207 files · ~9,896,446 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3066 nodes · 7459 edges · 135 communities (105 shown, 30 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 198 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `777b754e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- symbol_onboarding.py
- src/main.py
- InefficiencyReclaimPaperExecutor
- forecast.py
- false_breakout_one_bar.py
- order_requests.py
- OrderResult
- timedelta
- append_workflow_snapshot
- project/support.js
- levels.py
- Trading Bot Dashboard/support.js
- market_screener.py
- Bar
- data_access.py
- NasdaqDataClient
- SlackCommandProcessor
- order_journal.py
- server.py
- backtest/inefficiency_reclaim.py
- test_execute_requests.py
- components.py
- execution/inefficiency_reclaim.py
- TradeSignal
- src/config.py
- forex/tradingview_webhook.py
- nearest_level_details
- validator.py
- strategy/inefficiency_reclaim.py
- _make_level
- crypto/tradingview_webhook.py
- scanners/inefficiency_reclaim.py
- dashboard/app.py
- levels_export.py
- project/ibkr-gerchik-bot/dashboard/app.py
- Trade Log
- rebound.py
- execution/order_manager.py
- Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py
- stocks/tradingview_webhook.py
- premarket.py
- false_breakout_continuation.py
- NewsRiskFilter
- chart_history.py
- show_df
- load_stock_symbols
- session_utils.py
- candles_with_levels
- Levels Log
- _write_env_setting
- IBKR Gerchik Bot
- eod.py
- StrategyCandidate
- _BrokerStub
- _irs_schedule_status
- decision_log.py
- BrokerStub
- MarketDataService
- What You Must Do When Invoked
- _daily_live_frame
- 001_inefficiency_reclaim_up.sql
- InefficiencyReclaimSettings
- InefficiencyReclaimStore
- DashboardChartTests
- NewsService
- SlackAlerter
- Level
- collect_watchlist_intraday_bars
- crypto/order_manager.py
- broker_reconcile.py
- breakout.py
- Weekly Log
- DashboardDataAccessTests
- detect_false_breakout_one_bar
- execute_requests.py
- reward_risk_ratio
- should_trigger_kill_switch
- _FakeEvent
- jobs/inefficiency_reclaim.py
- _BrokerStub
- RiskManager
- third_touch.py
- _connect_broker_with_startup_retry
- SymbolHandlingTests
- build_task_scheduler_command
- PersistentStoreTests
- _MarketDataServiceStub
- setup_logging
- add_bmsb_signals
- maybe_commit_and_push
- IBKRClient
- OrderManager
- _NewsRiskFilterStub
- ValidatorTests
- DESIGN.md
- graphify reference: extra exports and benchmark
- disable_dashboard_cache
- GoldenFixtureTests
- dashboard/__init__.py
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
- false_breakout.py
- graphify reference: query, path, explain
- Trading Operations Dashboard
- Trading Strategy
- OrderRequestQueueTests
- CODING AGENTS: READ THIS FIRST
- _json_value
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- Weekly Review
- AGENTS.md
- extraction-spec.md
- irs/README.md
- _NewsRiskFilterStub

## God Nodes (most connected - your core abstractions)
1. `Trade Log` - 377 edges
2. `Level` - 132 edges
3. `Levels Log` - 114 edges
4. `TradeSignal` - 75 edges
5. `IBKRClient` - 72 edges
6. `MarketDataService` - 62 edges
7. `SlackAlerter` - 59 edges
8. `InefficiencyReclaimStore` - 51 edges
9. `Bar` - 49 edges
10. `OrderManager` - 42 edges

## Surprising Connections (you probably didn't know these)
- `ForecastScenario` --uses--> `Level`  [INFERRED]
  dashboard/forecast.py → src/strategy/levels.py
- `api_watchlist()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py
- `api_market_screener()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py
- `api_inefficiency_reclaim()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py
- `api_crypto()` --indirect_call--> `load_tradingview_state()`  [INFERRED]
  dashboard_react/server.py → src/crypto/tradingview_webhook.py

## Import Cycles
- None detected.

## Communities (135 total, 30 thin omitted)

### Community 0 - "symbol_onboarding.py"
Cohesion: 0.10
Nodes (29): bar_metadata(), bar_path(), _bar_store_lock(), _ensure_dir(), index_snapshot(), load_bars(), _normalize_frame(), DataFrame (+21 more)

### Community 1 - "src/main.py"
Cohesion: 0.06
Nodes (43): Namespace, ensure_directories(), Create runtime and memory directories expected by the application., Atomically publish IRS scheduler state for the Service Health dashboard., record_irs_runtime_status(), calculate_open_risk_amount(), Estimate current open risk from tracked positions., _account_equity_from_summary() (+35 more)

### Community 2 - "InefficiencyReclaimPaperExecutor"
Cohesion: 0.12
Nodes (13): Protocol, InefficiencyReclaimPaperExecutor, IRSBroker, IRSExecutionPolicy, IRSExecutionResult, IRSExpiryCancellationResult, IRSReconnectResult, Any (+5 more)

### Community 3 - "forecast.py"
Cohesion: 0.13
Nodes (23): _bars_freshness(), calculate_open_risk(), _candidate(), ForecastScenario, _number(), projected_level_candidates(), Any, DataFrame (+15 more)

### Community 4 - "false_breakout_one_bar.py"
Cohesion: 0.12
Nodes (46): PatternName, SignalSide, detect_false_breakout(), Gerchik-style complex 3+ bar false breakout detection helpers with…, Detect a complex 3+ bar false breakout structure around a level zone., _atr_filters_ok(), _atr_used_from_session(), _atr_used_from_zone() (+38 more)

### Community 5 - "order_requests.py"
Cohesion: 0.15
Nodes (24): _acquire_lock(), claim_pending(), list_requests(), _load(), _mutate(), _now(), pending_requests(), Any (+16 more)

### Community 6 - "OrderResult"
Cohesion: 0.13
Nodes (10): OrderResult, _BrokerStub, _chart_history_ready(), _manual_candidate_signal(), _MarketDataStub, _MultiSessionMarketDataStub, _NewsFilterStub, OrderFlowTests (+2 more)

### Community 7 - "timedelta"
Cohesion: 0.14
Nodes (5): PaperExecutorTests, make_zone(), RetraceAndConfirmationTests, zone(), timedelta

### Community 8 - "append_workflow_snapshot"
Cohesion: 0.11
Nodes (17): _default_state(), load_runtime_state(), Read the shared runtime state with log-based recovery fallback., append_workflow_snapshot(), Any, Path, Structured workflow logging and recovery helpers., Append a human-readable section plus a machine-readable JSON snapshot. (+9 more)

### Community 9 - "project/support.js"
Cohesion: 0.08
Nodes (42): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), createExternalModules(), createHelmetManager(), createPseudoSheet() (+34 more)

### Community 10 - "levels.py"
Cohesion: 0.10
Nodes (36): analyze_symbol(), _atr_distances(), _build_zone(), calculate_atr(), _cluster_levels(), dedupe_levels(), detect_levels(), _false_breakout_indices_for_zone() (+28 more)

### Community 11 - "Trading Bot Dashboard/support.js"
Cohesion: 0.08
Nodes (42): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), createExternalModules(), createHelmetManager(), createPseudoSheet() (+34 more)

### Community 12 - "market_screener.py"
Cohesion: 0.13
Nodes (36): _add_metrics(), _apply_anchor_date(), _approach_profile(), _bar_position(), _business_day_offset(), _date_value(), _detect_signals(), _event_dates() (+28 more)

### Community 13 - "Bar"
Cohesion: 0.14
Nodes (36): _diagnose_no_candidate(), Bar, _bounded(), build_inefficiency_zone(), build_low_overlap_zone(), build_strict_gap_zone(), calculate_overlap_ratio(), calculate_relative_volume() (+28 more)

### Community 14 - "data_access.py"
Cohesion: 0.12
Nodes (41): bars_index(), blocked_news_summary(), crypto_bars_index(), _filter_watchlist_symbols(), freshness(), get_crypto_bars(), latest_trade_log_sections(), _latest_workflow_watchlist_for_symbols() (+33 more)

### Community 15 - "NasdaqDataClient"
Cohesion: 0.10
Nodes (21): date, NasdaqDataConfig, Optional Nasdaq market-data fallback for historical candles., _cloud_precision(), _cloud_range(), _daily_cloud_range(), _duration_days(), _duration_start_date() (+13 more)

### Community 16 - "SlackCommandProcessor"
Cohesion: 0.11
Nodes (8): Path, Safe Slack command polling and dispatch., Parsed Slack command., Poll a Slack channel for whitelisted bot commands., SlackCommand, SlackCommandProcessor, Tests for safe Slack command parsing., SlackCommandTests

### Community 17 - "order_journal.py"
Cohesion: 0.10
Nodes (43): _base_row(), _closed_positions_by_symbol(), _f(), _fill_price_from_result(), _infer_bracket_exit_from_bars(), load_order_journal(), load_reviews(), _merge_reviews() (+35 more)

### Community 18 - "server.py"
Cohesion: 0.12
Nodes (40): api_bars(), api_crypto(), api_crypto_add(), api_crypto_analyze(), api_crypto_bars(), api_crypto_symbol(), api_crypto_tradingview_state(), api_dashboard() (+32 more)

### Community 19 - "backtest/inefficiency_reclaim.py"
Cohesion: 0.17
Nodes (21): BacktestCosts, BacktestTrade, _bar_contains_time(), build_backtest_report(), _entry_fill(), _exit_touches(), _maximum_drawdown(), Any (+13 more)

### Community 20 - "test_execute_requests.py"
Cohesion: 0.19
Nodes (7): ExecuteWorkerTests, FakeBroker, FakeOrderManager, _paper(), Tests for the dashboard order-request queue and the execution worker., Minimal OrderResult-like object the fake broker returns., _Recorder

### Community 21 - "components.py"
Cohesion: 0.13
Nodes (40): render_dashboard(), render_header(), render_navigation(), bias_card(), blocked_news_panel(), _clean(), _esc(), feature_card() (+32 more)

### Community 22 - "execution/inefficiency_reclaim.py"
Cohesion: 0.18
Nodes (15): Fail-closed paper executor for ENTRY_ARMED IRS candidates., ImmutableZoneError, Decimal, SQLite audit store for IRS zones, setups, transitions, and execution records., Raised when a scan attempts to move persisted zone boundaries., SetupState, advance_setup_state(), can_transition() (+7 more)

### Community 23 - "TradeSignal"
Cohesion: 0.11
Nodes (26): build_partial_targets(), Shared strategy data models., TradeSignal, _detect_enabled_candidates(), _level_zone(), _note_value(), DataFrame, DecisionSink (+18 more)

### Community 24 - "src/config.py"
Cohesion: 0.15
Nodes (19): BrokerConfig, _csv_env(), _csv_env_file_or_fallback(), _csv_env_with_fallback(), _dedupe_preserve_order(), _env_bool(), _env_float(), _env_int() (+11 more)

### Community 25 - "forex/tradingview_webhook.py"
Cohesion: 0.25
Nodes (17): fx_pair_components(), _append_execution(), _as_float(), _as_int(), _expected_bot_id(), ForexTradingViewWebhookError, handle_forex_tradingview_webhook(), load_forex_tradingview_state() (+9 more)

### Community 26 - "nearest_level_details"
Cohesion: 0.21
Nodes (10): _coerce_float(), nearest_level_details(), datetime, Path, _quote_reference_price(), Excel exports for intraday scan outcomes., Write one intraday scan result workbook and return its path., Return the most relevant watchlist level for reporting. (+2 more)

### Community 27 - "validator.py"
Cohesion: 0.16
Nodes (13): calculate_position_size(), position_value_ok(), Position sizing logic., Size a position so the loss to stop equals the allowed risk budget., _optional_float(), Universal trade validation rules., Explainable validator wrapper that preserves deterministic reason tracking., Validate a trade candidate against hard trading rules. (+5 more)

### Community 28 - "strategy/inefficiency_reclaim.py"
Cohesion: 0.11
Nodes (24): AccountState, _add_rth_hours(), build_explanation(), build_signal_id(), calculate_entry_trigger(), calculate_structural_r(), calculate_technical_stop(), Direction (+16 more)

### Community 29 - "_make_level"
Cohesion: 0.11
Nodes (9): _make_level(), DataFrame, Verify that breakout and rebound honour the configured RR minimum. We patch…, Bars that produce a valid rebound setup but only ~2.5 R to the nearest level., TestBaseDetectFalseBreakoutDisabled, TestComplexStopBuffer, TestMinTouchesFromSettings, TestRRMinimumUnified (+1 more)

### Community 30 - "crypto/tradingview_webhook.py"
Cohesion: 0.05
Nodes (86): BackgroundTasks, load_crypto_symbols(), api_crypto_tradingview_webhook(), Resolve user-friendly crypto input to an OKX instrument id. People often type…, _resolve_crypto_add_instrument(), RuntimeError, collect_crypto_bars(), configured_crypto_symbols() (+78 more)

### Community 31 - "scanners/inefficiency_reclaim.py"
Cohesion: 0.17
Nodes (26): BarLoader, QuoteLoader, _account_state(), candidate_row(), classify_market_regime(), classify_market_trend_direction(), data_quality_diagnostics(), frame_to_bars() (+18 more)

### Community 32 - "dashboard/app.py"
Cohesion: 0.22
Nodes (22): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+14 more)

### Community 33 - "levels_export.py"
Cohesion: 0.16
Nodes (19): _center(), _clean_gap_annotations(), _coerce_float(), export_premarket_levels_report(), _gap_to_upper_level(), _level_key(), _level_price(), _optimization_reason() (+11 more)

### Community 34 - "project/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.21
Nodes (23): load_tracked_positions(), _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity() (+15 more)

### Community 35 - "Trade Log"
Cohesion: 0.01
Nodes (377): End Of Day (2026-04-27T22:05:40), End Of Day (2026-04-27T22:06:16), End Of Day (2026-04-27T22:13:29), End Of Day (2026-04-28T16:10:04), End Of Day (2026-04-29T16:10:04), End Of Day (2026-04-30T16:10:04), End Of Day (2026-04-30T16:11:49), End Of Day (2026-04-30T16:39:25) (+369 more)

### Community 36 - "rebound.py"
Cohesion: 0.12
Nodes (39): average_range(), body_size(), close_above_level(), close_below_level(), close_location(), full_range(), has_compression(), is_abnormal_candle() (+31 more)

### Community 37 - "execution/order_manager.py"
Cohesion: 0.15
Nodes (9): Slack webhook notifications., _ensure_event_loop(), IBKRDependencyError, Interactive Brokers connectivity built on top of ib_insync., ib_insync/eventkit expects a current event loop on newer Python versions., Raised when ib_insync is unavailable., Order execution orchestration., _intraday_bars() (+1 more)

### Community 38 - "Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.17
Nodes (27): panel_header(), Seconds since the worker last beat, or None if no heartbeat exists., True if a worker heartbeat exists and is fresh (within the stale window)., worker_age_seconds(), worker_is_alive(), _as_dict(), _as_list(), _confirm_close_position() (+19 more)

### Community 39 - "stocks/tradingview_webhook.py"
Cohesion: 0.27
Nodes (17): _append_execution(), _as_float(), _as_int(), _expected_bot_id(), _first_float(), handle_stock_tradingview_webhook(), load_stock_tradingview_state(), _normalize_stock_symbol() (+9 more)

### Community 40 - "premarket.py"
Cohesion: 0.13
Nodes (20): _duration_to_trading_rows(), _level_center(), _level_daily_window(), _level_zone(), _premarket_monitor_idea(), Premarket scanning job., Return a monitor-only idea when the symbol has enough clean level room., Build the premarket research plan and level journal. (+12 more)

### Community 41 - "false_breakout_continuation.py"
Cohesion: 0.18
Nodes (18): _context(), _continuation_score(), _continuation_stop(), _current_session_uptrend_from_level(), detect_false_breakout_continuation(), _is_prior_false_breakdown(), _normalize_bars(), DataFrame (+10 more)

### Community 42 - "NewsRiskFilter"
Cohesion: 0.14
Nodes (12): _matching_headlines(), NewsRiskFilter, Evaluate symbol-specific and macro news risk before trading., _preserve_existing_runtime_watchlist(), Load the prepared watchlist from state or latest workflow snapshot., Persist runtime state atomically from within long-running jobs., Preserve already-onboarded symbols when a partial job saves state., refresh_watchlist_if_missing() (+4 more)

### Community 43 - "chart_history.py"
Cohesion: 0.14
Nodes (25): main(), Path, _symbol_from_intraday_file(), backfill_daily_from_intraday(), ChartHistorySpec, daily_bars_from_intraday(), _daily_to_weekly(), ensure_required_chart_history() (+17 more)

### Community 44 - "show_df"
Cohesion: 0.27
Nodes (17): render_intraday(), render_reports(), mark_levels(), metric_grid(), DataFrame, Render a responsive grid of glass metric cards., show_df(), all_decision_attempts() (+9 more)

### Community 45 - "load_stock_symbols"
Cohesion: 0.15
Nodes (21): api_strategy(), api_watchlist_add(), api_watchlist_bulk_add(), api_watchlist_remove(), _parse_stock_symbol_upload(), _queue_bulk_stock_onboarding(), _client_id(), _load_symbols() (+13 more)

### Community 46 - "session_utils.py"
Cohesion: 0.08
Nodes (49): NowProvider, SleepProvider, datetime, Intraday scanning and position-management loop., Continue scanning for new entries and manage any open positions., run_intraday(), datetime, Market open entry-scanning loop. (+41 more)

### Community 47 - "candles_with_levels"
Cohesion: 0.22
Nodes (17): candles_with_levels(), capital_gauges(), _focus_price(), forecast_rr_scatter(), forecast_sensitivity(), _labeled_trade_level_ids(), _level_price(), mark_forecast_position() (+9 more)

### Community 48 - "Levels Log"
Cohesion: 0.02
Nodes (114): Daily Levels (2026-04-27T22:02:49), Daily Levels (2026-04-27T22:03:01), Daily Levels (2026-04-27T22:04:21), Daily Levels (2026-04-27T22:12:41), Daily Levels (2026-04-27T22:36:13), Daily Levels (2026-04-27T22:45:49), Daily Levels (2026-04-27T22:47:57), Daily Levels (2026-04-27T22:53:40) (+106 more)

### Community 49 - "_write_env_setting"
Cohesion: 0.23
Nodes (9): api_inefficiency_reclaim_settings(), api_update_inefficiency_reclaim_settings(), _parse_irs_min_daily_history_rows(), _parse_irs_minimum_display_score(), Atomically update one allowlisted environment setting., _set_irs_min_daily_history_rows(), _set_irs_minimum_display_score(), _write_env_setting() (+1 more)

### Community 50 - "IBKR Gerchik Bot"
Cohesion: 0.07
Nodes (26): Configuration, Current Limitations, Dashboard, Data And Jobs, Execution And Replay, Hard Filters, Inefficiency Reclaim Strategy, State And Audit (+18 more)

### Community 51 - "eod.py"
Cohesion: 0.13
Nodes (23): append_markdown_log(), Append a timestamped Markdown section to a log file., _build_summary(), _build_ticker_section(), _build_workflow_fallback_summary(), _format_reason_lines(), _load_today_job_blockers(), _load_today_workflow_snapshot() (+15 more)

### Community 52 - "StrategyCandidate"
Cohesion: 0.15
Nodes (18): _historical_analysis_candidate(), Remove live-only risk gates from an anchor-date analysis result., ConfirmationEvent, ConfirmationType, OrderPlan, Enum, str, RejectionReason (+10 more)

### Community 54 - "_irs_schedule_status"
Cohesion: 0.14
Nodes (20): api_services(), _irs_schedule_status(), _iso_age_seconds(), _latest_job_log_status(), _lock_status(), _market_collector_window_status(), _next_manifest_run(), _pid_alive() (+12 more)

### Community 55 - "decision_log.py"
Cohesion: 0.22
Nodes (14): _acquire_daily_decisions_lock(), _apply_to_decisions(), _build_attempt(), _decision_rank(), _load_daily_decisions(), _lock_is_stale(), persist(), persist_decision() (+6 more)

### Community 56 - "BrokerStub"
Cohesion: 0.07
Nodes (17): _bot_may_manage_position_exit(), job_loop_lock(), _lock_is_stale(), manage_positions(), protective_stops_ok(), Path, Manage open positions, exits, and stop hygiene., Prevent overlapping session loops across scheduled invocations. (+9 more)

### Community 57 - "MarketDataService"
Cohesion: 0.06
Nodes (31): main(), One-off backfill of OHLCV bars for the dashboard. Connects to TWS / IB Gateway…, Exception, ensure_irs_history(), history_specs(), IRSHistorySpec, _mark_skipped_remaining(), _paced_get_bars() (+23 more)

### Community 58 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 59 - "_daily_live_frame"
Cohesion: 0.22
Nodes (13): api_watchlist(), _bmsb_scan_symbol(), _bmsb_strategy2_scan_symbol(), _cross_over(), _cross_under(), _daily_live_frame(), _gaussian_alpha(), _gaussian_filter() (+5 more)

### Community 60 - "001_inefficiency_reclaim_up.sql"
Cohesion: 0.26
Nodes (12): fills, inefficiency_zones, news_checks, order_plans, orders, risk_snapshots, scanner_runs, schema_migrations (+4 more)

### Community 61 - "InefficiencyReclaimSettings"
Cohesion: 0.47
Nodes (3): InefficiencyReclaimSettings, Validated environment-facing settings for the isolated IRS subsystem., Build the pure strategy config without making that module read env.

### Community 62 - "InefficiencyReclaimStore"
Cohesion: 0.19
Nodes (8): Connection, InefficiencyReclaimStore, _json(), _json_default(), Any, datetime, Path, ValueError

### Community 64 - "NewsService"
Cohesion: 0.19
Nodes (4): News-driven trade blocking logic., NewsService, News API access layer., Fetch symbol, macro, and earnings context from NewsAPI.ai / Event Registry.

### Community 65 - "SlackAlerter"
Cohesion: 0.15
Nodes (4): Path, Send alerts to Slack with graceful degradation when disabled., SlackAlerter, SlackHeartbeatTests

### Community 66 - "Level"
Cohesion: 0.09
Nodes (24): detect_breakout(), _level_zone(), Detect a Gerchik-style two-step confirmed breakout on intraday bars., apply_strength_scores(), _fallback_score(), filter_strong_levels(), Level strength scoring helpers., Return the precomputed strength score, falling back to the legacy field. (+16 more)

### Community 67 - "collect_watchlist_intraday_bars"
Cohesion: 0.19
Nodes (11): _collector_session_bounds(), _collector_session_is_open(), _next_collector_open(), datetime, Independent intraday bar collector for dashboard continuity., Return the intraday collection window for the date represented by ``now``., Collect bars without account synchronization, signals, or orders., run_market_data_collector() (+3 more)

### Community 68 - "crypto/order_manager.py"
Cohesion: 0.29
Nodes (8): CryptoOrderManager, CryptoOrderRequest, OKX crypto order planning and safe simulated execution. Crypto orders…, Expose the shared stock risk settings used for crypto sizing., Use a conservative generic precision until per-instrument lot sizes are wired., _reward_risk(), _round_crypto_quantity(), stock_risk_settings()

### Community 69 - "broker_reconcile.py"
Cohesion: 0.19
Nodes (19): _execution_closed_record(), _execution_matches_request(), _now(), _order_ids(), _parse_dt(), _place_requests(), Any, datetime (+11 more)

### Community 70 - "breakout.py"
Cohesion: 0.26
Nodes (19): round_number_guard(), _acts_as_resistance(), _acts_as_support(), _atr_used(), _bar_index(), _breakout_is_overextended(), _build_long_signal(), _build_short_signal() (+11 more)

### Community 71 - "Weekly Log"
Cohesion: 0.11
Nodes (18): Weekly Log, Weekly Metrics (2026-04-23T22:20:31), Weekly Metrics (2026-04-23T23:20:28), Weekly Review (2026-04-23T23:46:43), Weekly Review (2026-05-01T16:25:02), Weekly Review (2026-05-13T22:21:19), Weekly Review (2026-05-15T16:25:02), Weekly Review (2026-05-22T16:25:02) (+10 more)

### Community 73 - "detect_false_breakout_one_bar"
Cohesion: 0.10
Nodes (17): DecisionSink, Record a decision into ``sink`` if one is provided; otherwise do nothing., record(), _complex_score(), detect_false_breakout_complex(), DataFrame, DecisionSink, Series (+9 more)

### Community 74 - "execute_requests.py"
Cohesion: 0.29
Nodes (17): _effective_dry_run(), _f(), _market_order_tif(), _position_symbol_key(), _process_close(), _process_one(), process_pending_once(), _process_place() (+9 more)

### Community 75 - "reward_risk_ratio"
Cohesion: 0.24
Nodes (7): calculate_stop_loss(), calculate_take_profit(), reward_risk_ratio(), Tests for stop and reward/risk logic., StopTakeProfitTests, Tests for technical stop and target handling., StopTargetTests

### Community 76 - "should_trigger_kill_switch"
Cohesion: 0.31
Nodes (6): _equity_symbols(), Trading kill switch conditions., Block all trading when safety conditions are breached., should_trigger_kill_switch(), KillSwitchTests, Tests for kill switch behavior.

### Community 77 - "_FakeEvent"
Cohesion: 0.19
Nodes (3): _FakeEvent, _FakeIB, IBKRLoggingTests

### Community 78 - "jobs/inefficiency_reclaim.py"
Cohesion: 0.27
Nodes (9): active_confirmation_symbols(), notify_inefficiency_reclaim_scan(), Any, datetime, Connected IRS data-hydration and analysis workflow., Return non-expired symbols that need the 15-minute confirmation scan., run_inefficiency_reclaim_job(), _summary_value() (+1 more)

### Community 80 - "RiskManager"
Cohesion: 0.27
Nodes (4): Portfolio risk tracking., Track realized loss and open portfolio risk., RiskManager, RiskSnapshot

### Community 81 - "third_touch.py"
Cohesion: 0.33
Nodes (6): detect_third_touch(), DataFrame, Series, Third touch setup detection., Detect the third qualified interaction with a level., _touch_indices()

### Community 82 - "_connect_broker_with_startup_retry"
Cohesion: 0.07
Nodes (7): _connect_broker_with_startup_retry(), Create and connect an IBKR client, waiting through temporary startup contention., _AlwaysBusyBrokerStub, _FlakyBrokerStub, IBKRStartupRetryTests, _BrokerStub, _BrokerStub

### Community 83 - "SymbolHandlingTests"
Cohesion: 0.24
Nodes (3): normalize_symbol(), Settings, SymbolHandlingTests

### Community 84 - "build_task_scheduler_command"
Cohesion: 0.27
Nodes (8): build_all_task_scheduler_commands(), build_task_scheduler_command(), get_recommended_task_names(), Path, Helpers for wiring the bot into Windows Task Scheduler., Return a Task Scheduler command line for a specific job., Return concrete Task Scheduler commands for every job., Map jobs to recommended Task Scheduler task names.

### Community 87 - "setup_logging"
Cohesion: 0.33
Nodes (5): Logger, Rotating file handler that tolerates Windows multi-process log locks., Configure application logging once., SafeRotatingFileHandler, setup_logging()

### Community 88 - "add_bmsb_signals"
Cohesion: 0.40
Nodes (5): add_bmsb_signals(), main(), DataFrame, BMSB Strategy 1: Bull Market Support Band conversion. Long-only strategy on…, Add daily signals from completed weekly BMSB values.

### Community 89 - "maybe_commit_and_push"
Cohesion: 0.47
Nodes (5): maybe_commit_and_push(), Path, Optional git commit/push helpers for workflow jobs., Commit and push workflow outputs when AUTO_GIT_PUSH is enabled., _run_git()

### Community 90 - "IBKRClient"
Cohesion: 0.08
Nodes (17): IBKRClient, Any, Connect to IBKR TWS or IB Gateway with retry logic., Select live/frozen/delayed market data for this IBKR connection., Reconnect if the live connection is not healthy., Return executions currently available from IBKR as plain records.…, Request a snapshot and return bid/ask/last/close safely., Fetch historical bars and return a DataFrame. (+9 more)

### Community 91 - "OrderManager"
Cohesion: 0.23
Nodes (4): OrderManager, Place a human-initiated (dashboard) order. This bypasses the *autonomous*…, Execute validated trades and record the resulting actions., ManualOrderTests

### Community 94 - "DESIGN.md"
Cohesion: 0.15
Nodes (12): Brand & Style, Buttons, Cards, Colors, Components, Data Visualization, Elevation & Depth, Input Fields (+4 more)

### Community 95 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 96 - "disable_dashboard_cache"
Cohesion: 0.67
Nodes (3): disable_dashboard_cache(), middleware, Request

### Community 117 - "false_breakout.py"
Cohesion: 0.40
Nodes (4): detect_false_breakout(), DataFrame, False breakout detection — base module, intentionally disabled. This module…, Detect a level breach followed by immediate rejection. .. deprecated:: This…

### Community 118 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 119 - "Trading Operations Dashboard"
Cohesion: 0.33
Nodes (5): Candle data, Install, Run, Trading Operations Dashboard, Workspaces

### Community 120 - "Trading Strategy"
Cohesion: 0.33
Nodes (5): Buy-Side Gate, Core Rules, Intraday Rules, Review Process, Trading Strategy

### Community 122 - "CODING AGENTS: READ THIS FIRST"
Cohesion: 0.40
Nodes (4): About the design files, Bundle contents, CODING AGENTS: READ THIS FIRST, What you should do — IMPORTANT

### Community 124 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 125 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 126 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

## Knowledge Gaps
- **605 isolated node(s):** `schema_migrations`, `scanner_runs`, `risk_snapshots`, `news_checks`, `BrokerConfig` (+600 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **30 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Level` connect `Level` to `src/main.py`, `forecast.py`, `false_breakout_one_bar.py`, `rebound.py`, `breakout.py`, `premarket.py`, `detect_false_breakout_one_bar`, `false_breakout_continuation.py`, `levels.py`, `session_utils.py`, `TradeSignal`, `decision_log.py`, `_make_level`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Why does `TradeSignal` connect `TradeSignal` to `src/main.py`, `Level`, `false_breakout_one_bar.py`, `execution/order_manager.py`, `breakout.py`, `rebound.py`, `OrderResult`, `detect_false_breakout_one_bar`, `execute_requests.py`, `false_breakout_continuation.py`, `session_utils.py`, `test_execute_requests.py`, `_BrokerStub`, `_MarketDataServiceStub`, `OrderRequestQueueTests`, `IBKRClient`, `OrderManager`, `_NewsRiskFilterStub`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Why does `IBKRClient` connect `IBKRClient` to `NewsService`, `src/main.py`, `OrderRequestQueueTests`, `execution/order_manager.py`, `broker_reconcile.py`, `NewsRiskFilter`, `_FakeEvent`, `session_utils.py`, `NasdaqDataClient`, `_connect_broker_with_startup_retry`, `test_execute_requests.py`, `BrokerStub`, `MarketDataService`, `OrderManager`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `Level` (e.g. with `ForecastScenario` and `ZoneContext`) actually correct?**
  _`Level` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `TradeSignal` (e.g. with `OrderManager` and `ZoneContext`) actually correct?**
  _`TradeSignal` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `IBKRClient` (e.g. with `MarketDataService` and `NewsService`) actually correct?**
  _`IBKRClient` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `schema_migrations`, `scanner_runs`, `risk_snapshots` to the rest of the system?**
  _605 weakly-connected nodes found - possible documentation gaps or missing edges._