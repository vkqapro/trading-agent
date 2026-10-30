"""Read persisted candle CSVs without importing production write-capable modules."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .config import ProductionDataPaths
from .journal_reader import parse_timestamp
from .models import Candle, ResearchTrade, decimal_or_none
from .safety import assert_read_only_source


def _safe_symbol(symbol: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in symbol.upper())


class CandleReader:
    """Read the existing ``memory/bars/<SYMBOL>__<timeframe>.csv`` cache."""

    def __init__(self, paths: ProductionDataPaths | None = None, bars: Mapping[tuple[str, str], Iterable[Candle]] | None = None):
        self.paths = paths or ProductionDataPaths()
        self._bars = {
            (symbol.upper(), timeframe): sorted(list(values), key=lambda candle: candle.timestamp)
            for (symbol, timeframe), values in (bars or {}).items()
        }

    def read(self, symbol: str, timeframe: str = "intraday_5m") -> list[Candle]:
        key = (symbol.upper(), timeframe)
        if key in self._bars:
            return list(self._bars[key])
        path = self.paths.bars_dir / f"{_safe_symbol(symbol)}__{timeframe}.csv"
        assert_read_only_source(path, repository_root=self.paths.repository_root)
        if not path.exists() or not path.is_file():
            return []
        rows: list[Candle] = []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    timestamp = parse_timestamp(row.get("date"))
                    open_price = decimal_or_none(row.get("open"))
                    high = decimal_or_none(row.get("high"))
                    low = decimal_or_none(row.get("low"))
                    close = decimal_or_none(row.get("close"))
                    if timestamp is None or None in {open_price, high, low, close}:
                        continue
                    rows.append(Candle(timestamp, open_price, high, low, close, decimal_or_none(row.get("volume"))))
        except (OSError, csv.Error):
            return []
        return sorted(rows, key=lambda candle: candle.timestamp)

    def market_context(self, trade: ResearchTrade) -> dict[str, object]:
        """Return bounded deterministic candle evidence around a completed trade."""
        if trade.entry_time is None or trade.exit_time is None or trade.exit_time < trade.entry_time:
            return {"market_context_available": False, "reason": "entry_or_exit_time_unavailable", "candles": []}
        all_bars = self.read(trade.symbol)
        in_trade = [bar for bar in all_bars if trade.entry_time <= bar.timestamp <= trade.exit_time]
        if not in_trade:
            return {"market_context_available": False, "reason": "no_saved_intraday_5m_candles", "candles": []}
        prior = [bar for bar in all_bars if bar.timestamp <= trade.entry_time]
        return {
            "market_context_available": True,
            "timeframe": "intraday_5m",
            "bars_available": len(all_bars),
            "bars_in_trade": len(in_trade),
            "pre_entry_bars": prior[-14:],
            "candles": in_trade,
        }


def average_true_range(candles: Iterable[Candle], period: int = 14) -> Decimal | None:
    values = list(candles)[-period:]
    if len(values) < 2:
        return None
    ranges: list[Decimal] = []
    prior_close: Decimal | None = None
    for candle in values:
        options = [candle.high - candle.low]
        if prior_close is not None:
            options.extend((abs(candle.high - prior_close), abs(candle.low - prior_close)))
        ranges.append(max(options))
        prior_close = candle.close
    return sum(ranges, Decimal("0")) / Decimal(len(ranges)) if ranges else None

