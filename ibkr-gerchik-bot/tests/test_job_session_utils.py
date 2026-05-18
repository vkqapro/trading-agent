from __future__ import annotations

import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest import TestCase

from src.jobs.session_utils import (
    SESSION_LOCK_STALE_AFTER,
    can_scan_for_new_entries,
    get_scan_interval,
    is_open_entry_window,
    job_loop_lock,
    next_scan_time,
    protective_stops_ok,
)
from src.config import SETTINGS


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

    def test_protective_stop_recheck_passes_when_stop_appears_on_retry(self) -> None:
        class BrokerStub:
            def __init__(self) -> None:
                self.calls = 0

            def get_open_orders(self):
                self.calls += 1
                if self.calls == 1:
                    return []
                return [{"symbol": "BBBY", "type": "STP", "order_id": 123}]

        broker = BrokerStub()
        tracked_positions = [
            {
                "symbol": "BBBY",
                "quantity": 100,
                "direction": "long",
                "stop_order_id": 123,
            }
        ]

        slept: list[float] = []
        self.assertTrue(
            protective_stops_ok(
                broker,
                tracked_positions,
                now_provider=lambda: datetime(2026, 5, 5, 10, 35, tzinfo=TZ),
                sleep_provider=lambda seconds: slept.append(seconds),
            )
        )
        self.assertEqual(len(slept), 1)

    def test_protective_stop_recheck_respects_grace_window_for_new_position(self) -> None:
        class BrokerStub:
            def get_open_orders(self):
                return []

        now = datetime(2026, 5, 5, 10, 35, tzinfo=TZ)
        tracked_positions = [
            {
                "symbol": "BBBY",
                "quantity": 100,
                "direction": "long",
                "stop_order_id": 123,
                "opened_at": now.isoformat(),
            }
        ]

        self.assertTrue(
            protective_stops_ok(
                BrokerStub(),
                tracked_positions,
                now_provider=lambda: now,
                sleep_provider=lambda _seconds: None,
            )
        )

    def test_job_loop_lock_recovers_stale_lock_file(self) -> None:
        original_runtime_dir = SETTINGS.paths.runtime_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir.__class__(temp_dir))
            lock_path = SETTINGS.paths.runtime_dir / "open_session.lock"
            lock_path.write_text("123|stale\n", encoding="utf-8")
            stale_timestamp = datetime.now().timestamp() - SESSION_LOCK_STALE_AFTER.total_seconds() - 60
            os.utime(lock_path, (stale_timestamp, stale_timestamp))

            with job_loop_lock("open_session") as acquired:
                self.assertTrue(acquired)
                self.assertTrue(lock_path.exists())

            self.assertFalse(lock_path.exists())
        object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir)
