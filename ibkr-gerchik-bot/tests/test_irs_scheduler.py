from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard_react.server import (
    IRS_SCHEDULED_TASKS,
    _irs_schedule_status,
    _next_manifest_run,
)
from src.config import SETTINGS
from src.jobs.inefficiency_reclaim import (
    active_confirmation_symbols,
    record_irs_runtime_status,
)
from src.scheduler import build_task_scheduler_command, get_recommended_task_names


ET = ZoneInfo("America/New_York")


class _SetupStoreStub:
    def active_setups(self) -> list[dict[str, object]]:
        return [
            {"setup_id": "1", "symbol": "atai", "expires_at": "2026-07-27T12:00:00-04:00"},
            {"setup_id": "2", "symbol": "ATAI", "expires_at": "2026-07-27T13:00:00-04:00"},
            {"setup_id": "3", "symbol": "DAL", "expires_at": "2026-07-26T09:00:00-04:00"},
            {"setup_id": "4", "symbol": "SEI", "expires_at": None},
        ]


def _installed_tasks(next_run: datetime) -> list[dict[str, object]]:
    return [
        {
            "task_name": name,
            "state": "Ready",
            "next_run": (next_run + timedelta(minutes=index)).isoformat(),
            "last_result": 0,
        }
        for index, name in enumerate(IRS_SCHEDULED_TASKS)
    ]


class IrsSchedulerTests(unittest.TestCase):
    def test_confirmation_selects_unique_non_expired_active_symbols(self) -> None:
        symbols = active_confirmation_symbols(
            store=_SetupStoreStub(),
            as_of=datetime(2026, 7, 26, 10, 0, tzinfo=ET),
        )

        self.assertEqual(symbols, ["ATAI", "SEI"])

    def test_runtime_status_is_written_atomically(self) -> None:
        original_runtime_dir = SETTINGS.paths.runtime_dir
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                runtime_dir = Path(temp_dir)
                object.__setattr__(SETTINGS.paths, "runtime_dir", runtime_dir)

                record_irs_runtime_status(
                    "running",
                    mode="FIFTEEN_MIN_CONFIRMATION_SCAN",
                    connected=True,
                )

                payload = json.loads(
                    (runtime_dir / "irs_scheduler_status.json").read_text(encoding="utf-8")
                )
                self.assertEqual(payload["status"], "running")
                self.assertTrue(payload["connected"])
                self.assertEqual(payload["pid"], os.getpid())
                self.assertFalse(list(runtime_dir.glob("*.tmp")))
        finally:
            object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir)

    def test_dashboard_reports_connected_and_next_run(self) -> None:
        now = datetime(2026, 7, 27, 9, 30, tzinfo=ET)
        service = _irs_schedule_status(
            tasks=_installed_tasks(now + timedelta(minutes=16)),
            runtime={
                "status": "running",
                "mode": "FIFTEEN_MIN_CONFIRMATION_SCAN",
                "connected": True,
                "pid": os.getpid(),
                "updated_at": now.isoformat(),
            },
            now=now,
        )

        self.assertEqual(service["status"], "connected")
        self.assertTrue(service["ok"])
        self.assertIn("IBKR connected", service["detail"])
        self.assertIn("next Premarket", service["detail"])

    def test_dashboard_reports_partial_install(self) -> None:
        service = _irs_schedule_status(
            tasks=_installed_tasks(datetime.now(ET) + timedelta(hours=1))[:2],
            runtime={},
        )

        self.assertEqual(service["status"], "partial")
        self.assertFalse(service["ok"])
        self.assertIn("2/4 tasks installed", service["detail"])

    def test_manifest_schedule_skips_weekend(self) -> None:
        next_run = _next_manifest_run(
            ["08:00"],
            now=datetime(2026, 7, 26, 11, 0, tzinfo=ET),
        )

        self.assertEqual(next_run, "2026-07-27T08:00:00-04:00")

    def test_scheduler_commands_and_task_names_are_distinct(self) -> None:
        root = Path("C:/bot")
        command = build_task_scheduler_command(
            root,
            "C:/Python/python.exe",
            "irs_premarket",
        )
        names = get_recommended_task_names()

        self.assertIn("--irs-mode PREMARKET_CONTEXT", command)
        self.assertEqual(names["irs_confirmation"], "IBKR Bot - IRS Confirmation")
        self.assertEqual(names["irs_eod"], "IBKR Bot - IRS EOD")


if __name__ == "__main__":
    unittest.main()
