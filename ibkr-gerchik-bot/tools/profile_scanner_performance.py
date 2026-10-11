"""Profile the authoritative market screener without changing scanner logic.

This diagnostic wraps the existing scanner seams at runtime. It intentionally
does not modify production functions or persist any market data. Use it for a
repeatable before/after comparison against the current checkout:

    rtk python tools/profile_scanner_performance.py --mode direct --limit 196
    rtk python tools/profile_scanner_performance.py --mode radar --limit 196 --workers 3

The direct mode matches the market-screener endpoint. Radar mode matches the
Trend Radar batch callback and bounded worker pool. JSON is printed to stdout
so two snapshots can be compared without relying on brittle wall-clock tests.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class Profile:
    mode: str
    symbols: list[str]
    workers: int
    counts: Counter[str] = field(default_factory=Counter)
    durations: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    calls_by_symbol: defaultdict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    durations_by_symbol: defaultdict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    symbol_durations: dict[str, float] = field(default_factory=dict)
    signal_count: int = 0
    result: dict[str, Any] | None = None
    wall_clock_seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, stage: str, elapsed: float, symbol: str | None = None) -> None:
        with self._lock:
            self.counts[stage] += 1
            self.durations[stage] += elapsed
            if symbol:
                self.calls_by_symbol[symbol][stage] += 1
                self.durations_by_symbol[symbol][stage] += elapsed

    def snapshot(self) -> dict[str, Any]:
        aggregate_symbol_seconds = sum(self.symbol_durations.values())
        total = self.wall_clock_seconds or aggregate_symbol_seconds
        instrumented_total = sum(
            elapsed for stage, elapsed in self.durations.items() if stage != "scan:wall_clock"
        )
        stage_rows = []
        for stage in sorted(self.durations):
            if stage == "scan:wall_clock":
                continue
            elapsed = self.durations[stage]
            stage_rows.append(
                {
                    "stage": stage,
                    "calls": self.counts[stage],
                    "seconds": round(elapsed, 6),
                    "share_of_instrumented": round(elapsed / max(instrumented_total, 1e-9), 4),
                }
            )
        slowest = sorted(self.symbol_durations.items(), key=lambda item: item[1], reverse=True)[:10]
        symbols = max(len(self.symbols), 1)
        signals = int(self.signal_count)
        return {
            "mode": self.mode,
            "symbols_requested": len(self.symbols),
            "symbols_completed": len(self.symbol_durations),
            "workers": self.workers,
            "total_scan_seconds": round(total, 6),
            "aggregate_symbol_seconds": round(aggregate_symbol_seconds, 6),
            "average_seconds_per_symbol": round(aggregate_symbol_seconds / symbols, 6),
            "symbols_per_second": round(len(self.symbol_durations) / max(total, 1e-9), 6),
            "slowest_symbols": [
                {"symbol": symbol, "seconds": round(seconds, 6)} for symbol, seconds in slowest
            ],
            "stage_timings": stage_rows,
            "access_counts": {
                "daily_bar_loads": self.counts["storage:daily"],
                "five_minute_bar_loads": self.counts["storage:intraday_5m"],
                "bar_index_queries": self.counts["metadata:bar_index"],
                "symbol_metadata_queries": self.counts["metadata:watchlist"],
                "stock_symbol_universe_queries": self.counts["metadata:stock_symbols"],
                "five_minute_loads_per_signal": round(
                    self.counts["storage:intraday_5m"] / signals, 6
                )
                if signals
                else None,
                "five_minute_loads_per_symbol": round(
                    self.counts["storage:intraday_5m"] / max(len(self.symbol_durations), 1), 6
                ),
                "five_minute_loads_per_batch": self.counts["storage:intraday_5m"],
            },
            "signals_found": signals,
            "result_summary": _summarize_result(self.result),
        }


def _summarize_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return {
        "signals_found": result.get("signals_found"),
        "rejected": len(result.get("rejected") or []),
        "near_misses": len(result.get("near_misses") or []),
        "reason_counts": result.get("reason_counts") or {},
        "scanner_statuses": result.get("statuses"),
        "orders_submitted": result.get("orders_submitted"),
    }


def _symbols(limit: int) -> list[str]:
    from dashboard import data_access as da
    from src.symbol_universe import load_stock_symbols

    configured = load_stock_symbols()
    watchlist = da.load_watchlist() or {}
    values = sorted(set(configured) | set(watchlist))
    return values[: max(1, min(limit, len(values)))]


def _wrap(profile: Profile, local: threading.local, name: str, function: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            profile.add(name, time.perf_counter() - started, getattr(local, "symbol", None))

    return wrapped


def _close_symbol(profile: Profile, local: threading.local) -> None:
    started = getattr(local, "symbol_started", None)
    symbol = getattr(local, "symbol", None)
    if started is not None and symbol:
        elapsed = time.perf_counter() - started
        with profile._lock:
            profile.symbol_durations[symbol] = profile.symbol_durations.get(symbol, 0.0) + elapsed
    local.symbol_started = None


def _install_wrappers(profile: Profile, local: threading.local) -> None:
    import dashboard_react.market_screener as ms
    import dashboard_react.server as server
    from dashboard import data_access as da

    for name in ("_add_metrics", "_passes_filters", "_pivot_levels", "_detect_signals", "_make_signal"):
        original = getattr(ms, name)
        setattr(ms, name, _wrap(profile, local, f"scanner:{name[1:]}", original))

    original_lifecycle = ms._finalize_candidate_lifecycle
    ms._finalize_candidate_lifecycle = _wrap(profile, local, "lifecycle:chronology", original_lifecycle)

    original_loader = server._screener_frame

    def timed_loader(symbol: str, timeframe: str, asset: str = "stock") -> Any:
        _close_symbol(profile, local)
        local.symbol = symbol
        local.symbol_started = time.perf_counter()
        return _wrap(profile, local, "data:frame_loader", original_loader)(symbol, timeframe, asset)

    server._screener_frame = timed_loader

    original_radar_runner = server._run_authoritative_radar_scanner

    def timed_radar_runner(symbol: str) -> Any:
        try:
            return original_radar_runner(symbol)
        finally:
            _close_symbol(profile, local)

    server._run_authoritative_radar_scanner = timed_radar_runner

    original_get_bars = da.get_bars

    def counted_get_bars(symbol: str, timeframe: str) -> Any:
        normalized = str(timeframe)
        profile.add(f"storage:{normalized}", 0.0, str(symbol).upper())
        started = time.perf_counter()
        try:
            return original_get_bars(symbol, timeframe)
        finally:
            profile.durations[f"storage:{normalized}"] += time.perf_counter() - started

    da.get_bars = counted_get_bars

    original_watchlist = da.load_watchlist

    def counted_watchlist(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return original_watchlist(*args, **kwargs)
        finally:
            profile.add(
                "metadata:watchlist",
                time.perf_counter() - started,
                getattr(local, "symbol", None),
            )

    da.load_watchlist = counted_watchlist

    from src import symbol_universe

    original_symbols = symbol_universe.load_stock_symbols

    def counted_symbols(*args: Any, **kwargs: Any) -> Any:
        profile.add("metadata:stock_symbols", 0.0)
        return original_symbols(*args, **kwargs)

    symbol_universe.load_stock_symbols = counted_symbols


def _run(profile: Profile, *, legacy_pivot: bool = False) -> None:
    import dashboard_react.market_screener as ms
    import dashboard_react.server as server

    local = threading.local()
    if legacy_pivot:
        # The helper is the exact pre-optimization body. Calling it directly
        # intentionally reproduces the attrs deep-copy behavior for parity
        # measurement; production always uses the wrapper above.
        ms._pivot_levels = ms._pivot_levels_without_attrs
    _install_wrappers(profile, local)
    started = time.perf_counter()
    if profile.mode == "direct":
        from dashboard import data_access as da

        watchlist = da.load_watchlist() or {}
        profile.result = ms.run_market_screener(
            symbols=profile.symbols,
            bars_loader=lambda symbol, timeframe: server._screener_frame(symbol, timeframe, "stock"),
            watchlist=watchlist,
            params=ms.ScreenerParams(asset_class="stock", timeframe="1D"),
        )
    else:
        from src.radar.service import RadarService

        service = RadarService(scanner_runner=server._run_authoritative_radar_scanner)
        profile.result = service.scan_symbols(profile.symbols, max_workers=profile.workers)
    _close_symbol(profile, local)
    profile.wall_clock_seconds = time.perf_counter() - started
    if isinstance(profile.result, dict):
        serialization_started = time.perf_counter()
        json.dumps(profile.result, sort_keys=True, default=str)
        profile.add("serialization:result_json", time.perf_counter() - serialization_started)
        profile.signal_count = int(profile.result.get("signals_found") or 0)
        if profile.mode == "radar":
            profile.signal_count = sum(
                int(row.get("signals_found") or 0)
                for row in profile.result.get("results", [])
                if isinstance(row, dict)
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("direct", "radar"), default="direct")
    parser.add_argument("--limit", type=int, default=196)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--legacy-pivot", action="store_true", help="Profile the pre-optimization pivot body.")
    parser.add_argument("--result-out", type=Path, help="Write the complete JSON result to this path.")
    args = parser.parse_args()
    symbols = _symbols(args.limit)
    profile = Profile(mode=args.mode, symbols=symbols, workers=args.workers)
    _run(profile, legacy_pivot=args.legacy_pivot)
    snapshot = profile.snapshot()
    if args.result_out:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(json.dumps(profile.result, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(snapshot, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
