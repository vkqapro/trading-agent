"""Research-only orchestration over persisted journal data and candle evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .candle_reader import CandleReader
from .compare import winner_loser_comparison
from .journal_reader import OrderJournalReader
from .metrics import derive_trade_metrics, summarize
from .models import ResearchTrade
from .strategy_library import StrategyLibrary
from .timezones import EASTERN


LOSS_CLASSIFICATION_VOCABULARY = (
    "ENTRY_QUALITY",
    "LATE_ENTRY",
    "EARLY_ENTRY",
    "SETUP_INVALIDATION",
    "STOP_PLACEMENT",
    "TARGET_PLACEMENT",
    "EXIT_MANAGEMENT",
    "MARKET_REGIME",
    "LOW_VOLUME",
    "HIGH_VOLATILITY",
    "EXECUTION_SLIPPAGE",
    "INSUFFICIENT_LIQUIDITY",
    "NO_CLEAR_CAUSE",
)


@dataclass(frozen=True)
class ReviewRequest:
    type: str = "trade_review"
    from_date: date | None = None
    to_date: date | None = None
    strategies: tuple[str, ...] = field(default_factory=tuple)
    side: str | None = None
    symbol: str | None = None
    statuses: tuple[str, ...] = field(default_factory=tuple)
    outcome: str = "all"
    trade_limit: int = 200

    def normalized_strategies(self) -> set[str]:
        return {item.strip().upper() for item in self.strategies if item.strip()}


def confidence_guidance(count: int) -> dict[str, str]:
    if count < 5:
        return {"level": "DESCRIPTIVE_ONLY", "guidance": "Fewer than 5 comparable trades; describe facts only."}
    if count < 20:
        return {"level": "LOW", "guidance": "5-19 comparable trades; low confidence. Correlation does not establish causation."}
    if count < 50:
        return {"level": "MODERATE", "guidance": "20-49 comparable trades; moderate confidence only if effects are consistent."}
    return {"level": "VALIDATION_STILL_REQUIRED", "guidance": "50+ comparable trades can support stronger hypotheses, but validation is still required."}


class TradingResearchService:
    """Coordinates deterministic analysis; it has no broker or mutation capability."""

    def __init__(
        self,
        journal_reader: OrderJournalReader | None = None,
        candle_reader: CandleReader | None = None,
        strategy_library: StrategyLibrary | None = None,
    ):
        self.journal_reader = journal_reader or OrderJournalReader()
        self.candle_reader = candle_reader or CandleReader()
        self.strategy_library = strategy_library or StrategyLibrary()

    def trade_review(self, request: ReviewRequest) -> dict[str, Any]:
        trades = [trade for trade in self.journal_reader.read() if self._matches(trade, request)]
        records: list[dict[str, Any]] = []
        missing_fields: Counter[str] = Counter()
        for trade in trades:
            context = self.candle_reader.market_context(trade)
            derived = derive_trade_metrics(trade, context)
            record = trade.to_dict()
            record.update(derived)
            record["trade_id"] = trade.trade_id
            if not record["market_context_available"]:
                missing_fields["market_context"] += 1
            for field in trade.missing_fields:
                missing_fields[field] += 1
            records.append(record)
        records.sort(key=lambda item: item.get("exit_time") or item.get("entry_time") or item.get("created_at") or "")
        performance_records = [record for record in records if record["performance_eligible"]]
        if request.outcome != "all":
            performance_records = [record for record in performance_records if self._outcome_matches(record, request.outcome)]
        by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in performance_records:
            by_strategy[str(record["strategy"])] .append(record)
        strategies = {
            strategy: {
                "metrics": summarize(items),
                "confidence": confidence_guidance(len(items)),
                "winner_loser_comparison": winner_loser_comparison(items),
            }
            for strategy, items in sorted(by_strategy.items())
        }
        sessions = {
            session: summarize([item for item in performance_records if item.get("entry_session") == session])
            for session in ("morning", "afternoon")
        }
        status_counts = Counter(str(record["status"]) for record in records)
        group_counts = Counter(str(record["status_group"]) for record in records)
        warnings: list[str] = []
        if group_counts.get("ambiguous"):
            warnings.append(f"{group_counts['ambiguous']} ambiguous journal record(s) were excluded from realized performance.")
        nonexecuted = group_counts.get("non_executed", 0)
        if nonexecuted:
            warnings.append(f"{nonexecuted} non-executed journal record(s) were excluded from realized performance.")
        if missing_fields.get("strategy"):
            warnings.append(f"{missing_fields['strategy']} trade(s) are labeled strategy_unknown.")
        if missing_fields.get("market_context"):
            warnings.append(f"{missing_fields['market_context']} trade(s) have no reconstructable saved 5-minute candle context.")
        displayed = performance_records[: max(1, request.trade_limit)]
        if len(displayed) < len(performance_records):
            warnings.append(f"Per-trade output is bounded to {len(displayed)} records; {len(performance_records) - len(displayed)} additional analyzable records were aggregated only.")
        return {
            "schema_version": "1.0",
            "request": {
                "type": request.type,
                "from": request.from_date.isoformat() if request.from_date else None,
                "to": request.to_date.isoformat() if request.to_date else None,
                "strategies": sorted(request.normalized_strategies()),
                "side": request.side.upper() if request.side else None,
                "symbol": request.symbol.upper() if request.symbol else None,
                "statuses": [status.upper() for status in request.statuses],
                "outcome": request.outcome,
            },
            "data_quality": {
                "complete": not missing_fields and not warnings,
                "missing_fields": sorted(missing_fields),
                "missing_field_counts": dict(sorted(missing_fields.items())),
                "warnings": warnings,
                "coverage": {
                    "journal_rows_selected": len(records),
                    "performance_eligible": len(performance_records),
                    "market_context_available": sum(1 for item in records if item["market_context_available"]),
                    "excluded_or_ambiguous": len(records) - len([item for item in records if item["performance_eligible"]]),
                },
            },
            "summary": summarize(performance_records),
            "strategies": strategies,
            "comparisons": {
                strategy: details["winner_loser_comparison"]
                for strategy, details in strategies.items()
                if details["winner_loser_comparison"] is not None
            },
            "entry_session_metrics": sessions,
            "journal_statuses": {
                "by_status": dict(sorted(status_counts.items())),
                "by_group": dict(sorted(group_counts.items())),
                "mapping": {
                    "active": ["OPEN", "SUBMITTED", "OPEN_ALERT", "CLOSE_ALERT"],
                    "completed": ["CLOSED", "CLOSED_STOP", "CLOSED_TARGET", "CLOSED_OBSERVED with fill evidence"],
                    "non_executed": ["UNFILLED", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED", "FAILED", "ERROR"],
                    "ambiguous": ["CLOSED_UNKNOWN"],
                    "excluded_from_pnl": ["active", "non_executed", "ambiguous", "completed rows without P&L or fill evidence"],
                },
            },
            "loss_classification": {
                "vocabulary": list(LOSS_CLASSIFICATION_VOCABULARY),
                "rule": "Candidates are deterministic evidence tags, not Hermes interpretations. Do not persist them outside the research workspace.",
            },
            "confidence": confidence_guidance(len(performance_records)),
            "trades": displayed,
            "trades_omitted_count": max(0, len(performance_records) - len(displayed)),
            "artifacts": [],
        }

    def strategy_review(self, request: ReviewRequest) -> dict[str, Any]:
        return self.trade_review(ReviewRequest(**{**request.__dict__, "type": "strategy_review"}))

    def list_strategies(self, strategy: str | None = None) -> dict[str, Any]:
        if strategy:
            return {"schema_version": "1.0", "strategy": self.strategy_library.get(strategy, include_documentation=True)}
        return {"schema_version": "1.0", "strategies": self.strategy_library.list()}

    def _matches(self, trade: ResearchTrade, request: ReviewRequest) -> bool:
        strategies = request.normalized_strategies()
        if strategies and trade.strategy.upper() not in strategies:
            return False
        if request.side and trade.side.upper() != request.side.upper():
            return False
        if request.symbol and trade.symbol.upper() != request.symbol.upper():
            return False
        if request.statuses:
            requested = {status.upper() for status in request.statuses}
            if trade.status.upper() not in requested and not ("CLOSED" in requested and trade.status.upper().startswith("CLOSED")):
                return False
        event_time = trade.exit_time or trade.entry_time or trade.created_at
        if request.from_date and (event_time is None or event_time.date() < request.from_date):
            return False
        if request.to_date and (event_time is None or event_time.date() > request.to_date):
            return False
        return True

    @staticmethod
    def _outcome_matches(record: dict[str, Any], outcome: str) -> bool:
        pnl = record.get("pnl")
        if pnl is None:
            return False
        if outcome == "win":
            return pnl > 0
        if outcome == "loss":
            return pnl < 0
        if outcome == "breakeven":
            return pnl == 0
        return True


def trailing_date_range(days: int) -> tuple[date, date]:
    if days <= 0:
        raise ValueError("days must be positive")
    today = datetime.now(EASTERN).date()
    return today - timedelta(days=days - 1), today

