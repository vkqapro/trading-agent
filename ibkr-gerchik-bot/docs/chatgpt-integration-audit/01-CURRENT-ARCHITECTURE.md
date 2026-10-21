# Current Architecture

## Audit scope

This is a read-only audit of the repository at `C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot`. Source inspection, graph traversal, GET-only localhost probes, and the repository's read-only `service-status` operation were used. No code, configuration, database, broker order, approval, scan, or service lifecycle state was changed.

Runtime evidence was collected on 2026-10-20 around 21:00 UTC. Secrets and exact account identifiers are intentionally omitted.

## System map

| Boundary | Authoritative implementation | Inputs | Outputs | Hermes-independent? |
|---|---|---|---|---|
| Configuration and safety | `src/config.py`, `.env.template`, `ops/trading_platform_ops.py` | paper/live flags, dry-run, allowlists, risk limits | `SETTINGS`, startup safety decision | Yes |
| Market data | `src/brokers/ibkr.py`, `src/jobs/market_data_collector.py`, `src/data/*` | IBKR Gateway/TWS, persisted bars | bars, quotes, heartbeats, data-quality artifacts | Yes, but IBKR Gateway remains a runtime dependency |
| Level strategies | `src/strategy/levels.py`, `backtest_engine_v24.4/scanner/adapters/lp_prb.py`, `backtest_engine_v24.4/scanner/registry.py` | completed bars, levels, strategy parameters | LP1, LP2, PRB1, PRB2 signals and plans | Yes |
| Production scanner | `backtest_engine_v24.4/scanner/runner.py`, `backtest_engine_v24.4/scanner/run_scan.py`, `backtest_engine_v24.4/scanner_mcp/service.py` | persisted data, symbols, strategy selection | scan artifacts, candidate proposals, lifecycle evidence | Yes |
| Trend Radar | `src/radar/engine.py`, `src/radar/service.py`, `src/storage/trend_radar_store.py` | daily bars, scanner result | trend, breakout, momentum, liquidity, Radar score, scanner context | Yes |
| Daily Trader | `src/daily_trader.py`, dashboard routes near `dashboard_react/server.py:2880` | persisted Radar and scanner results | read-only summaries, persisted setups, market reports | Yes; Hermes can consume it |
| Risk and planning | `src/risk/*`, `src/strategy/validator.py` | signal, account equity, risk settings, quote | validation result, stop, target, quantity, rejection reasons | Yes |
| Execution | `src/execution/order_manager.py`, `src/brokers/ibkr.py` | validated signal, paper/live gates, IBKR state | bracket orders, order results, audit payloads | Yes, but must remain local |
| Broker reconciliation | `src/journal/broker_reconcile.py`, `src/brokers/ibkr.py` | broker executions, local journal | reconciled fills, recovery state | Yes |
| Approval transport | `src/decision/telegram_approval.py`, `src/mcp/trading_bot_server.py` | canonical candidate, Telegram delivery events | durable approval state machine | Hermes/Telegram-specific at the transport edge |
| Data MCP | `src/mcp/trading_bot_server.py`, port 8765 | persisted local data | symbol snapshot/history/levels/charts and approval-state reads | Yes |
| Scanner MCP | `backtest_engine_v24.4/scanner_mcp/server.py`, port 8766 | persisted bars and scanner artifacts | deterministic scanner tools and live preview | Yes |
| Presentation | `dashboard_react/server.py`, `dashboard/*` | local files, APIs, persisted state | dashboard and operational endpoints | Yes |
| Operations | `ops/trading_platform_ops.py`, `tools/trading_platform_ops.py` | process tree, heartbeats, APIs, broker identity, DBs | readiness and bounded status report | Yes |

## Authoritative lifecycle

The verified lifecycle is:

`IBKR market data -> persisted bars -> level/strategy scanner -> Radar annotation -> Daily Trader persisted setup -> candidate identity and revalidation -> risk validation -> local OrderManager -> IBKR bracket -> journal/reconciliation -> position monitoring/reporting`.

The scanner remains authoritative for strategy status and setup identity. `src/radar/service.py` explicitly computes `direction_conflict` and preserves Radar trend separately from scanner side. A Radar `GREEN` trend or breakout is informational context; it does not create an executable setup and cannot override a scanner `NO_SETUP`, invalidation, data-quality failure, or Risk Gate rejection.

The current persisted output demonstrated this distinction: on 2026-10-20, Radar returned a bullish `GREEN` breakout for `MSCI`, while the authoritative scanner status was `NO_SETUP` with no signal. Separately, the persisted setup endpoint contained two active candidates and kept scanner status, strategy, side, lifecycle status, planned entry, actual entry, stop, target, and source scan identity as separate fields.

## Strategies and risk

The production strategy set exposed by the scanner is `lp1`, `lp2`, `prb1`, and `prb2`. The repository also contains ATR-based stop and position-size calculations in `src/risk/stop_loss.py`, `src/risk/position_size.py`, `src/risk/order_planner.py`, and `src/risk/risk_manager.py`. The 30-minute and other timeframe data paths are exposed by the data and dashboard layers; the scanner MCP has persisted-data live preview and does not call the broker or order path.

`OrderManager` delegates broker operations to `IBKRClient`. The broker adapter exposes account identity, positions, open orders, executions, market data, cancellation, market orders, limit orders, stop orders, and bracket-order variants. These are executable capabilities and therefore must remain behind local paper/live gates and reconciliation. The execution path is not an appropriate direct ChatGPT control surface.

## Local APIs and MCP

The following GET endpoints were verified live through the dashboard:

- `/api/market-screener/health`
- `/api/daily-trader/health`
- `/api/pre-trade-review/health`
- `/api/radar/top?limit=3`
- `/api/daily-trader/scans/latest`
- `/api/daily-trader/setups`

The read-only Trading Bot MCP health endpoint is `/health` on `127.0.0.1:8765`. Its implementation labels the mode `data_read_only_approval_state_machine`; it also contains approval functions, so tool-level allowlisting is required. The Strategy Scanner MCP health endpoint is `/health` on `127.0.0.1:8766` and reports `deterministic_analysis`.

The dashboard also exposes mutating POST routes, including Radar refresh/scan, daily scan submission, order placement/close, watchlist changes, and system controls. ChatGPT must receive a read-only route/tool allowlist rather than broad dashboard access.

## Runtime state at audit time

The escalated, read-only `service-status` check reported:

- Paper IBKR Gateway connected and paper identity verified; live trading disabled.
- Trading Bot MCP, Strategy Scanner MCP, Dashboard, Radar, Production Scanner, Daily Trader, and Pre-Trade Reviewer healthy.
- Market-data collector healthy with a fresh heartbeat and matching lock owner, but data quality was not fully clean: 389 received of 393 requested, one error, and three no-data symbols in the status artifact.
- Execute Worker stopped/missing with a stale heartbeat. This is the primary readiness issue.
- Autonomous and crypto workers reported healthy, but they are optional to the stock readiness boundary.
- `orders_submitted=0`, `approvals_created=0`, and `execution_intents_created=0`.

The production scanner's latest artifact was readable but `COMPLETED_WITH_ERRORS`: 95 of 96 requested symbols scanned, one skipped for `NO_PERSISTED_DAILY_DATA`, and one candidate returned. This is usable evidence, not a clean full-universe pass.

## Persistence and auditability

SQLite and JSON artifacts under `memory/`, scanner run artifacts, trend-Radar storage, runtime heartbeats, order journals, broker reconciliation records, and report directories provide durable state. `ops/trading_platform_ops.py` uses read-only SQLite checks for several operational probes. The repository's research-report contract also requires evidence-backed metrics and forbids fabricating missing fills or market data.

## Main architectural conclusion

The platform already has the deterministic execution boundary needed for a ChatGPT-native analytical layer. The safe integration point is read-only APIs/MCP over persisted data plus explicitly bounded operational probes. Hermes can be reduced at the presentation/orchestration edge, but local market data, scanner, risk, execution, protective-stop, reconciliation, and continuous worker processes remain necessary.
