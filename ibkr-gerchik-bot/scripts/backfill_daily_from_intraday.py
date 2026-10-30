from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.bar_store import BARS_DIR
from src.data.chart_history import backfill_daily_from_intraday


def _symbol_from_intraday_file(path: Path) -> str:
    return path.name.split("__intraday_5m.csv", 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing daily OHLCV rows from saved 5-minute bars."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Session date to reconstruct, e.g. 2026-06-25.",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        help="Optional symbol to backfill. Repeat for multiple symbols. Defaults to all intraday files.",
    )
    args = parser.parse_args()

    if args.symbol:
        symbols = sorted({symbol.upper() for symbol in args.symbol})
    else:
        symbols = sorted(
            _symbol_from_intraday_file(path)
            for path in BARS_DIR.glob("*__intraday_5m.csv")
        )

    results = [
        backfill_daily_from_intraday(symbol, args.date)
        for symbol in symbols
    ]
    updated = [row for row in results if row.get("updated")]
    skipped = [row for row in results if not row.get("updated")]
    print(json.dumps({
        "date": args.date,
        "symbols_checked": len(symbols),
        "updated": len(updated),
        "skipped": len(skipped),
        "updated_symbols": [row["symbol"] for row in updated],
        "skipped_reasons": {
            reason: sum(1 for row in skipped if row.get("reason") == reason)
            for reason in sorted({str(row.get("reason")) for row in skipped})
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
