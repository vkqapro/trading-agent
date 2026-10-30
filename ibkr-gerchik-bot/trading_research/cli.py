"""Command-line interface for the local, read-only Trading Research Analyst."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from .analytics_service import ReviewRequest, TradingResearchService, trailing_date_range
from .artifacts import write_review_artifact


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from exc


def _add_filters(parser: argparse.ArgumentParser, *, strategy_required: bool = False) -> None:
    parser.add_argument("--from", dest="from_date", type=_date, help="Inclusive ET date, YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", type=_date, help="Inclusive ET date, YYYY-MM-DD")
    parser.add_argument("--strategy", action="append", default=[], required=strategy_required, help="Strategy identifier; repeat for comparisons")
    parser.add_argument("--side", choices=("LONG", "SHORT", "BUY", "SELL"))
    parser.add_argument("--symbol")
    parser.add_argument("--status", action="append", default=[], help="Journal status, e.g. CLOSED or CLOSED_OBSERVED")
    parser.add_argument("--outcome", choices=("all", "win", "loss", "breakeven"), default="all")
    parser.add_argument("--trade-limit", type=int, default=200, help="Maximum detailed records; aggregates always use all selected rows")
    parser.add_argument("--write-artifact", action="store_true", help="Write this JSON only under C:\\AI\\TradingResearch\\workspace\\exports")
    parser.add_argument("--artifact-name", help="Optional JSON filename under workspace/exports")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Trading Research Analyst")
    commands = parser.add_subparsers(dest="command", required=True)
    trade = commands.add_parser("trade-review", help="Analyze closed journal records over an ET date range")
    _add_filters(trade)
    strategy = commands.add_parser("strategy-review", help="Analyze one or more strategies")
    _add_filters(strategy, strategy_required=True)
    strategy.add_argument("--days", type=int, help="Trailing inclusive ET days when --from/--to are omitted")
    library = commands.add_parser("list-strategies", help="List or read analysis-only strategy documentation")
    library.add_argument("--strategy", help="Return one strategy document")
    return parser


def _request_from_args(args: argparse.Namespace, command: str) -> ReviewRequest:
    from_date, to_date = args.from_date, args.to_date
    if command == "strategy-review" and args.days is not None:
        if from_date or to_date:
            raise ValueError("Use either --days or --from/--to, not both")
        from_date, to_date = trailing_date_range(args.days)
    if (from_date is None) != (to_date is None):
        raise ValueError("--from and --to must be supplied together")
    if from_date and to_date and from_date > to_date:
        raise ValueError("--from must not be after --to")
    if args.trade_limit <= 0:
        raise ValueError("--trade-limit must be positive")
    return ReviewRequest(
        type=command.replace("-", "_"),
        from_date=from_date,
        to_date=to_date,
        strategies=tuple(args.strategy),
        side=args.side,
        symbol=args.symbol,
        statuses=tuple(args.status),
        outcome=args.outcome,
        trade_limit=args.trade_limit,
    )


def main(argv: list[str] | None = None) -> int:
    if not sys.dont_write_bytecode:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "error": {
                        "type": "RuntimeSafetyError",
                        "message": "Run the Trading Research CLI with Python -B to prevent repository bytecode-cache writes.",
                    },
                }
            ),
            file=sys.stderr,
        )
        return 2
    args = build_parser().parse_args(argv)
    service = TradingResearchService()
    try:
        if args.command == "list-strategies":
            result = service.list_strategies(args.strategy)
        else:
            request = _request_from_args(args, args.command)
            result = service.strategy_review(request) if args.command == "strategy-review" else service.trade_review(request)
            if args.write_artifact:
                artifact = write_review_artifact(result, name=args.artifact_name)
                result["artifacts"].append({"type": "json", "path": str(artifact)})
    except (ValueError, PermissionError, KeyError) as exc:
        print(json.dumps({"schema_version": "1.0", "error": {"type": exc.__class__.__name__, "message": str(exc)}}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
