from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from dashboard_react.server import (
    SETTINGS,
    api_inefficiency_reclaim,
    _parse_irs_min_daily_history_rows,
    _parse_irs_minimum_display_score,
    _write_env_setting,
    api_inefficiency_reclaim_settings,
)


class DashboardIrsSettingsTests(unittest.TestCase):
    def test_write_env_setting_preserves_other_values_and_comment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "SECRET_VALUE=leave-this-alone\n"
                "IRS_MIN_DAILY_HISTORY_ROWS=1000 # scanner threshold\n",
                encoding="utf-8",
            )

            _write_env_setting(
                env_path,
                "IRS_MIN_DAILY_HISTORY_ROWS",
                "750",
            )

            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "SECRET_VALUE=leave-this-alone\n"
                "IRS_MIN_DAILY_HISTORY_ROWS=750 # scanner threshold\n",
            )

    def test_parse_minimum_rejects_fractional_and_too_small_values(self) -> None:
        self.assertEqual(_parse_irs_min_daily_history_rows("1000"), 1000)
        with self.assertRaises(ValueError):
            _parse_irs_min_daily_history_rows(19)
        with self.assertRaises(ValueError):
            _parse_irs_min_daily_history_rows(1000.5)

    def test_display_score_defaults_to_80_and_respects_order_threshold(self) -> None:
        original = SETTINGS.inefficiency_reclaim
        object.__setattr__(
            SETTINGS,
            "inefficiency_reclaim",
            replace(original, minimum_display_score=80.0, minimum_order_score=85.0),
        )
        try:
            settings = api_inefficiency_reclaim_settings()
        finally:
            object.__setattr__(SETTINGS, "inefficiency_reclaim", original)

        self.assertEqual(settings["minimum_display_score"], 80.0)
        self.assertEqual(_parse_irs_minimum_display_score("80"), 80.0)
        with self.assertRaises(ValueError):
            _parse_irs_minimum_display_score(86)
        with self.assertRaises(ValueError):
            _parse_irs_minimum_display_score(True)

    def test_write_env_setting_allows_display_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("KEEP=true\n", encoding="utf-8")

            _write_env_setting(env_path, "IRS_MINIMUM_DISPLAY_SCORE", "75")

            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "KEEP=true\nIRS_MINIMUM_DISPLAY_SCORE=75\n",
            )

    def test_dashboard_irs_analysis_scan_is_read_only(self) -> None:
        with (
            patch("src.symbol_universe.load_stock_symbols", return_value=["SANM"]),
            patch("dashboard_react.server.da.load_watchlist", return_value={}),
            patch(
                "dashboard_react.server.InefficiencyReclaimStore"
            ) as store_cls,
            patch(
                "dashboard_react.server.run_inefficiency_reclaim_screener",
                return_value={"ok": True},
            ) as scanner,
        ):
            store_cls.return_value.active_setups.return_value = [{"ticker": "SANM"}]
            result = api_inefficiency_reclaim(anchor_date="2026-07-24")

        self.assertEqual(result["active_setups"], [{"ticker": "SANM"}])
        self.assertFalse(scanner.call_args.kwargs["persist"])


if __name__ == "__main__":
    unittest.main()
