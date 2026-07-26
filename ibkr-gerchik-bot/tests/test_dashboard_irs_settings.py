from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dashboard_react.server import (
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
        settings = api_inefficiency_reclaim_settings()

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


if __name__ == "__main__":
    unittest.main()
