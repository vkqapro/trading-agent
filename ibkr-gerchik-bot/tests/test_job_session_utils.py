from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from unittest import TestCase

from src.jobs.session_utils import (
    can_scan_for_new_entries,
    get_scan_interval,
    is_open_entry_window,
    next_scan_time,
)


TZ = ZoneInfo("America/New_York")


class JobSessionUtilsTests(TestCase):
    def test_open_window_uses_five_minute_scans(self) -> None:
        current = datetime(2026, 5, 1, 9, 36, tzinfo=TZ)
        self.assertTrue(is_open_entry_window(current))
        self.assertTrue(can_scan_for_new_entries(current))
        self.assertEqual(get_scan_interval(current), 300)
        self.assertEqual(next_scan_time(current, 300), datetime(2026, 5, 1, 9, 40, tzinfo=TZ))

    def test_midday_window_uses_fifteen_minute_scans(self) -> None:
        current = datetime(2026, 5, 1, 12, 7, tzinfo=TZ)
        self.assertFalse(is_open_entry_window(current))
        self.assertTrue(can_scan_for_new_entries(current))
        self.assertEqual(get_scan_interval(current), 900)
        self.assertEqual(next_scan_time(current, 900), datetime(2026, 5, 1, 12, 15, tzinfo=TZ))

    def test_new_entries_stop_after_1545(self) -> None:
        current = datetime(2026, 5, 1, 15, 46, tzinfo=TZ)
        self.assertFalse(can_scan_for_new_entries(current))
        self.assertEqual(get_scan_interval(current), 600)

