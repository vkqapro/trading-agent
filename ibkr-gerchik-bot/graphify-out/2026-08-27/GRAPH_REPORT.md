# Graph Report - ibkr-gerchik-bot  (2026-08-27)

## Corpus Check
- 214 files · ~29,104,747 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3670 nodes · 8162 edges · 125 communities (95 shown, 30 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 283 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `14a1ba59`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- premarket.py
- test_quote_check_job.py
- execution/inefficiency_reclaim.py
- forecast.py
- false_breakout_one_bar.py
- order_requests.py
- SlackAlerter
- timedelta
- src/main.py
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
- jobs/inefficiency_reclaim.py
- components.py
- InefficiencyReclaimStore
- TradeSignal
- src/config.py
- forex/tradingview_webhook.py
- stocks/tradingview_webhook.py
- validator.py
- Quote
- test_p0_fixes.py
- show_df
- scanners/inefficiency_reclaim.py
- dashboard/app.py
- levels_export.py
- project/ibkr-gerchik-bot/dashboard/app.py
- Trade Log
- rebound.py
- MarketDataServiceTests
- Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py
- make_bar
- chart_history.py
- false_breakout_continuation.py
- test_news_blocking.py
- BarStoreMergeTests
- metric_grid
- load_stock_symbols
- session_utils.py
- candles_with_levels
- Levels Log
- _write_env_setting
- IBKR Gerchik Bot
- NewsService
- Research Log
- crypto/tradingview_webhook.py
- _OrderManagerStub
- decision_log.py
- symbols.py
- What You Must Do When Invoked
- normalize_okx_instrument
- install_windows_scheduled_tasks.ps1
- OKXClient
- IBKRClient
- manual_order.py
- BrokerStub
- collect_watchlist_intraday_bars
- CryptoOrderRequest
- broker_reconcile.py
- breakout.py
- Weekly Log
- DashboardDataAccessTests
- StrategyCandidate
- _BrokerStub
- IRSBroker
- should_trigger_kill_switch
- _BrokerStub
- crypto/config.py
- _BrokerStub
- load_workflow_context
- third_touch.py
- _connect_broker_with_startup_retry
- _BrokerStub
- _MarketDataServiceStub
- _FakeEvent
- add_bmsb_signals
- run_crypto_analysis
- MarketDataService
- _irs_schedule_status
- DESIGN.md
- graphify reference: extra exports and benchmark
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
- analysis.py
- graphify reference: query, path, explain
- Trading Operations Dashboard
- Trading Strategy
- CODING AGENTS: READ THIS FIRST
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- Weekly Review
- AGENTS.md
- extraction-spec.md
- irs/README.md
- _daily_live_frame
- Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab

## God Nodes (most connected - your core abstractions)
1. `Research Log` - 422 edges
2. `Trade Log` - 422 edges
3. `Levels Log` - 126 edges
4. `Level` - 125 edges
5. `IBKRClient` - 64 edges
6. `TradeSignal` - 60 edges
7. `MarketDataService` - 56 edges
8. `SlackAlerter` - 54 edges
9. `Bar` - 49 edges
10. `InefficiencyReclaimStore` - 44 edges

## Surprising Connections (you probably didn't know these)
- `api_watchlist()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py
- `api_market_screener()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py
- `api_inefficiency_reclaim()` --uses--> `InefficiencyReclaimStore`  [INFERRED]
  dashboard_react/server.py → src/storage/inefficiency_reclaim_store.py
- `api_inefficiency_reclaim()` --indirect_call--> `load_stock_symbols()`  [INFERRED]
  dashboard_react/server.py → src/symbol_universe.py
- `api_inefficiency_reclaim_active()` --uses--> `InefficiencyReclaimStore`  [INFERRED]
  dashboard_react/server.py → src/storage/inefficiency_reclaim_store.py

## Import Cycles
- None detected.

## Communities (125 total, 30 thin omitted)

### Community 0 - "premarket.py"
Cohesion: 0.06
Nodes (52): bar_metadata(), bar_path(), _bar_store_lock(), _ensure_dir(), index_snapshot(), load_bars(), _normalize_frame(), DataFrame (+44 more)

### Community 1 - "test_quote_check_job.py"
Cohesion: 0.14
Nodes (6): _quote_check_once(), Report quote spread details for a single symbol against the configured limit., _MarketDataServiceStub, _NewsRiskFilterStub, _OrderManagerStub, QuoteCheckJobTests

### Community 2 - "execution/inefficiency_reclaim.py"
Cohesion: 0.14
Nodes (13): InefficiencyReclaimPaperExecutor, IRSExecutionPolicy, IRSExecutionResult, IRSExpiryCancellationResult, IRSReconnectResult, datetime, Decimal, Fail-closed paper executor for ENTRY_ARMED IRS candidates. (+5 more)

### Community 3 - "forecast.py"
Cohesion: 0.13
Nodes (23): _bars_freshness(), calculate_open_risk(), _candidate(), ForecastScenario, _number(), projected_level_candidates(), Any, DataFrame (+15 more)

### Community 4 - "false_breakout_one_bar.py"
Cohesion: 0.08
Nodes (65): PatternName, SignalSide, body_size(), DecisionSink, Record a decision into ``sink`` if one is provided; otherwise do nothing., record(), _complex_score(), detect_false_breakout() (+57 more)

### Community 5 - "order_requests.py"
Cohesion: 0.05
Nodes (49): _acquire_lock(), claim_pending(), list_requests(), _load(), _mutate(), _now(), pending_requests(), Any (+41 more)

### Community 6 - "SlackAlerter"
Cohesion: 0.06
Nodes (26): Path, Send alerts to Slack with graceful degradation when disabled., SlackAlerter, _ensure_event_loop(), IBKRDependencyError, OrderResult, Interactive Brokers connectivity built on top of ib_insync., ib_insync/eventkit expects a current event loop on newer Python versions. (+18 more)

### Community 7 - "timedelta"
Cohesion: 0.14
Nodes (5): PaperExecutorTests, make_zone(), RetraceAndConfirmationTests, zone(), timedelta

### Community 8 - "src/main.py"
Cohesion: 0.04
Nodes (69): Namespace, Slack webhook notifications., append_markdown_log(), ensure_directories(), Create runtime and memory directories expected by the application., Append a timestamped Markdown section to a log file., _build_summary(), _build_ticker_section() (+61 more)

### Community 9 - "project/support.js"
Cohesion: 0.07
Nodes (61): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), getDC(), Dispatcher(), createExternalModules() (+53 more)

### Community 10 - "Level"
Cohesion: 0.06
Nodes (58): detect_breakout(), Detect a Gerchik-style two-step confirmed breakout on intraday bars., apply_strength_scores(), _fallback_score(), filter_strong_levels(), Level strength scoring helpers., Return the precomputed strength score, falling back to the legacy field., score_level() (+50 more)

### Community 11 - "Trading Bot Dashboard/support.js"
Cohesion: 0.07
Nodes (61): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), getDC(), Dispatcher(), createExternalModules() (+53 more)

### Community 12 - "ScreenerParams"
Cohesion: 0.10
Nodes (40): _add_metrics(), _apply_anchor_date(), _approach_profile(), _bar_position(), _business_day_offset(), _date_value(), _detect_signals(), _event_dates() (+32 more)

### Community 13 - "strategy/inefficiency_reclaim.py"
Cohesion: 0.13
Nodes (53): _diagnose_no_candidate(), _add_rth_hours(), Bar, _bounded(), build_explanation(), build_inefficiency_zone(), build_low_overlap_zone(), build_signal_id() (+45 more)

### Community 14 - "data_access.py"
Cohesion: 0.11
Nodes (45): render_reports(), bars_index(), blocked_news_summary(), crypto_bars_index(), _filter_watchlist_symbols(), freshness(), get_bars(), get_crypto_bars() (+37 more)

### Community 15 - "NasdaqDataClient"
Cohesion: 0.10
Nodes (21): date, NasdaqDataConfig, Optional Nasdaq market-data fallback for historical candles., _cloud_precision(), _cloud_range(), _daily_cloud_range(), _duration_days(), _duration_start_date() (+13 more)

### Community 16 - "SlackCommandProcessor"
Cohesion: 0.12
Nodes (6): Path, Parsed Slack command., Poll a Slack channel for whitelisted bot commands., SlackCommand, SlackCommandProcessor, SlackCommandTests

### Community 17 - "order_journal.py"
Cohesion: 0.09
Nodes (45): _base_row(), _closed_positions_by_symbol(), _f(), _fill_price_from_result(), _infer_bracket_exit_from_bars(), load_order_journal(), load_reviews(), _merge_reviews() (+37 more)

### Community 18 - "server.py"
Cohesion: 0.13
Nodes (36): api_bars(), api_crypto(), api_crypto_bars(), api_crypto_manual_orders(), api_crypto_strategy(), api_crypto_symbol(), api_crypto_tradingview_state(), api_dashboard() (+28 more)

### Community 19 - "backtest/inefficiency_reclaim.py"
Cohesion: 0.16
Nodes (21): BacktestCosts, BacktestTrade, _bar_contains_time(), build_backtest_report(), _entry_fill(), _exit_touches(), _maximum_drawdown(), Any (+13 more)

### Community 20 - "jobs/inefficiency_reclaim.py"
Cohesion: 0.05
Nodes (41): _next_manifest_run(), Exception, ensure_irs_history(), history_specs(), IRSHistorySpec, _mark_skipped_remaining(), _paced_get_bars(), Any (+33 more)

### Community 21 - "components.py"
Cohesion: 0.15
Nodes (33): render_dashboard(), render_navigation(), bias_card(), blocked_news_panel(), _clean(), _esc(), feature_card(), fmt() (+25 more)

### Community 22 - "InefficiencyReclaimStore"
Cohesion: 0.08
Nodes (24): Connection, ImmutableZoneError, InefficiencyReclaimStore, _json(), _json_default(), Any, datetime, Decimal (+16 more)

### Community 23 - "TradeSignal"
Cohesion: 0.11
Nodes (26): build_partial_targets(), Shared strategy data models., TradeSignal, _detect_enabled_candidates(), _level_zone(), _note_value(), DataFrame, DecisionSink (+18 more)

### Community 24 - "src/config.py"
Cohesion: 0.09
Nodes (29): Logger, BrokerConfig, _csv_env(), _csv_env_file_or_fallback(), _csv_env_with_fallback(), _dedupe_preserve_order(), _env_bool(), _env_float() (+21 more)

### Community 25 - "forex/tradingview_webhook.py"
Cohesion: 0.25
Nodes (17): fx_pair_components(), _append_execution(), _as_float(), _as_int(), _expected_bot_id(), ForexTradingViewWebhookError, handle_forex_tradingview_webhook(), load_forex_tradingview_state() (+9 more)

### Community 26 - "stocks/tradingview_webhook.py"
Cohesion: 0.27
Nodes (17): _append_execution(), _as_float(), _as_int(), _expected_bot_id(), _first_float(), handle_stock_tradingview_webhook(), load_stock_tradingview_state(), _normalize_stock_symbol() (+9 more)

### Community 27 - "validator.py"
Cohesion: 0.11
Nodes (15): Place a human-initiated (dashboard) order. This bypasses the *autonomous*…, calculate_position_size(), position_value_ok(), Position sizing logic., Size a position so the loss to stop equals the allowed risk budget., _optional_float(), Universal trade validation rules., Explainable validator wrapper that preserves deterministic reason tracking. (+7 more)

### Community 29 - "test_p0_fixes.py"
Cohesion: 0.09
Nodes (14): detect_false_breakout(), DataFrame, False breakout detection — base module, intentionally disabled. This module…, Detect a level breach followed by immediate rejection. .. deprecated:: This…, _make_level(), DataFrame, Tests covering P0 fixes for Gerchik-style trading logic. P0-A:…, Verify that breakout and rebound honour the configured RR minimum. We patch… (+6 more)

### Community 30 - "show_df"
Cohesion: 0.40
Nodes (10): render_header(), human_age(), market_session(), DataFrame, datetime, show_df(), source_health_bar(), clear_caches() (+2 more)

### Community 31 - "scanners/inefficiency_reclaim.py"
Cohesion: 0.13
Nodes (27): BarLoader, QuoteLoader, _account_state(), candidate_row(), classify_market_regime(), classify_market_trend_direction(), data_quality_diagnostics(), frame_to_bars() (+19 more)

### Community 32 - "dashboard/app.py"
Cohesion: 0.21
Nodes (22): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+14 more)

### Community 33 - "levels_export.py"
Cohesion: 0.16
Nodes (19): _center(), _clean_gap_annotations(), _coerce_float(), export_premarket_levels_report(), _gap_to_upper_level(), _level_key(), _level_price(), _optimization_reason() (+11 more)

### Community 34 - "project/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.16
Nodes (26): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+18 more)

### Community 35 - "Trade Log"
Cohesion: 0.00
Nodes (422): End Of Day (2026-04-27T22:05:40), End Of Day (2026-04-27T22:06:16), End Of Day (2026-04-27T22:13:29), End Of Day (2026-04-28T16:10:04), End Of Day (2026-04-29T16:10:04), End Of Day (2026-04-30T16:10:04), End Of Day (2026-04-30T16:11:49), End Of Day (2026-04-30T16:39:25) (+414 more)

### Community 36 - "rebound.py"
Cohesion: 0.12
Nodes (38): average_range(), close_above_level(), close_below_level(), close_location(), full_range(), has_compression(), is_abnormal_candle(), is_bearish() (+30 more)

### Community 37 - "MarketDataServiceTests"
Cohesion: 0.13
Nodes (6): _BrokerStub, _DurationBrokerStub, MarketDataServiceTests, _NasdaqProviderStub, DataFrame, _QuoteBrokerStub

### Community 38 - "Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.21
Nodes (22): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+14 more)

### Community 39 - "make_bar"
Cohesion: 0.27
Nodes (3): DisplacementAndZoneTests, make_bar(), RobustATRTests

### Community 40 - "chart_history.py"
Cohesion: 0.14
Nodes (25): main(), Path, _symbol_from_intraday_file(), backfill_daily_from_intraday(), ChartHistorySpec, daily_bars_from_intraday(), _daily_to_weekly(), ensure_required_chart_history() (+17 more)

### Community 41 - "false_breakout_continuation.py"
Cohesion: 0.18
Nodes (16): _context(), _continuation_score(), _continuation_stop(), _current_session_uptrend_from_level(), detect_false_breakout_continuation(), _is_prior_false_breakdown(), _normalize_bars(), DataFrame (+8 more)

### Community 42 - "test_news_blocking.py"
Cohesion: 0.32
Nodes (3): NewsBlockingTests, Tests for news blocking., StubNewsService

### Community 44 - "metric_grid"
Cohesion: 0.44
Nodes (11): render_intraday(), mark_levels(), metric_grid(), Render a responsive grid of glass metric cards., all_decision_attempts(), decisions_for_symbol(), decisions_to_frame(), load_daily_decisions() (+3 more)

### Community 45 - "load_stock_symbols"
Cohesion: 0.15
Nodes (21): api_watchlist_add(), api_watchlist_bulk_add(), api_watchlist_remove(), _onboard_client_id(), _parse_stock_symbol_upload(), _queue_bulk_stock_onboarding(), _client_id(), _load_symbols() (+13 more)

### Community 46 - "session_utils.py"
Cohesion: 0.05
Nodes (76): NowProvider, SleepProvider, _matching_headlines(), NewsRiskFilter, Evaluate symbol-specific and macro news risk before trading., datetime, Intraday scanning and position-management loop., Continue scanning for new entries and manage any open positions. (+68 more)

### Community 47 - "candles_with_levels"
Cohesion: 0.14
Nodes (18): candles_with_levels(), capital_gauges(), _focus_price(), forecast_rr_scatter(), forecast_sensitivity(), _labeled_trade_level_ids(), _level_price(), mark_forecast_position() (+10 more)

### Community 48 - "Levels Log"
Cohesion: 0.02
Nodes (126): Daily Levels (2026-04-27T22:02:49), Daily Levels (2026-04-27T22:03:01), Daily Levels (2026-04-27T22:04:21), Daily Levels (2026-04-27T22:12:41), Daily Levels (2026-04-27T22:36:13), Daily Levels (2026-04-27T22:45:49), Daily Levels (2026-04-27T22:47:57), Daily Levels (2026-04-27T22:53:40) (+118 more)

### Community 49 - "_write_env_setting"
Cohesion: 0.23
Nodes (9): api_inefficiency_reclaim_settings(), api_update_inefficiency_reclaim_settings(), _parse_irs_min_daily_history_rows(), _parse_irs_minimum_display_score(), Atomically update one allowlisted environment setting., _set_irs_min_daily_history_rows(), _set_irs_minimum_display_score(), _write_env_setting() (+1 more)

### Community 50 - "IBKR Gerchik Bot"
Cohesion: 0.07
Nodes (26): Configuration, Current Limitations, Dashboard, Data And Jobs, Execution And Replay, Hard Filters, Inefficiency Reclaim Strategy, State And Audit (+18 more)

### Community 51 - "NewsService"
Cohesion: 0.11
Nodes (8): InefficiencyReclaimSettings, normalize_symbol(), Validated environment-facing settings for the isolated IRS subsystem., Build the pure strategy config without making that module read env., Settings, NewsService, Fetch symbol, macro, and earnings context from NewsAPI.ai / Event Registry., SymbolHandlingTests

### Community 52 - "Research Log"
Cohesion: 0.00
Nodes (422): Premarket Research (2026-04-27T22:02:49), Premarket Research (2026-04-27T22:03:01), Premarket Research (2026-04-27T22:04:21), Premarket Research (2026-04-27T22:12:41), Premarket Research (2026-04-27T22:36:13), Premarket Research (2026-04-27T22:45:49), Premarket Research (2026-04-27T22:47:57), Premarket Research (2026-04-27T22:53:40) (+414 more)

### Community 53 - "crypto/tradingview_webhook.py"
Cohesion: 0.19
Nodes (27): BackgroundTasks, api_crypto_tradingview_webhook(), _append_execution(), _as_float(), enqueue_tradingview_webhook(), _execution_from_signal(), _extract_okx_order_id(), _find_execution() (+19 more)

### Community 55 - "decision_log.py"
Cohesion: 0.22
Nodes (14): _acquire_daily_decisions_lock(), _apply_to_decisions(), _build_attempt(), _decision_rank(), _load_daily_decisions(), _lock_is_stale(), persist(), persist_decision() (+6 more)

### Community 56 - "symbols.py"
Cohesion: 0.16
Nodes (8): _dedupe(), load_crypto_symbol_inputs(), Path, Normalize TradingView/OKX crypto symbols into OKX instrument ids., Remove every configured spelling that resolves to ``symbol``. Comments and…, remove_crypto_symbol(), _split_raw_text(), CryptoStrategyMonitorTests

### Community 58 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 59 - "normalize_okx_instrument"
Cohesion: 0.12
Nodes (22): api_crypto_add(), api_crypto_analyze(), api_crypto_bulk_add(), api_crypto_order_place(), api_crypto_order_simulate(), api_crypto_remove(), api_forex_tradingview_webhook(), api_order_close() (+14 more)

### Community 62 - "OKXClient"
Cohesion: 0.17
Nodes (8): RuntimeError, OKXClient, OKXInstrument, Any, DataFrame, Small OKX REST client for public candles and future private order work., Place a SPOT order, optionally with attached market-exit TP/SL. Private…, OKXSpotOrderPayloadTests

### Community 63 - "IBKRClient"
Cohesion: 0.08
Nodes (17): IBKRClient, Any, Connect to IBKR TWS or IB Gateway with retry logic., Select live/frozen/delayed market data for this IBKR connection., Reconnect if the live connection is not healthy., Return executions currently available from IBKR as plain records.…, Request a snapshot and return bid/ask/last/close safely., Fetch historical bars and return a DataFrame. (+9 more)

### Community 64 - "manual_order.py"
Cohesion: 0.31
Nodes (14): _client_order_id(), _decimal(), _decimal_text(), execute_manual_demo_order(), _floor_to_step(), _instrument(), load_manual_order_state(), _now() (+6 more)

### Community 66 - "BrokerStub"
Cohesion: 0.11
Nodes (11): _coerce_float(), nearest_level_details(), datetime, Path, _quote_reference_price(), Excel exports for intraday scan outcomes., Write one intraday scan result workbook and return its path., Return the most relevant watchlist level for reporting. (+3 more)

### Community 67 - "collect_watchlist_intraday_bars"
Cohesion: 0.19
Nodes (11): _collector_session_bounds(), _collector_session_is_open(), _next_collector_open(), datetime, Independent intraday bar collector for dashboard continuity., Return the intraday collection window for the date represented by ``now``., Collect bars without account synchronization, signals, or orders., run_market_data_collector() (+3 more)

### Community 68 - "CryptoOrderRequest"
Cohesion: 0.17
Nodes (11): CryptoOrderManager, CryptoOrderRequest, OKX crypto order planning and safe simulated execution. Crypto orders…, Expose the shared stock risk settings used for crypto sizing., Floor generic precision so rounding can never exceed buying power., _reward_risk(), _round_crypto_quantity(), stock_risk_settings() (+3 more)

### Community 69 - "broker_reconcile.py"
Cohesion: 0.16
Nodes (19): _execution_closed_record(), _execution_matches_request(), _now(), _order_ids(), _parse_dt(), _place_requests(), Any, datetime (+11 more)

### Community 70 - "breakout.py"
Cohesion: 0.13
Nodes (27): calculate_stop_loss(), round_number_guard(), calculate_take_profit(), reward_risk_ratio(), _acts_as_resistance(), _acts_as_support(), _atr_used(), _bar_index() (+19 more)

### Community 71 - "Weekly Log"
Cohesion: 0.09
Nodes (21): Weekly Log, Weekly Metrics (2026-04-23T22:20:31), Weekly Metrics (2026-04-23T23:20:28), Weekly Review (2026-04-23T23:46:43), Weekly Review (2026-05-01T16:25:02), Weekly Review (2026-05-13T22:21:19), Weekly Review (2026-05-15T16:25:02), Weekly Review (2026-05-22T16:25:02) (+13 more)

### Community 73 - "StrategyCandidate"
Cohesion: 0.18
Nodes (19): _historical_analysis_candidate(), Remove live-only risk gates from an anchor-date analysis result., ConfirmationEvent, ConfirmationType, _json_value(), OrderPlan, Any, Enum (+11 more)

### Community 75 - "IRSBroker"
Cohesion: 0.25
Nodes (3): Protocol, IRSBroker, Any

### Community 76 - "should_trigger_kill_switch"
Cohesion: 0.31
Nodes (6): _equity_symbols(), Trading kill switch conditions., Block all trading when safety conditions are breached., should_trigger_kill_switch(), KillSwitchTests, Tests for kill switch behavior.

### Community 78 - "crypto/config.py"
Cohesion: 0.36
Nodes (7): CryptoConfig, _env_bool(), _env_csv(), _env_float(), _env_int(), _env_str(), Crypto bot configuration. API secrets should live in the local .env file only.…

### Community 80 - "load_workflow_context"
Cohesion: 0.33
Nodes (8): load_workflow_context(), Path, Helpers for reading strategy and recent workflow memory., Return the tail of a text file, or an empty string if it does not exist., Return the full contents of a text file, or an empty string if missing., Load the strategy doc plus recent research/trade context for workflow jobs., read_text_full(), read_text_tail()

### Community 81 - "third_touch.py"
Cohesion: 0.33
Nodes (6): detect_third_touch(), DataFrame, Series, Third touch setup detection., Detect the third qualified interaction with a level., _touch_indices()

### Community 82 - "_connect_broker_with_startup_retry"
Cohesion: 0.16
Nodes (5): _connect_broker_with_startup_retry(), Create and connect an IBKR client, waiting through temporary startup contention., _AlwaysBusyBrokerStub, _FlakyBrokerStub, IBKRStartupRetryTests

### Community 88 - "add_bmsb_signals"
Cohesion: 0.40
Nodes (5): add_bmsb_signals(), main(), DataFrame, BMSB Strategy 1: Bull Market Support Band conversion. Long-only strategy on…, Add daily signals from completed weekly BMSB values.

### Community 89 - "run_crypto_analysis"
Cohesion: 0.19
Nodes (21): load_crypto_symbols(), collect_crypto_bars(), configured_crypto_symbols(), run_crypto_analysis(), validate_symbols(), ensure_crypto_directories(), _analysis_summary(), main() (+13 more)

### Community 90 - "MarketDataService"
Cohesion: 0.15
Nodes (9): main(), One-off backfill of OHLCV bars for the dashboard. Connects to TWS / IB Gateway…, MarketDataService, DataFrame, datetime, Fetch a timeframe for an incremental strategy cache., Provide reusable market data retrieval wrappers., Allow delayed data when the account lacks a live subscription. (+1 more)

### Community 91 - "_irs_schedule_status"
Cohesion: 0.24
Nodes (15): api_services(), _irs_schedule_status(), _iso_age_seconds(), _latest_job_log_status(), _lock_status(), _market_collector_window_status(), _pid_alive(), _premarket_status() (+7 more)

### Community 94 - "DESIGN.md"
Cohesion: 0.15
Nodes (12): Brand & Style, Buttons, Cards, Colors, Components, Data Visualization, Elevation & Depth, Input Fields (+4 more)

### Community 95 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 112 - "analysis.py"
Cohesion: 0.22
Nodes (16): analyze_symbol(), load_crypto_state(), _quiet_shared_level_logs(), Crypto Gerchik-style analysis using the shared stock strategy logic., Keep crypto batch scans from flooding the shared stock bot log file., crypto_bar_path(), crypto_index_snapshot(), load_crypto_bars() (+8 more)

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

### Community 135 - "_daily_live_frame"
Cohesion: 0.23
Nodes (12): _bmsb_scan_symbol(), _bmsb_strategy2_scan_symbol(), _cross_over(), _cross_under(), _daily_live_frame(), _gaussian_alpha(), _gaussian_filter(), _gaussian_scan_symbol() (+4 more)

### Community 139 - "Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab, Source Nodes

## Knowledge Gaps
- **1085 isolated node(s):** `BrokerConfig`, `RiskConfig`, `StrategyConfig`, `TradingHours`, `NewsConfig` (+1080 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **30 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Level` connect `Level` to `premarket.py`, `forecast.py`, `false_breakout_one_bar.py`, `rebound.py`, `breakout.py`, `src/main.py`, `false_breakout_continuation.py`, `session_utils.py`, `TradeSignal`, `decision_log.py`, `test_p0_fixes.py`?**
  _High betweenness centrality (0.033) - this node is a cross-community bridge._
- **Why does `MarketDataService` connect `MarketDataService` to `premarket.py`, `test_quote_check_job.py`, `collect_watchlist_intraday_bars`, `MarketDataServiceTests`, `SlackAlerter`, `chart_history.py`, `src/main.py`, `session_utils.py`, `NasdaqDataClient`, `jobs/inefficiency_reclaim.py`, `IBKRClient`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Why does `SlackAlerter` connect `SlackAlerter` to `src/main.py`, `SlackCommandProcessor`, `session_utils.py`?**
  _High betweenness centrality (0.016) - this node is a cross-community bridge._
- **Are the 56 inferred relationships involving `Level` (e.g. with `_level_center()` and `_level_zone()`) actually correct?**
  _`Level` has 56 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `IBKRClient` (e.g. with `MarketDataService` and `NewsService`) actually correct?**
  _`IBKRClient` has 10 INFERRED edges - model-reasoned connections that need verification._
- **What connects `BrokerConfig`, `RiskConfig`, `StrategyConfig` to the rest of the system?**
  _1085 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `premarket.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06451612903225806 - nodes in this community are weakly interconnected._