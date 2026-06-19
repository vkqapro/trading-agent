"""Tests for the dashboard's read-only risk forecast engine."""

import unittest

from dashboard.forecast import ForecastScenario, projected_level_candidates, run_forecast


def scenario(**overrides: float) -> ForecastScenario:
    values = {
        "account_equity": 100_000.0,
        "cash_available": 100_000.0,
        "daily_realized_pnl": 0.0,
        "assumed_spread_pct": 0.001,
        "risk_per_trade": 0.01,
        "max_daily_loss_pct": 0.02,
        "min_reward_risk_ratio": 3.0,
        "max_positions": 5,
        "max_spread_pct": 0.003,
        "max_open_risk_pct": 0.03,
        "max_position_value": 25_000.0,
    }
    values.update(overrides)
    return ForecastScenario(**values)


class DashboardForecastTests(unittest.TestCase):
    def test_allocates_trade_without_mutating_candidate(self) -> None:
        candidate = {
            "symbol": "AAPL",
            "strategy": "test",
            "source": "PROJECTED",
            "signal": "BUY",
            "direction": "long",
            "entry": 100.0,
            "stop": 99.0,
            "target": 104.0,
            "level": 100.0,
            "level_type": "historical",
            "reward_risk": 4.0,
            "risk_per_share": 1.0,
            "confidence": 80.0,
        }
        original = candidate.copy()
        result = run_forecast([candidate], scenario(max_position_value=200_000.0), [])
        self.assertEqual(result["summary"]["forecast_trades"], 1)
        self.assertEqual(result["accepted"][0]["quantity"], 1000)
        self.assertEqual(candidate, original)

    def test_filters_by_reward_risk_and_spread(self) -> None:
        candidate = {
            "symbol": "AAPL",
            "strategy": "test",
            "source": "PROJECTED",
            "signal": "BUY",
            "direction": "long",
            "entry": 100.0,
            "stop": 99.0,
            "target": 102.0,
            "level": 100.0,
            "level_type": "historical",
            "reward_risk": 2.0,
            "risk_per_share": 1.0,
            "confidence": 80.0,
        }
        result = run_forecast(
            [candidate],
            scenario(assumed_spread_pct=0.004),
            [],
        )
        self.assertEqual(result["summary"]["forecast_trades"], 0)
        self.assertIn("Reward:risk", result["rows"][0]["reason"])
        self.assertIn("spread", result["rows"][0]["reason"])

    def test_respects_open_risk_and_position_limits(self) -> None:
        candidates = [
            {
                "symbol": symbol,
                "strategy": "test",
                "source": "PROJECTED",
                "signal": "BUY",
                "direction": "long",
                "entry": 10.0,
                "stop": 9.0,
                "target": 14.0,
                "level": 10.0,
                "level_type": "historical",
                "reward_risk": 4.0,
                "risk_per_share": 1.0,
                "confidence": 80.0,
            }
            for symbol in ("AAA", "BBB")
        ]
        result = run_forecast(
            candidates,
            scenario(max_positions=1, max_position_value=200_000.0),
            [],
        )
        self.assertEqual(result["summary"]["forecast_trades"], 1)

    def test_builds_projected_level_candidate(self) -> None:
        watchlist = {
            "AAPL": {
                "daily_atr": 2.0,
                "news_blocked": False,
                "level_spacing": {"current_price": 101.0},
                "levels": [
                    {
                        "price": 100.0,
                        "zone_low": 99.8,
                        "zone_high": 100.2,
                        "nearest_upper_level": 104.0,
                        "nearest_lower_level": 96.0,
                        "type": "historical",
                        "strength_score": 10.0,
                    }
                ],
            }
        }
        candidates = projected_level_candidates(watchlist)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["signal"], "BUY")


if __name__ == "__main__":
    unittest.main()
