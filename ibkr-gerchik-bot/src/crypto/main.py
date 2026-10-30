"""CLI entry point for the separate crypto bot."""

from __future__ import annotations

import argparse
import json
from typing import List

from src.crypto.analysis import collect_crypto_bars, run_crypto_analysis, validate_symbols
from src.crypto.symbols import normalize_okx_instrument
from src.crypto.worker import run_crypto_worker


def _symbols(values: List[str] | None) -> List[str] | None:
    if not values:
        return None
    return [normalize_okx_instrument(value) for value in values if normalize_okx_instrument(value)]


def _analysis_summary(payload: dict) -> dict:
    watchlist = payload.get("watchlist", {})
    ready = [symbol for symbol, row in watchlist.items() if isinstance(row, dict) and row.get("ready")]
    return {
        "updated_at": payload.get("updated_at"),
        "symbols": len(payload.get("symbols", [])),
        "ready": len(ready),
        "errors": payload.get("errors", []),
        "state_path": "memory/crypto/state.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OKX crypto bot utilities")
    parser.add_argument("--job", choices=["validate", "collect", "analyze", "collect_analyze", "worker"], required=True)
    parser.add_argument("--symbol", action="append", help="Optional symbol/instrument id. Repeat for multiple.")
    parser.add_argument("--validate", action="store_true", help="Validate configured instruments against OKX before collecting.")
    parser.add_argument("--once", action="store_true", help="For worker: run one cycle and exit.")
    parser.add_argument("--interval-seconds", type=int, default=None, help="For worker: candle collection cadence.")
    args = parser.parse_args()

    symbols = _symbols(args.symbol)
    if args.job == "validate":
        result = {"symbols": validate_symbols()}
    elif args.job == "collect":
        result = collect_crypto_bars(symbols, validate=args.validate)
    elif args.job == "analyze":
        result = _analysis_summary(run_crypto_analysis(symbols))
    elif args.job == "worker":
        if symbols:
            raise SystemExit("--symbol is not supported with --job worker; edit config/crypto_symbols.txt instead.")
        run_crypto_worker(
            interval_seconds=args.interval_seconds,
            once=args.once,
            validate=args.validate,
        )
        return 0
    else:
        collect = collect_crypto_bars(symbols, validate=args.validate)
        analysis = run_crypto_analysis(symbols)
        result = {"collect": collect, "analysis": _analysis_summary(analysis)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
