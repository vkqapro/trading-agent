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

    def test_mark_forecast_position_adds_labeled_price_lines(self) -> None:
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
        figure = charts.candles_with_levels(bars)
        charts.mark_forecast_position(figure, entry=101, stop=99, target=107)

        labels = [annotation.text for annotation in figure.layout.annotations]
        self.assertIn("ENTRY  $101.00", labels)
        self.assertIn("STOP  $99.00", labels)
        self.assertIn("TARGET  $107.00", labels)
        entry_shape = next(
            shape for shape in figure.layout.shapes
            if float(shape.y0) == 101 and float(shape.y1) == 101
        )
        entry_label = next(
            annotation for annotation in figure.layout.annotations
            if annotation.text == "ENTRY  $101.00"
        )
        stop_label = next(
            annotation for annotation in figure.layout.annotations
            if annotation.text == "STOP  $99.00"
        )
        target_label = next(
            annotation for annotation in figure.layout.annotations
            if annotation.text == "TARGET  $107.00"
        )
        self.assertEqual(entry_shape.line.dash, "dash")
        self.assertEqual(entry_shape.line.color, "#ffffff")
        self.assertEqual(entry_label.font.color, "#ffffff")
        self.assertEqual(stop_label.font.color, "#ffffff")
        self.assertEqual(target_label.font.color, "#ffffff")


if __name__ == "__main__":
    unittest.main()
