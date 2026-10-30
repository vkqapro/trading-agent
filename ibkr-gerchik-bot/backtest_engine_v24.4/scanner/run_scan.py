"""CLI entry point: python backtest_engine_v24.4/scanner/run_scan.py"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from scanner.runner import run_universe_scan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic LP1/LP2/PRB1/PRB2 scan on persisted closed daily bars")
    parser.add_argument("--symbols", nargs="*", help="Optional symbol subset; defaults to configured Trading Bot universe")
    parser.add_argument("--as-of", help="Optional inclusive candle timestamp cutoff")
    parser.add_argument("--runs-root", type=Path, help="Optional artifact root (defaults to scanner/runs)")
    args = parser.parse_args()
    result = run_universe_scan(symbols=args.symbols or None, as_of=args.as_of, runs_root=args.runs_root)
    print(json.dumps({"run_dir": result["run_dir"], **result["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
