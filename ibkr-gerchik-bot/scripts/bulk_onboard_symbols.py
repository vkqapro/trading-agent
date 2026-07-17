from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import SETTINGS
from src.symbol_universe import normalize_stock_symbol


def _client_id(symbol: str, index: int) -> str:
    seed = sum((idx + 1) * ord(char) for idx, char in enumerate(symbol.upper()))
    return str(1000 + ((seed + index * 97) % 8000))


def _load_symbols(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for raw in row:
            value = str(raw or "").strip()
            if not value or value.lower() in {"symbol", "symbols", "ticker", "tickers"}:
                continue
            try:
                symbol = normalize_stock_symbol(value)
            except ValueError:
                print(f"[skip] invalid symbol: {value}", flush=True)
                continue
            if symbol not in seen:
                seen.add(symbol)
                out.append(symbol)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequentially onboard uploaded stock symbols.")
    parser.add_argument("--symbols-file", required=True)
    args = parser.parse_args()
    symbols_path = Path(args.symbols_file)
    symbols = _load_symbols(symbols_path)
    print(f"[bulk] {datetime.now().isoformat(timespec='seconds')} symbols={symbols}", flush=True)
    if not symbols:
        print("[bulk] no valid symbols to onboard", flush=True)
        return 0

    for index, symbol in enumerate(symbols, start=1):
        client_id = _client_id(symbol, index)
        cmd = [
            sys.executable,
            "-m",
            "src.main",
            "--job",
            "onboard_symbol",
            "--symbol",
            symbol,
            "--client-id",
            client_id,
        ]
        print(f"[bulk] {index}/{len(symbols)} onboarding {symbol} client_id={client_id}", flush=True)
        result = subprocess.run(cmd, cwd=str(ROOT), text=True)
        print(f"[bulk] {symbol} exit_code={result.returncode}", flush=True)
        if result.returncode != 0:
            print(f"[bulk] warning: onboarding failed for {symbol}; continuing", flush=True)

    premarket_client_id = "31"
    premarket_cmd = [
        sys.executable,
        "-m",
        "src.main",
        "--job",
        "premarket",
        "--dry-run",
        "--client-id",
        premarket_client_id,
    ]
    print(f"[bulk] refreshing premarket context client_id={premarket_client_id}", flush=True)
    premarket = subprocess.run(premarket_cmd, cwd=str(ROOT), text=True)
    print(f"[bulk] premarket exit_code={premarket.returncode}", flush=True)
    if premarket.returncode != 0:
        print("[bulk] warning: final premarket refresh failed", flush=True)

    print(f"[bulk] complete; state={SETTINGS.paths.state_file}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
