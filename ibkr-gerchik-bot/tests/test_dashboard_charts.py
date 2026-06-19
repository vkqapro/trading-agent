from __future__ import annotations

import unittest

import pandas as pd

from dashboard import charts


class DashboardChartTests(unittest.TestCase):
    def test_chart_adds_candles_volume_levels_and_current_price(self) -> None:
        bars = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-06-18", "2026-06-19"]),
                "open": [100, 102],
                "high": [103, 104],
                "low": [99, 101],
                "close": [102, 103],
                "volume": [1000, 1200],
            }
        )
        figure = charts.candles_with_levels(
            bars,
            raw_levels=[{"price": 98}],
            trade_levels=[
                {
                    "price": 105,
                    "zone_low": 104.5,
                    "zone_high": 105.5,
                    "touches": 3,
                    "strength_score": 5,
                }
            ],
            current_price=103,
            show_raw=True,
            show_trade=True,
            show_volume=True,
        )

        self.assertEqual(len(figure.data), 2)
        self.assertGreaterEqual(len(figure.layout.shapes), 4)
        self.assertEqual(figure.layout.paper_bgcolor, charts.PAPER)

    def test_mark_levels_deduplicates_prices_and_ignores_invalid_values(self) -> None:
        figure = charts.candles_with_levels(pd.DataFrame(), show_volume=False)
        charts.mark_levels(figure, [100, 100, "bad", 101])

        self.assertEqual(len(figure.layout.shapes), 2)


if __name__ == "__main__":
    unittest.main()
