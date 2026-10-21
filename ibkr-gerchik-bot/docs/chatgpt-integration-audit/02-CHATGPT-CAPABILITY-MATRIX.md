# ChatGPT Capability Matrix

## Classification

`VERIFIED` means observed in this audit. `FEASIBLE` means supported by source or connector contract but not exercised here. `REQUIRES ADAPTER` means the capability exists only behind a local boundary or needs a narrow read-only facade. `UNSUPPORTED` means no supported path was found. `UNKNOWN` means evidence is insufficient.

| Capability | Evidence | Classification | Boundary |
|---|---|---|---|
| Read repository source with Codex | Repository accessible; graph query returned current graph with 8,432 nodes | VERIFIED | Codex session |
| Run bounded local terminal probes | Read-only probes completed; wrapper and sandbox constraints observed | VERIFIED | Codex/local desktop |
| Query local localhost APIs | GET-only endpoints returned 200 under approved elevated read-only probe | VERIFIED | Local desktop execution |
| Invoke persisted scanner data | Trading Bot MCP and Strategy Scanner MCP healthy; source exposes read-only tools | VERIFIED | Local MCP adapter |
| Start a continuous local service | Existing launchers and scheduled tasks exist | FEASIBLE | Windows lifecycle owner required |
| Maintain a persistent ChatGPT Work process | No evidence that interactive Work itself replaces resident services | UNSUPPORTED as a replacement | Keep local workers |
| Retrieve IBKR account balances, summary, positions, trades, orders | Connector exposes read-only tools for each | FEASIBLE | Account link selection required |
| Retrieve IBKR quotes and history | Connector exposes price snapshot/history and contract search | FEASIBLE | Account link and contract resolution |
| Distinguish connector paper/live account | Connector contract exposes account data, but this audit did not call it because two same-named account links were present and no account was selected | UNKNOWN for connected-link evidence | User must select intended link |
| Create IBKR order instruction | Connector exposes `create_order_instruction`; contract says it is not a live order and returns a review URL | FEASIBLE, not exercised | Human review in IBKR |
| Submit a live broker order through connector | No submit-order tool was exposed in the available connector tools | UNSUPPORTED in this environment | Existing local adapter is separate |
| Cancel or modify local broker orders through connector | No connector cancel/modify tool was exposed | UNSUPPORTED in this environment | Existing local `IBKRClient` remains authoritative |
| Execute Python strategy directly from ChatGPT | Strategies are local Python modules, not a safe general remote command surface | REQUIRES ADAPTER | Read-only API/MCP facade |
| Replace Risk Gate | ChatGPT can explain/review but source risk modules and gates are deterministic | UNSUPPORTED as a safe replacement | Local Risk Gate remains authoritative |
| Replace protective-stop monitoring | Requires continuous process and broker connectivity | UNSUPPORTED | Local worker remains required |
| Replace Hermes Daily Trader analysis | `DailyTraderService` already returns persisted read-only results and reports | FEASIBLE | ChatGPT can render/explain API output |
| Replace Hermes Pre-Trade Reviewer | Health and review boundary exist; ChatGPT can summarize, but review cannot authorize execution | FEASIBLE partially | Preserve read-only review semantics |
| Replace Trading Analyst | Reports are deterministic and persisted; ChatGPT can narrate and compare | FEASIBLE partially | Local source-backed metrics |
| Replace Trading Ops Supervisor | ChatGPT can run an interactive status check, not provide continuous supervision | FEASIBLE interactively; REQUIRES ADAPTER continuously | Keep local supervisor/heartbeat |
| Eliminate Telegram as command interface | Existing approval state machine and delivery/reconciliation are implemented | FEASIBLE only after a separately designed approval adapter | Do not remove during Phase 1 |
| Run recurring checks | Codex automations can wake a thread, but this is not equivalent to an always-on trading supervisor | FEASIBLE for reports; UNSUPPORTED for execution continuity | Local scheduler remains |

## IBKR connector findings

The connected IBKR tool surface exposes read-only account balances, account summary, positions, trades, orders, price snapshots, price history, and contract search. It also exposes order-instruction creation. The instruction contract explicitly distinguishes an instruction from a live order: the resulting link requires review and submission in IBKR. No submit, cancel, or modify broker-order tool was present in the available tool inventory.

The connector listed two links with the same display name. Because the target account was not unambiguously identified as the intended paper account, no account positions, balances, trades, or orders were fetched. This is a deliberate unresolved item, not a claim that the connector cannot access them.

## Recommended capability boundary

ChatGPT Work/Codex should be allowed to read:

- health and readiness summaries;
- latest completed scans by explicit scan ID;
- persisted Radar and scanner setup data;
- deterministic reports and audit records;
- broker state only through an explicitly selected, read-only account link.

It should not receive broad permission to call POST dashboard routes, create approval records, create order instructions, submit orders, cancel orders, refresh Radar, or trigger scans. A review response is not an approval and an `ENTRY_TRIGGERED` lifecycle state is not execution.
