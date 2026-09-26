# Graph Report - ibkr-gerchik-bot  (2026-08-27)

## Corpus Check
- 216 files · ~29,105,111 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3683 nodes · 8193 edges · 129 communities (97 shown, 32 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 283 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `14a1ba59`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- symbol_onboarding.py
- src/main.py
- InefficiencyReclaimStore
- forecast.py
- false_breakout_one_bar.py
- order_requests.py
- OrderManager
- timedelta
- premarket.py
- project/support.js
- levels.py
- Trading Bot Dashboard/support.js
- ScreenerParams
- strategy/inefficiency_reclaim.py
- data_access.py
- NasdaqDataClient
- SlackAlerter
- order_journal.py
- _safe
- Bar
- jobs/inefficiency_reclaim.py
- components.py
- execution/inefficiency_reclaim.py
- TradeSignal
- src/config.py
- reward_risk_ratio
- stocks/tradingview_webhook.py
- validator.py
- test_inefficiency_reclaim.py
- _make_level
- atr.py
- scanners/inefficiency_reclaim.py
- dashboard/app.py
- levels_export.py
- project/ibkr-gerchik-bot/dashboard/app.py
- Trade Log
- rebound.py
- MarketDataServiceTests
- Trading Bot Dashboard/ibkr-gerchik-bot/dashboard/app.py
- detect_displacement
- chart_history.py
- false_breakout_continuation.py
- NewsRiskFilter
- BarStoreMergeTests
- show_df
- load_stock_symbols
- session_utils.py
- candles_with_levels
- Levels Log
- BrokerStub
- IBKR Gerchik Bot
- NewsService
- Research Log
- crypto/tradingview_webhook.py
- test_irs_scheduler.py
- decision_log.py
- symbols.py
- ensure_irs_history
- What You Must Do When Invoked
- FakeMarketData
- install_windows_scheduled_tasks.ps1
- build_task_scheduler_command
- OKXClient
- IBKRClient
- _NewsRiskFilterStub
- false_breakout.py
- nearest_level_details
- market_data_collector.py
- manual_order.py
- broker_reconcile.py
- Level
- Weekly Log
- DashboardDataAccessTests
- StrategyCandidate
- _BrokerStub
- IRSBroker
- should_trigger_kill_switch
- _BrokerStub
- crypto/config.py
- _BrokerStub
- _FlakyBrokerStub
- third_touch.py
- _AlwaysBusyBrokerStub
- TestBaseDetectFalseBreakoutDisabled
- _BrokerStub
- _MarketDataServiceStub
- _FakeEvent
- add_bmsb_signals
- analysis.py
- MarketDataService
- server.py
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
- crypto/bar_store.py
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

## Communities (129 total, 32 thin omitted)

### Community 0 - "symbol_onboarding.py"
Cohesion: 0.13
Nodes (28): bar_metadata(), bar_path(), _bar_store_lock(), _ensure_dir(), index_snapshot(), load_bars(), _normalize_frame(), DataFrame (+20 more)

### Community 1 - "src/main.py"
Cohesion: 0.05
Nodes (46): Namespace, maybe_commit_and_push(), Path, Optional git commit/push helpers for workflow jobs., Commit and push workflow outputs when AUTO_GIT_PUSH is enabled., _run_git(), archive_positions(), Merge current positions without deleting symbols that later disappear. (+38 more)

### Community 2 - "InefficiencyReclaimStore"
Cohesion: 0.19
Nodes (8): Connection, InefficiencyReclaimStore, _json(), _json_default(), Any, datetime, Path, ValueError

### Community 3 - "forecast.py"
Cohesion: 0.06
Nodes (33): _bars_freshness(), calculate_open_risk(), _candidate(), ForecastScenario, _number(), projected_level_candidates(), Any, DataFrame (+25 more)

### Community 4 - "false_breakout_one_bar.py"
Cohesion: 0.11
Nodes (50): PatternName, SignalSide, body_size(), _complex_score(), detect_false_breakout(), DataFrame, Series, Gerchik-style complex 3+ bar false breakout detection helpers with… (+42 more)

### Community 5 - "order_requests.py"
Cohesion: 0.08
Nodes (57): _acquire_lock(), claim_pending(), list_requests(), _load(), _mutate(), _now(), pending_requests(), Any (+49 more)

### Community 6 - "OrderManager"
Cohesion: 0.10
Nodes (19): OrderResult, OrderManager, Execute validated trades and record the resulting actions., _current_session_bars(), DataFrame, Return bars from the active trading session while preserving full-history…, Run one deterministic entry scan over the prepared watchlist., run_entry_scan() (+11 more)

### Community 7 - "timedelta"
Cohesion: 0.15
Nodes (4): PaperExecutorTests, make_zone(), RetraceAndConfirmationTests, timedelta

### Community 8 - "premarket.py"
Cohesion: 0.06
Nodes (48): Slack webhook notifications., append_markdown_log(), ensure_directories(), Create runtime and memory directories expected by the application., Append a timestamped Markdown section to a log file., _build_summary(), _build_ticker_section(), _build_workflow_fallback_summary() (+40 more)

### Community 9 - "project/support.js"
Cohesion: 0.07
Nodes (61): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), getDC(), Dispatcher(), createExternalModules() (+53 more)

### Community 10 - "levels.py"
Cohesion: 0.08
Nodes (42): _atr_distances(), _build_zone(), calculate_atr(), _clean_gap_atr_pct_between_levels(), _clean_gap_between_levels(), _cluster_levels(), dedupe_levels(), detect_levels() (+34 more)

### Community 11 - "Trading Bot Dashboard/support.js"
Cohesion: 0.07
Nodes (61): boot(), collectProps(), compileAttr(), compileTemplate(), createComponentFactory(), getDC(), Dispatcher(), createExternalModules() (+53 more)

### Community 12 - "ScreenerParams"
Cohesion: 0.10
Nodes (40): _add_metrics(), _apply_anchor_date(), _approach_profile(), _bar_position(), _business_day_offset(), _date_value(), _detect_signals(), _event_dates() (+32 more)

### Community 13 - "strategy/inefficiency_reclaim.py"
Cohesion: 0.17
Nodes (41): _add_rth_hours(), _bounded(), build_explanation(), build_inefficiency_zone(), build_low_overlap_zone(), build_signal_id(), build_strict_gap_zone(), calculate_entry_trigger() (+33 more)

### Community 14 - "data_access.py"
Cohesion: 0.12
Nodes (39): bars_index(), blocked_news_summary(), crypto_bars_index(), _filter_watchlist_symbols(), freshness(), get_crypto_bars(), latest_trade_log_sections(), _latest_workflow_watchlist_for_symbols() (+31 more)

### Community 15 - "NasdaqDataClient"
Cohesion: 0.10
Nodes (21): date, NasdaqDataConfig, Optional Nasdaq market-data fallback for historical candles., _cloud_precision(), _cloud_range(), _daily_cloud_range(), _duration_days(), _duration_start_date() (+13 more)

### Community 16 - "SlackAlerter"
Cohesion: 0.07
Nodes (12): Path, Send alerts to Slack with graceful degradation when disabled., SlackAlerter, Path, Safe Slack command polling and dispatch., Parsed Slack command., Poll a Slack channel for whitelisted bot commands., SlackCommand (+4 more)

### Community 17 - "order_journal.py"
Cohesion: 0.09
Nodes (45): _base_row(), _closed_positions_by_symbol(), _f(), _fill_price_from_result(), _infer_bracket_exit_from_bars(), load_order_journal(), load_reviews(), _merge_reviews() (+37 more)

### Community 18 - "_safe"
Cohesion: 0.11
Nodes (28): api_bars(), api_crypto(), api_crypto_bars(), api_crypto_manual_orders(), api_crypto_strategy(), api_crypto_symbol(), api_crypto_tradingview_state(), api_dashboard() (+20 more)

### Community 19 - "Bar"
Cohesion: 0.16
Nodes (23): BacktestCosts, BacktestTrade, _bar_contains_time(), build_backtest_report(), _entry_fill(), _exit_touches(), _maximum_drawdown(), Any (+15 more)

### Community 20 - "jobs/inefficiency_reclaim.py"
Cohesion: 0.20
Nodes (10): active_confirmation_symbols(), notify_inefficiency_reclaim_scan(), Any, datetime, Connected IRS data-hydration and analysis workflow., Return non-expired symbols that need the 15-minute confirmation scan., run_inefficiency_reclaim_job(), _summary_value() (+2 more)

### Community 21 - "components.py"
Cohesion: 0.11
Nodes (36): render_header(), render_navigation(), bias_card(), blocked_news_panel(), _clean(), _esc(), feature_card(), hero() (+28 more)

### Community 22 - "execution/inefficiency_reclaim.py"
Cohesion: 0.08
Nodes (27): InefficiencyReclaimPaperExecutor, IRSExecutionResult, IRSExpiryCancellationResult, IRSReconnectResult, datetime, Decimal, Fail-closed paper executor for ENTRY_ARMED IRS candidates., Resize both protective children to exactly the filled quantity. (+19 more)

### Community 23 - "TradeSignal"
Cohesion: 0.08
Nodes (38): build_partial_targets(), DecisionSink, Record a decision into ``sink`` if one is provided; otherwise do nothing., record(), detect_false_breakout_complex(), DecisionSink, Router-compatible wrapper returning a TradeSignal for complex false breakouts., detect_false_breakout_one_bar() (+30 more)

### Community 24 - "src/config.py"
Cohesion: 0.09
Nodes (27): Logger, BrokerConfig, _csv_env(), _csv_env_file_or_fallback(), _csv_env_with_fallback(), _dedupe_preserve_order(), _env_bool(), _env_float() (+19 more)

### Community 25 - "reward_risk_ratio"
Cohesion: 0.20
Nodes (9): _build_manual_watch_trade_signal(), _infer_manual_watch_signal(), calculate_stop_loss(), calculate_take_profit(), reward_risk_ratio(), Tests for stop and reward/risk logic., StopTakeProfitTests, Tests for technical stop and target handling. (+1 more)

### Community 26 - "stocks/tradingview_webhook.py"
Cohesion: 0.27
Nodes (17): _append_execution(), _as_float(), _as_int(), _expected_bot_id(), _first_float(), handle_stock_tradingview_webhook(), load_stock_tradingview_state(), _normalize_stock_symbol() (+9 more)

### Community 27 - "validator.py"
Cohesion: 0.11
Nodes (15): Place a human-initiated (dashboard) order. This bypasses the *autonomous*…, calculate_position_size(), position_value_ok(), Position sizing logic., Size a position so the loss to stop equals the allowed risk budget., _optional_float(), Universal trade validation rules., Explainable validator wrapper that preserves deterministic reason tracking. (+7 more)

### Community 28 - "test_inefficiency_reclaim.py"
Cohesion: 0.18
Nodes (8): AccountState, evaluate_hard_gates(), Decimal, Quote, size_position(), StrategyContext, StructuralLevel, PlanningAndGateTests

### Community 29 - "_make_level"
Cohesion: 0.13
Nodes (8): _make_level(), DataFrame, Verify that breakout and rebound honour the configured RR minimum. We patch…, Bars that produce a valid rebound setup but only ~2.5 R to the nearest level., TestComplexStopBuffer, TestMinTouchesFromSettings, TestRRMinimumUnified, TestTwoBarStopBuffer

### Community 30 - "atr.py"
Cohesion: 0.20
Nodes (11): Evaluate whether any current watchlist symbols would pass full order validation., _validate_watchlist_once(), atr_travel_filter(), calculate_daily_atr(), calculate_technical_atr(), DataFrame, ATR helpers for Gerchik-style trade filtering., Calculate ATR from the last 3-5 completed daily candles excluding abnormal… (+3 more)

### Community 31 - "scanners/inefficiency_reclaim.py"
Cohesion: 0.13
Nodes (28): BarLoader, QuoteLoader, _account_state(), candidate_row(), classify_market_regime(), classify_market_trend_direction(), data_quality_diagnostics(), _diagnose_no_candidate() (+20 more)

### Community 32 - "dashboard/app.py"
Cohesion: 0.16
Nodes (28): _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity(), _level_frame() (+20 more)

### Community 33 - "levels_export.py"
Cohesion: 0.16
Nodes (19): _center(), _clean_gap_annotations(), _coerce_float(), export_premarket_levels_report(), _gap_to_upper_level(), _level_key(), _level_price(), _optimization_reason() (+11 more)

### Community 34 - "project/ibkr-gerchik-bot/dashboard/app.py"
Cohesion: 0.20
Nodes (24): load_tracked_positions(), _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame(), _forecast_trade_identity() (+16 more)

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
Cohesion: 0.20
Nodes (25): fmt(), panel_header(), _as_dict(), _as_list(), _confirm_close_position(), _confirm_place_order(), _forecast_defaults(), _forecast_result_frame() (+17 more)

### Community 39 - "detect_displacement"
Cohesion: 0.18
Nodes (10): calculate_overlap_ratio(), calculate_relative_volume(), calculate_robust_atr(), detect_displacement(), Calculate the median/MAD filtered and symmetrically trimmed ATR., Current volume divided by median prior volume; current is excluded., Intersection of prior/current ranges divided by the current true range., DisplacementAndZoneTests (+2 more)

### Community 40 - "chart_history.py"
Cohesion: 0.14
Nodes (25): main(), Path, _symbol_from_intraday_file(), backfill_daily_from_intraday(), ChartHistorySpec, daily_bars_from_intraday(), _daily_to_weekly(), ensure_required_chart_history() (+17 more)

### Community 41 - "false_breakout_continuation.py"
Cohesion: 0.18
Nodes (18): _context(), _continuation_score(), _continuation_stop(), _current_session_uptrend_from_level(), detect_false_breakout_continuation(), _is_prior_false_breakdown(), _normalize_bars(), DataFrame (+10 more)

### Community 42 - "NewsRiskFilter"
Cohesion: 0.18
Nodes (6): _matching_headlines(), NewsRiskFilter, Evaluate symbol-specific and macro news risk before trading., NewsBlockingTests, Tests for news blocking., StubNewsService

### Community 44 - "show_df"
Cohesion: 0.27
Nodes (17): render_intraday(), render_reports(), mark_levels(), metric_grid(), DataFrame, Render a responsive grid of glass metric cards., show_df(), all_decision_attempts() (+9 more)

### Community 45 - "load_stock_symbols"
Cohesion: 0.15
Nodes (21): api_watchlist_add(), api_watchlist_bulk_add(), api_watchlist_remove(), _onboard_client_id(), _parse_stock_symbol_upload(), _queue_bulk_stock_onboarding(), _client_id(), _load_symbols() (+13 more)

### Community 46 - "session_utils.py"
Cohesion: 0.05
Nodes (72): NowProvider, SleepProvider, datetime, Intraday scanning and position-management loop., Continue scanning for new entries and manage any open positions., run_intraday(), datetime, Market open entry-scanning loop. (+64 more)

### Community 47 - "candles_with_levels"
Cohesion: 0.14
Nodes (18): candles_with_levels(), capital_gauges(), _focus_price(), forecast_rr_scatter(), forecast_sensitivity(), _labeled_trade_level_ids(), _level_price(), mark_forecast_position() (+10 more)

### Community 48 - "Levels Log"
Cohesion: 0.02
Nodes (126): Daily Levels (2026-04-27T22:02:49), Daily Levels (2026-04-27T22:03:01), Daily Levels (2026-04-27T22:04:21), Daily Levels (2026-04-27T22:12:41), Daily Levels (2026-04-27T22:36:13), Daily Levels (2026-04-27T22:45:49), Daily Levels (2026-04-27T22:47:57), Daily Levels (2026-04-27T22:53:40) (+118 more)

### Community 49 - "BrokerStub"
Cohesion: 0.14
Nodes (3): BrokerStub, IntradaySyncTests, TestCase

### Community 50 - "IBKR Gerchik Bot"
Cohesion: 0.07
Nodes (26): Configuration, Current Limitations, Dashboard, Data And Jobs, Execution And Replay, Hard Filters, Inefficiency Reclaim Strategy, State And Audit (+18 more)

### Community 51 - "NewsService"
Cohesion: 0.14
Nodes (7): fx_pair_components(), normalize_symbol(), Settings, NewsService, Fetch symbol, macro, and earnings context from NewsAPI.ai / Event Registry., _build_research_symbols(), SymbolHandlingTests

### Community 52 - "Research Log"
Cohesion: 0.00
Nodes (422): Premarket Research (2026-04-27T22:02:49), Premarket Research (2026-04-27T22:03:01), Premarket Research (2026-04-27T22:04:21), Premarket Research (2026-04-27T22:12:41), Premarket Research (2026-04-27T22:36:13), Premarket Research (2026-04-27T22:45:49), Premarket Research (2026-04-27T22:47:57), Premarket Research (2026-04-27T22:53:40) (+414 more)

### Community 53 - "crypto/tradingview_webhook.py"
Cohesion: 0.19
Nodes (27): BackgroundTasks, api_crypto_tradingview_webhook(), _append_execution(), _as_float(), enqueue_tradingview_webhook(), _execution_from_signal(), _extract_okx_order_id(), _find_execution() (+19 more)

### Community 54 - "test_irs_scheduler.py"
Cohesion: 0.24
Nodes (7): _next_manifest_run(), Atomically publish IRS scheduler state for the Service Health dashboard., record_irs_runtime_status(), _installed_tasks(), IrsSchedulerTests, datetime, _SetupStoreStub

### Community 55 - "decision_log.py"
Cohesion: 0.22
Nodes (14): _acquire_daily_decisions_lock(), _apply_to_decisions(), _build_attempt(), _decision_rank(), _load_daily_decisions(), _lock_is_stale(), persist(), persist_decision() (+6 more)

### Community 56 - "symbols.py"
Cohesion: 0.15
Nodes (10): add_crypto_symbol(), _dedupe(), load_crypto_symbol_inputs(), Path, Normalize TradingView/OKX crypto symbols into OKX instrument ids., Remove every configured spelling that resolves to ``symbol``. Comments and…, Persist a crypto symbol input unless its normalized instrument exists. Returns…, remove_crypto_symbol() (+2 more)

### Community 57 - "ensure_irs_history"
Cohesion: 0.33
Nodes (11): Exception, ensure_irs_history(), history_specs(), IRSHistorySpec, _mark_skipped_remaining(), _paced_get_bars(), Any, Incremental historical-data hydration for IRS timeframes. (+3 more)

### Community 58 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 59 - "FakeMarketData"
Cohesion: 0.32
Nodes (5): _daily_bars(), _empty_bars(), FakeMarketData, IRSHistoryHydrationTests, DataFrame

### Community 61 - "build_task_scheduler_command"
Cohesion: 0.27
Nodes (8): build_all_task_scheduler_commands(), build_task_scheduler_command(), get_recommended_task_names(), Path, Helpers for wiring the bot into Windows Task Scheduler., Return a Task Scheduler command line for a specific job., Return concrete Task Scheduler commands for every job., Map jobs to recommended Task Scheduler task names.

### Community 62 - "OKXClient"
Cohesion: 0.24
Nodes (6): RuntimeError, OKXClient, Any, DataFrame, Place a SPOT order, optionally with attached market-exit TP/SL. Private…, OKXSpotOrderPayloadTests

### Community 63 - "IBKRClient"
Cohesion: 0.06
Nodes (25): _ensure_event_loop(), IBKRClient, IBKRDependencyError, Any, Interactive Brokers connectivity built on top of ib_insync., ib_insync/eventkit expects a current event loop on newer Python versions., Connect to IBKR TWS or IB Gateway with retry logic., Select live/frozen/delayed market data for this IBKR connection. (+17 more)

### Community 65 - "false_breakout.py"
Cohesion: 0.40
Nodes (4): detect_false_breakout(), DataFrame, False breakout detection — base module, intentionally disabled. This module…, Detect a level breach followed by immediate rejection. .. deprecated:: This…

### Community 66 - "nearest_level_details"
Cohesion: 0.19
Nodes (10): _coerce_float(), nearest_level_details(), datetime, Path, _quote_reference_price(), Excel exports for intraday scan outcomes., Write one intraday scan result workbook and return its path., Return the most relevant watchlist level for reporting. (+2 more)

### Community 67 - "market_data_collector.py"
Cohesion: 0.19
Nodes (9): _collector_session_bounds(), _collector_session_is_open(), _next_collector_open(), datetime, Independent intraday bar collector for dashboard continuity., Return the intraday collection window for the date represented by ``now``., Collect bars without account synchronization, signals, or orders., run_market_data_collector() (+1 more)

### Community 68 - "manual_order.py"
Cohesion: 0.10
Nodes (30): api_crypto_order_place(), api_crypto_order_simulate(), Submit a manual Strategy Crypto order to OKX Demo only., _client_order_id(), _decimal(), _decimal_text(), execute_manual_demo_order(), _floor_to_step() (+22 more)

### Community 69 - "broker_reconcile.py"
Cohesion: 0.10
Nodes (26): _execution_closed_record(), _execution_matches_request(), _now(), _order_ids(), _parse_dt(), _place_requests(), Any, datetime (+18 more)

### Community 70 - "Level"
Cohesion: 0.08
Nodes (40): _level_center(), _level_zone(), Describe whether current price has clean ATR room between nearby level zones., _spacing_context(), round_number_guard(), _acts_as_resistance(), _acts_as_support(), _atr_used() (+32 more)

### Community 71 - "Weekly Log"
Cohesion: 0.09
Nodes (21): Weekly Log, Weekly Metrics (2026-04-23T22:20:31), Weekly Metrics (2026-04-23T23:20:28), Weekly Review (2026-04-23T23:46:43), Weekly Review (2026-05-01T16:25:02), Weekly Review (2026-05-13T22:21:19), Weekly Review (2026-05-15T16:25:02), Weekly Review (2026-05-22T16:25:02) (+13 more)

### Community 73 - "StrategyCandidate"
Cohesion: 0.18
Nodes (17): _historical_analysis_candidate(), Remove live-only risk gates from an anchor-date analysis result., ConfirmationType, _json_value(), OrderPlan, Any, Enum, str (+9 more)

### Community 75 - "IRSBroker"
Cohesion: 0.17
Nodes (4): Protocol, IRSBroker, IRSExecutionPolicy, Any

### Community 76 - "should_trigger_kill_switch"
Cohesion: 0.31
Nodes (6): _equity_symbols(), Trading kill switch conditions., Block all trading when safety conditions are breached., should_trigger_kill_switch(), KillSwitchTests, Tests for kill switch behavior.

### Community 78 - "crypto/config.py"
Cohesion: 0.36
Nodes (7): CryptoConfig, _env_bool(), _env_csv(), _env_float(), _env_int(), _env_str(), Crypto bot configuration. API secrets should live in the local .env file only.…

### Community 81 - "third_touch.py"
Cohesion: 0.33
Nodes (6): detect_third_touch(), DataFrame, Series, Third touch setup detection., Detect the third qualified interaction with a level., _touch_indices()

### Community 88 - "add_bmsb_signals"
Cohesion: 0.40
Nodes (5): add_bmsb_signals(), main(), DataFrame, BMSB Strategy 1: Bull Market Support Band conversion. Long-only strategy on…, Add daily signals from completed weekly BMSB values.

### Community 89 - "analysis.py"
Cohesion: 0.13
Nodes (32): load_crypto_symbols(), api_crypto_add(), api_crypto_bulk_add(), Resolve user-friendly crypto input to an OKX instrument id. People often type…, _resolve_crypto_add_instrument(), analyze_symbol(), collect_crypto_bars(), configured_crypto_symbols() (+24 more)

### Community 90 - "MarketDataService"
Cohesion: 0.14
Nodes (9): main(), One-off backfill of OHLCV bars for the dashboard. Connects to TWS / IB Gateway…, MarketDataService, DataFrame, datetime, Fetch a timeframe for an incremental strategy cache., Provide reusable market data retrieval wrappers., Allow delayed data when the account lacks a live subscription. (+1 more)

### Community 91 - "server.py"
Cohesion: 0.09
Nodes (43): api_crypto_analyze(), api_crypto_remove(), api_forex_tradingview_webhook(), api_inefficiency_reclaim_settings(), api_meta(), api_order_close(), api_order_place(), api_orders_journal_review() (+35 more)

### Community 94 - "DESIGN.md"
Cohesion: 0.15
Nodes (12): Brand & Style, Buttons, Cards, Colors, Components, Data Visualization, Elevation & Depth, Input Fields (+4 more)

### Community 95 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 112 - "crypto/bar_store.py"
Cohesion: 0.35
Nodes (11): crypto_bar_path(), crypto_index_snapshot(), load_crypto_bars(), _normalize_frame(), DataFrame, Path, Crypto OHLCV CSV storage under memory/crypto/bars., _read_index() (+3 more)

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
Nodes (12): api_market_screener(), api_watchlist(), _bmsb_scan_symbol(), _bmsb_strategy2_scan_symbol(), _daily_live_frame(), disable_dashboard_cache(), DataFrame, Return weekly OHLCV with current-week daily/live data stitched in. (+4 more)

### Community 139 - "Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab, Source Nodes

## Knowledge Gaps
- **1085 isolated node(s):** `BrokerConfig`, `RiskConfig`, `StrategyConfig`, `TradingHours`, `NewsConfig` (+1080 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **32 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Level` connect `Level` to `src/main.py`, `forecast.py`, `false_breakout_one_bar.py`, `rebound.py`, `OrderManager`, `premarket.py`, `false_breakout_continuation.py`, `levels.py`, `session_utils.py`, `TradeSignal`, `decision_log.py`, `_make_level`, `atr.py`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **Why does `InefficiencyReclaimStore` connect `InefficiencyReclaimStore` to `timedelta`, `StrategyCandidate`, `IRSBroker`, `_safe`, `jobs/inefficiency_reclaim.py`, `execution/inefficiency_reclaim.py`, `server.py`, `scanners/inefficiency_reclaim.py`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **Why does `MarketDataService` connect `MarketDataService` to `symbol_onboarding.py`, `src/main.py`, `market_data_collector.py`, `MarketDataServiceTests`, `OrderManager`, `chart_history.py`, `premarket.py`, `NewsRiskFilter`, `session_utils.py`, `NasdaqDataClient`, `jobs/inefficiency_reclaim.py`, `ensure_irs_history`, `atr.py`, `IBKRClient`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **Are the 56 inferred relationships involving `Level` (e.g. with `_level_center()` and `_level_zone()`) actually correct?**
  _`Level` has 56 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `IBKRClient` (e.g. with `MarketDataService` and `NewsService`) actually correct?**
  _`IBKRClient` has 10 INFERRED edges - model-reasoned connections that need verification._
- **What connects `BrokerConfig`, `RiskConfig`, `StrategyConfig` to the rest of the system?**
  _1085 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `symbol_onboarding.py` be split into smaller, more focused modules?**
  _Cohesion score 0.13118279569892474 - nodes in this community are weakly interconnected._