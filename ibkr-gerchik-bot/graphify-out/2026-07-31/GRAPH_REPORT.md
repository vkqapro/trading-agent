# Graph Report - ibkr-gerchik-bot  (2026-07-31)

## Corpus Check
- 206 files · ~8,359,252 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3015 nodes · 7383 edges · 127 communities (97 shown, 30 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 197 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9bdfa9d4`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- level_strength.py
- src/main.py
- InefficiencyReclaimStore
- forecast.py
- false_breakout_one_bar.py
- order_requests.py
- SlackAlerter
- IRSConfig
- append_workflow_snapshot
- project/support.js
- Level
- Trading Bot Dashboard/support.js
- market_screener.py
- strategy/inefficiency_reclaim.py
- data_access.py
- NasdaqDataClient
- load_workflow_context
- order_journal.py
- server.py
- test_inefficiency_reclaim_backtest.py
- components.py
- inefficiency_reclaim_store.py
- TradeSignal
- src/config.py
- _BrokerStub
- _NewsRiskFilterStub
- validator.py
- timedelta
- test_p0_fixes.py
- crypto/tradingview_webhook.py
- scanners/inefficiency_reclaim.py
- dashboard/app.py
- levels_export.py
- project/ibkr-gerchik-bot/dashboard/app.py
- Trade Log
- rebound.py
- MarketDataService
- Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py
- premarket.py
- false_breakout_continuation.py
- test_irs_scheduler.py
- SlackCommandProcessor
- stocks/tradingview_webhook.py
- load_stock_symbols
- session_utils.py
- candles_with_levels
- Levels Log
- _write_env_setting
- IBKR Gerchik Bot
- forex/tradingview_webhook.py
- IBKRClient
- _irs_schedule_status
- decision_log.py
- jobs/inefficiency_reclaim.py
- DataFrame
- What You Must Do When Invoked
- _daily_live_frame
- 001_inefficiency_reclaim_up.sql
- test_execute_requests.py
- make_bar
- save_bars
- NewsService
- .symbol_security_type
- breakout.py
- collect_watchlist_intraday_bars
- crypto/order_manager.py
- broker_reconcile.py
- _FakeEvent
- Weekly Log
- DashboardDataAccessTests
- metric_grid
- _MarketDataServiceStub
- Direction
- should_trigger_kill_switch
- _BrokerStub
- third_touch.py
- _BrokerStub
- _BrokerStub
- _connect_broker_with_startup_retry
- add_bmsb_signals
- .get_market_price
- symbols.py
- OKXClient
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
- OrderResult
- graphify reference: query, path, explain
- Trading Operations Dashboard
- Trading Strategy
- analysis.py
- CODING AGENTS: READ THIS FIRST
- run_crypto_analysis
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- Weekly Review
- AGENTS.md
- extraction-spec.md
- irs/README.md
- crypto/config.py
- ManualOrderTests
- IBKRDependencyError
- OrderRequestQueueTests

## God Nodes (most connected - your core abstractions)
1. `Trade Log` - 349 edges
2. `Level` - 132 edges
3. `Levels Log` - 109 edges
4. `TradeSignal` - 75 edges
5. `IBKRClient` - 71 edges
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

## Communities (127 total, 30 thin omitted)

### Community 0 - "level_strength.py"
Cohesion: 0.27
Nodes (8): apply_strength_scores(), _fallback_score(), filter_strong_levels(), Level strength scoring helpers., Return the precomputed strength score, falling back to the legacy field., score_level(), LevelStrengthTests, Tests for level strength scoring.

### Community 1 - "src/main.py"
Cohesion: 0.06
Nodes (42): Namespace, maybe_commit_and_push(), Path, Optional git commit/push helpers for workflow jobs., Commit and push workflow outputs when AUTO_GIT_PUSH is enabled., _run_git(), Poll the request queue and execute pending requests until the deadline., run_execute_requests() (+34 more)

### Community 2 - "InefficiencyReclaimStore"
Cohesion: 0.08
Nodes (24): Connection, Protocol, InefficiencyReclaimPaperExecutor, IRSBroker, IRSExecutionPolicy, IRSExecutionResult, IRSExpiryCancellationResult, IRSReconnectResult (+16 more)

### Community 3 - "forecast.py"
Cohesion: 0.13
Nodes (22): _bars_freshness(), calculate_open_risk(), _candidate(), ForecastScenario, _number(), projected_level_candidates(), Any, DataFrame (+14 more)

### Community 4 - "false_breakout_one_bar.py"
Cohesion: 0.11
Nodes (50): PatternName, SignalSide, body_size(), _complex_score(), detect_false_breakout(), DataFrame, Series, Gerchik-style complex 3+ bar false breakout detection helpers with… (+42 more)

### Community 5 - "order_requests.py"
Cohesion: 0.11
Nodes (39): _acquire_lock(), claim_pending(), list_requests(), _load(), _mutate(), _now(), pending_requests(), Any (+31 more)

### Community 6 - "SlackAlerter"
Cohesion: 0.05
Nodes (26): Path, Slack webhook notifications., Send alerts to Slack with graceful degradation when disabled., SlackAlerter, Interactive Brokers connectivity built on top of ib_insync., OrderManager, Order execution orchestration., Place a human-initiated (dashboard) order. This bypasses the *autonomous*… (+18 more)

### Community 7 - "IRSConfig"
Cohesion: 0.20
Nodes (5): IRSConfig, make_zone(), RetraceAndConfirmationTests, datetime, SavedBarScannerTests

### Community 8 - "append_workflow_snapshot"
Cohesion: 0.08
Nodes (35): append_markdown_log(), ensure_directories(), Create runtime and memory directories expected by the application., Append a timestamped Markdown section to a log file., _build_summary(), _build_ticker_section(), _build_workflow_fallback_summary(), _format_reason_lines() (+27 more)

### Community 9 - "project/support.js"
Cohesion: 0.08
Nodes (42): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), createExternalModules(), createHelmetManager(), createPseudoSheet() (+34 more)

### Community 10 - "Level"
Cohesion: 0.07
Nodes (51): analyze_symbol(), detect_breakout(), Detect a Gerchik-style two-step confirmed breakout on intraday bars., _atr_distances(), _build_zone(), calculate_atr(), _clean_gap_atr_pct_between_levels(), _clean_gap_between_levels() (+43 more)

### Community 11 - "Trading Bot Dashboard/support.js"
Cohesion: 0.08
Nodes (42): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), createExternalModules(), createHelmetManager(), createPseudoSheet() (+34 more)

### Community 12 - "market_screener.py"
Cohesion: 0.13
Nodes (36): _add_metrics(), _apply_anchor_date(), _approach_profile(), _bar_position(), _business_day_offset(), _date_value(), _detect_signals(), _event_dates() (+28 more)

### Community 13 - "strategy/inefficiency_reclaim.py"
Cohesion: 0.17
Nodes (46): _diagnose_no_candidate(), Bar, _bounded(), build_explanation(), build_inefficiency_zone(), build_low_overlap_zone(), build_strict_gap_zone(), calculate_entry_trigger() (+38 more)

### Community 14 - "data_access.py"
Cohesion: 0.11
Nodes (43): bars_index(), blocked_news_summary(), crypto_bars_index(), _filter_watchlist_symbols(), freshness(), get_crypto_bars(), latest_trade_log_sections(), _latest_workflow_watchlist_for_symbols() (+35 more)

### Community 15 - "NasdaqDataClient"
Cohesion: 0.10
Nodes (21): date, NasdaqDataConfig, Optional Nasdaq market-data fallback for historical candles., _cloud_precision(), _cloud_range(), _daily_cloud_range(), _duration_days(), _duration_start_date() (+13 more)

### Community 16 - "load_workflow_context"
Cohesion: 0.26
Nodes (10): Calculate weekly win rate, expectancy, and strategy-level review., run_weekly(), load_workflow_context(), Path, Helpers for reading strategy and recent workflow memory., Return the tail of a text file, or an empty string if it does not exist., Return the full contents of a text file, or an empty string if missing., Load the strategy doc plus recent research/trade context for workflow jobs. (+2 more)

### Community 17 - "order_journal.py"
Cohesion: 0.10
Nodes (43): _base_row(), _closed_positions_by_symbol(), _f(), _fill_price_from_result(), _infer_bracket_exit_from_bars(), load_order_journal(), load_reviews(), _merge_reviews() (+35 more)

### Community 18 - "server.py"
Cohesion: 0.11
Nodes (44): api_bars(), api_crypto(), api_crypto_add(), api_crypto_analyze(), api_crypto_bars(), api_crypto_symbol(), api_crypto_tradingview_state(), api_dashboard() (+36 more)

### Community 19 - "test_inefficiency_reclaim_backtest.py"
Cohesion: 0.16
Nodes (23): BacktestCosts, BacktestTrade, _bar_contains_time(), build_backtest_report(), _entry_fill(), _exit_touches(), _maximum_drawdown(), Any (+15 more)

### Community 21 - "components.py"
Cohesion: 0.11
Nodes (38): render_header(), render_navigation(), bias_card(), blocked_news_panel(), _clean(), _esc(), feature_card(), hero() (+30 more)

### Community 22 - "inefficiency_reclaim_store.py"
Cohesion: 0.12
Nodes (17): Resize both protective children to exactly the filled quantity., ImmutableZoneError, SQLite audit store for IRS zones, setups, transitions, and execution records., Raised when a scan attempts to move persisted zone boundaries., SetupState, advance_setup_state(), can_transition(), InvalidStateTransition (+9 more)

### Community 23 - "TradeSignal"
Cohesion: 0.08
Nodes (38): build_partial_targets(), DecisionSink, Record a decision into ``sink`` if one is provided; otherwise do nothing., record(), detect_false_breakout_complex(), DecisionSink, Router-compatible wrapper returning a TradeSignal for complex false breakouts., detect_false_breakout_one_bar() (+30 more)

### Community 24 - "src/config.py"
Cohesion: 0.08
Nodes (29): Logger, BrokerConfig, _csv_env(), _csv_env_file_or_fallback(), _csv_env_with_fallback(), _dedupe_preserve_order(), _env_bool(), _env_float() (+21 more)

### Community 27 - "validator.py"
Cohesion: 0.13
Nodes (14): calculate_position_size(), position_value_ok(), Position sizing logic., Size a position so the loss to stop equals the allowed risk budget., _optional_float(), Universal trade validation rules., Explainable validator wrapper that preserves deterministic reason tracking., Validate a trade candidate against hard trading rules. (+6 more)

### Community 28 - "timedelta"
Cohesion: 0.10
Nodes (7): evaluate_hard_gates(), Decimal, Quote, StrategyContext, PaperExecutorTests, PlanningAndGateTests, timedelta

### Community 29 - "test_p0_fixes.py"
Cohesion: 0.09
Nodes (14): detect_false_breakout(), DataFrame, False breakout detection — base module, intentionally disabled. This module…, Detect a level breach followed by immediate rejection. .. deprecated:: This…, _make_level(), DataFrame, Tests covering P0 fixes for Gerchik-style trading logic. P0-A:…, Verify that breakout and rebound honour the configured RR minimum. We patch… (+6 more)

### Community 30 - "crypto/tradingview_webhook.py"
Cohesion: 0.21
Nodes (25): BackgroundTasks, api_crypto_tradingview_webhook(), _append_execution(), _as_float(), enqueue_tradingview_webhook(), _execution_from_signal(), _extract_okx_order_id(), _find_execution() (+17 more)

### Community 31 - "scanners/inefficiency_reclaim.py"
Cohesion: 0.18
Nodes (25): BarLoader, QuoteLoader, _account_state(), candidate_row(), classify_market_regime(), classify_market_trend_direction(), data_quality_diagnostics(), frame_to_bars() (+17 more)

### Community 32 - "dashboard/app.py"
Cohesion: 0.21
Nodes (24): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+16 more)

### Community 33 - "levels_export.py"
Cohesion: 0.16
Nodes (19): _center(), _clean_gap_annotations(), _coerce_float(), export_premarket_levels_report(), _gap_to_upper_level(), _level_key(), _level_price(), _optimization_reason() (+11 more)

### Community 34 - "project/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.17
Nodes (29): fmt(), load_tracked_positions(), _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame() (+21 more)

### Community 35 - "Trade Log"
Cohesion: 0.01
Nodes (349): End Of Day (2026-04-27T22:05:40), End Of Day (2026-04-27T22:06:16), End Of Day (2026-04-27T22:13:29), End Of Day (2026-04-28T16:10:04), End Of Day (2026-04-29T16:10:04), End Of Day (2026-04-30T16:10:04), End Of Day (2026-04-30T16:11:49), End Of Day (2026-04-30T16:39:25) (+341 more)

### Community 36 - "rebound.py"
Cohesion: 0.12
Nodes (38): average_range(), close_above_level(), close_below_level(), close_location(), full_range(), has_compression(), is_abnormal_candle(), is_bearish() (+30 more)

### Community 37 - "MarketDataService"
Cohesion: 0.19
Nodes (7): MarketDataService, DataFrame, datetime, Fetch a timeframe for an incremental strategy cache., Provide reusable market data retrieval wrappers., Allow delayed data when the account lacks a live subscription., Refresh the broker session and restore the collector data mode.

### Community 38 - "Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.22
Nodes (24): panel_header(), _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity() (+16 more)

### Community 40 - "premarket.py"
Cohesion: 0.07
Nodes (35): _matching_headlines(), NewsRiskFilter, News-driven trade blocking logic., Evaluate symbol-specific and macro news risk before trading., News API access layer., _duration_to_trading_rows(), _level_center(), _level_daily_window() (+27 more)

### Community 41 - "false_breakout_continuation.py"
Cohesion: 0.17
Nodes (17): _context(), _continuation_score(), _continuation_stop(), _current_session_uptrend_from_level(), detect_false_breakout_continuation(), _is_prior_false_breakdown(), _normalize_bars(), DataFrame (+9 more)

### Community 42 - "test_irs_scheduler.py"
Cohesion: 0.15
Nodes (13): _next_manifest_run(), build_all_task_scheduler_commands(), build_task_scheduler_command(), get_recommended_task_names(), Path, Helpers for wiring the bot into Windows Task Scheduler., Return a Task Scheduler command line for a specific job., Return concrete Task Scheduler commands for every job. (+5 more)

### Community 43 - "SlackCommandProcessor"
Cohesion: 0.13
Nodes (4): Path, Poll a Slack channel for whitelisted bot commands., SlackCommandProcessor, SlackCommandTests

### Community 44 - "stocks/tradingview_webhook.py"
Cohesion: 0.27
Nodes (17): _append_execution(), _as_float(), _as_int(), _expected_bot_id(), _first_float(), handle_stock_tradingview_webhook(), load_stock_tradingview_state(), _normalize_stock_symbol() (+9 more)

### Community 45 - "load_stock_symbols"
Cohesion: 0.14
Nodes (22): api_strategy(), api_watchlist_add(), api_watchlist_bulk_add(), api_watchlist_remove(), _onboard_client_id(), _parse_stock_symbol_upload(), _queue_bulk_stock_onboarding(), _client_id() (+14 more)

### Community 46 - "session_utils.py"
Cohesion: 0.04
Nodes (84): NowProvider, SleepProvider, datetime, Intraday scanning and position-management loop., Continue scanning for new entries and manage any open positions., run_intraday(), datetime, Market open entry-scanning loop. (+76 more)

### Community 47 - "candles_with_levels"
Cohesion: 0.14
Nodes (18): candles_with_levels(), capital_gauges(), _focus_price(), forecast_rr_scatter(), forecast_sensitivity(), _labeled_trade_level_ids(), _level_price(), mark_forecast_position() (+10 more)

### Community 48 - "Levels Log"
Cohesion: 0.02
Nodes (109): Daily Levels (2026-04-27T22:02:49), Daily Levels (2026-04-27T22:03:01), Daily Levels (2026-04-27T22:04:21), Daily Levels (2026-04-27T22:12:41), Daily Levels (2026-04-27T22:36:13), Daily Levels (2026-04-27T22:45:49), Daily Levels (2026-04-27T22:47:57), Daily Levels (2026-04-27T22:53:40) (+101 more)

### Community 49 - "_write_env_setting"
Cohesion: 0.23
Nodes (9): api_inefficiency_reclaim_settings(), api_update_inefficiency_reclaim_settings(), _parse_irs_min_daily_history_rows(), _parse_irs_minimum_display_score(), Atomically update one allowlisted environment setting., _set_irs_min_daily_history_rows(), _set_irs_minimum_display_score(), _write_env_setting() (+1 more)

### Community 50 - "IBKR Gerchik Bot"
Cohesion: 0.07
Nodes (26): Configuration, Current Limitations, Dashboard, Data And Jobs, Execution And Replay, Hard Filters, Inefficiency Reclaim Strategy, State And Audit (+18 more)

### Community 51 - "forex/tradingview_webhook.py"
Cohesion: 0.25
Nodes (17): fx_pair_components(), _append_execution(), _as_float(), _as_int(), _expected_bot_id(), ForexTradingViewWebhookError, handle_forex_tradingview_webhook(), load_forex_tradingview_state() (+9 more)

### Community 52 - "IBKRClient"
Cohesion: 0.11
Nodes (12): IBKRClient, Any, Connect to IBKR TWS or IB Gateway with retry logic., Select live/frozen/delayed market data for this IBKR connection., Reconnect if the live connection is not healthy., Fetch historical bars and return a DataFrame., Round a price to a valid US-equity tick (1c at/above $1, else 0.0001). TWS…, Return the broker's reject/cancel message from a trade's log, if any. (+4 more)

### Community 54 - "_irs_schedule_status"
Cohesion: 0.20
Nodes (17): api_services(), _crypto_event_date(), _irs_schedule_status(), _iso_age_seconds(), _latest_job_log_status(), _lock_status(), _market_collector_window_status(), _market_session() (+9 more)

### Community 55 - "decision_log.py"
Cohesion: 0.19
Nodes (16): _acquire_daily_decisions_lock(), _apply_to_decisions(), _build_attempt(), _decision_rank(), load(), _load_daily_decisions(), _lock_is_stale(), persist() (+8 more)

### Community 56 - "jobs/inefficiency_reclaim.py"
Cohesion: 0.09
Nodes (13): active_confirmation_symbols(), notify_inefficiency_reclaim_scan(), Any, datetime, Connected IRS data-hydration and analysis workflow., Return non-expired symbols that need the 15-minute confirmation scan., run_inefficiency_reclaim_job(), _summary_value() (+5 more)

### Community 57 - "DataFrame"
Cohesion: 0.18
Nodes (6): _BrokerStub, _DurationBrokerStub, MarketDataServiceTests, _NasdaqProviderStub, DataFrame, _QuoteBrokerStub

### Community 58 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 59 - "_daily_live_frame"
Cohesion: 0.22
Nodes (13): api_watchlist(), _bmsb_scan_symbol(), _bmsb_strategy2_scan_symbol(), _cross_over(), _cross_under(), _daily_live_frame(), _gaussian_alpha(), _gaussian_filter() (+5 more)

### Community 60 - "001_inefficiency_reclaim_up.sql"
Cohesion: 0.26
Nodes (12): fills, inefficiency_zones, news_checks, order_plans, orders, risk_snapshots, scanner_runs, schema_migrations (+4 more)

### Community 61 - "test_execute_requests.py"
Cohesion: 0.15
Nodes (8): ExecuteWorkerTests, FakeBroker, FakeOrderManager, _paper(), Tests for the dashboard order-request queue and the execution worker., Minimal OrderResult-like object the fake broker returns., _Recorder, TickRoundingTests

### Community 62 - "make_bar"
Cohesion: 0.27
Nodes (3): DisplacementAndZoneTests, make_bar(), RobustATRTests

### Community 63 - "save_bars"
Cohesion: 0.05
Nodes (61): main(), One-off backfill of OHLCV bars for the dashboard. Connects to TWS / IB Gateway…, Exception, main(), Path, _symbol_from_intraday_file(), bar_metadata(), bar_path() (+53 more)

### Community 65 - ".symbol_security_type"
Cohesion: 0.16
Nodes (6): InefficiencyReclaimSettings, normalize_symbol(), Validated environment-facing settings for the isolated IRS subsystem., Build the pure strategy config without making that module read env., Settings, SymbolHandlingTests

### Community 66 - "breakout.py"
Cohesion: 0.13
Nodes (27): calculate_stop_loss(), round_number_guard(), calculate_take_profit(), reward_risk_ratio(), _acts_as_resistance(), _acts_as_support(), _atr_used(), _bar_index() (+19 more)

### Community 67 - "collect_watchlist_intraday_bars"
Cohesion: 0.19
Nodes (11): _collector_session_bounds(), _collector_session_is_open(), _next_collector_open(), datetime, Independent intraday bar collector for dashboard continuity., Return the intraday collection window for the date represented by ``now``., Collect bars without account synchronization, signals, or orders., run_market_data_collector() (+3 more)

### Community 68 - "crypto/order_manager.py"
Cohesion: 0.29
Nodes (8): CryptoOrderManager, CryptoOrderRequest, OKX crypto order planning and safe simulated execution. Crypto orders…, Expose the shared stock risk settings used for crypto sizing., Use a conservative generic precision until per-instrument lot sizes are wired., _reward_risk(), _round_crypto_quantity(), stock_risk_settings()

### Community 69 - "broker_reconcile.py"
Cohesion: 0.40
Nodes (10): _now(), Any, Path, Best-effort broker reconciliation for the local order journal. The journal is…, Sync live IBKR open stop/limit prices into local tracked positions. Returns a…, _read_json(), reconcile_ibkr_open_orders(), _record_closed_positions() (+2 more)

### Community 70 - "_FakeEvent"
Cohesion: 0.19
Nodes (3): _FakeEvent, _FakeIB, IBKRLoggingTests

### Community 71 - "Weekly Log"
Cohesion: 0.11
Nodes (17): Weekly Log, Weekly Metrics (2026-04-23T22:20:31), Weekly Metrics (2026-04-23T23:20:28), Weekly Review (2026-04-23T23:46:43), Weekly Review (2026-05-01T16:25:02), Weekly Review (2026-05-13T22:21:19), Weekly Review (2026-05-15T16:25:02), Weekly Review (2026-05-22T16:25:02) (+9 more)

### Community 73 - "metric_grid"
Cohesion: 0.38
Nodes (12): render_intraday(), render_reports(), mark_levels(), metric_grid(), Render a responsive grid of glass metric cards., all_decision_attempts(), decisions_for_symbol(), decisions_to_frame() (+4 more)

### Community 75 - "Direction"
Cohesion: 0.15
Nodes (20): _historical_analysis_candidate(), Remove live-only risk gates from an anchor-date analysis result., _add_rth_hours(), build_signal_id(), ConfirmationType, Direction, _json_value(), Any (+12 more)

### Community 76 - "should_trigger_kill_switch"
Cohesion: 0.31
Nodes (6): _equity_symbols(), Trading kill switch conditions., Block all trading when safety conditions are breached., should_trigger_kill_switch(), KillSwitchTests, Tests for kill switch behavior.

### Community 81 - "third_touch.py"
Cohesion: 0.33
Nodes (6): detect_third_touch(), DataFrame, Series, Third touch setup detection., Detect the third qualified interaction with a level., _touch_indices()

### Community 87 - "_connect_broker_with_startup_retry"
Cohesion: 0.16
Nodes (5): _connect_broker_with_startup_retry(), Create and connect an IBKR client, waiting through temporary startup contention., _AlwaysBusyBrokerStub, _FlakyBrokerStub, IBKRStartupRetryTests

### Community 88 - "add_bmsb_signals"
Cohesion: 0.40
Nodes (5): add_bmsb_signals(), main(), DataFrame, BMSB Strategy 1: Bull Market Support Band conversion. Long-only strategy on…, Add daily signals from completed weekly BMSB values.

### Community 90 - ".get_market_price"
Cohesion: 0.40
Nodes (3): Request a snapshot and return bid/ask/last/close safely., Normalize broker quote/order prices so IBKR unset values become zeros., _safe_market_price()

### Community 92 - "symbols.py"
Cohesion: 0.38
Nodes (6): _dedupe(), default_instrument_type(), load_crypto_symbol_inputs(), Path, Normalize TradingView/OKX crypto symbols into OKX instrument ids., _split_raw_text()

### Community 93 - "OKXClient"
Cohesion: 0.24
Nodes (6): RuntimeError, OKXClient, OKXInstrument, Any, DataFrame, Small OKX REST client for public candles and future private order work.

### Community 94 - "DESIGN.md"
Cohesion: 0.15
Nodes (12): Brand & Style, Buttons, Cards, Colors, Components, Data Visualization, Elevation & Depth, Input Fields (+4 more)

### Community 95 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 96 - "disable_dashboard_cache"
Cohesion: 0.67
Nodes (3): disable_dashboard_cache(), middleware, Request

### Community 118 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 119 - "Trading Operations Dashboard"
Cohesion: 0.33
Nodes (5): Candle data, Install, Run, Trading Operations Dashboard, Workspaces

### Community 120 - "Trading Strategy"
Cohesion: 0.33
Nodes (5): Buy-Side Gate, Core Rules, Intraday Rules, Review Process, Trading Strategy

### Community 121 - "analysis.py"
Cohesion: 0.25
Nodes (15): _quiet_shared_level_logs(), Crypto Gerchik-style analysis using the shared stock strategy logic., Keep crypto batch scans from flooding the shared stock bot log file., crypto_bar_path(), crypto_index_snapshot(), load_crypto_bars(), _normalize_frame(), DataFrame (+7 more)

### Community 122 - "CODING AGENTS: READ THIS FIRST"
Cohesion: 0.40
Nodes (4): About the design files, Bundle contents, CODING AGENTS: READ THIS FIRST, What you should do — IMPORTANT

### Community 123 - "run_crypto_analysis"
Cohesion: 0.18
Nodes (21): load_crypto_symbols(), collect_crypto_bars(), configured_crypto_symbols(), run_crypto_analysis(), validate_symbols(), _analysis_summary(), main(), CLI entry point for the separate crypto bot. (+13 more)

### Community 124 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 125 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 126 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

### Community 134 - "crypto/config.py"
Cohesion: 0.36
Nodes (7): CryptoConfig, _env_bool(), _env_csv(), _env_float(), _env_int(), _env_str(), Crypto bot configuration. API secrets should live in the local .env file only.…

### Community 136 - "IBKRDependencyError"
Cohesion: 0.40
Nodes (4): _ensure_event_loop(), IBKRDependencyError, ib_insync/eventkit expects a current event loop on newer Python versions., Raised when ib_insync is unavailable.

## Knowledge Gaps
- **571 isolated node(s):** `schema_migrations`, `scanner_runs`, `risk_snapshots`, `news_checks`, `BrokerConfig` (+566 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **30 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Level` connect `Level` to `level_strength.py`, `src/main.py`, `breakout.py`, `forecast.py`, `false_breakout_one_bar.py`, `rebound.py`, `premarket.py`, `false_breakout_continuation.py`, `session_utils.py`, `TradeSignal`, `decision_log.py`, `test_p0_fixes.py`?**
  _High betweenness centrality (0.049) - this node is a cross-community bridge._
- **Why does `InefficiencyReclaimStore` connect `InefficiencyReclaimStore` to `IRSConfig`, `Direction`, `server.py`, `inefficiency_reclaim_store.py`, `jobs/inefficiency_reclaim.py`, `timedelta`, `scanners/inefficiency_reclaim.py`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Why does `MarketDataService` connect `MarketDataService` to `src/main.py`, `collect_watchlist_intraday_bars`, `SlackAlerter`, `premarket.py`, `session_utils.py`, `NasdaqDataClient`, `IBKRClient`, `jobs/inefficiency_reclaim.py`, `DataFrame`, `save_bars`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `Level` (e.g. with `ForecastScenario` and `ZoneContext`) actually correct?**
  _`Level` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `TradeSignal` (e.g. with `OrderManager` and `ZoneContext`) actually correct?**
  _`TradeSignal` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `IBKRClient` (e.g. with `MarketDataService` and `NewsService`) actually correct?**
  _`IBKRClient` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `schema_migrations`, `scanner_runs`, `risk_snapshots` to the rest of the system?**
  _571 weakly-connected nodes found - possible documentation gaps or missing edges._