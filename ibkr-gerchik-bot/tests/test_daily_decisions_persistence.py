from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.strategy import false_breakout_one_bar as fb1b
from src.strategy.levels import Level


class DailyDecisionsPersistenceTests(unittest.TestCase):
    def test_save_daily_decisions_retries_after_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "daily_decisions.json"
            with (
                patch.object(fb1b, "DAILY_DECISIONS_PATH", temp_path),
                patch.object(fb1b, "DAILY_DECISIONS_LOCK_PATH", temp_path.with_suffix(".json.lock")),
                patch.object(fb1b.time, "sleep", lambda seconds: None),
            ):
                attempts = {"count": 0}
                real_replace = fb1b.os.replace

                def flaky_replace(src: object, dst: object) -> None:
                    attempts["count"] += 1
                    if attempts["count"] == 1:
                        raise PermissionError("temporary file lock")
                    real_replace(src, dst)

                with patch.object(fb1b.os, "replace", side_effect=flaky_replace):
                    saved = fb1b._save_daily_decisions({"date": "2026-05-18", "decisions": {}})

                self.assertTrue(saved)
                self.assertEqual(attempts["count"], 2)
                self.assertTrue(temp_path.exists())
                self.assertEqual(json.loads(temp_path.read_text(encoding="utf-8"))["date"], "2026-05-18")

    def test_persist_daily_decision_swallow_lock_timeout(self) -> None:
        level = Level(
            symbol="KO",
            price=80.32,
            type="historical",
            timeframe="daily",
            touches=1,
            false_breakouts=0,
            strength_score=1.0,
            created_by="test",
        )
        result = {
            "signal": "NONE",
            "entry": None,
            "stop": None,
            "target": None,
            "position_modifier": 1.0,
            "confidence": 0.0,
            "reason": ["no breakout"],
            "context": {"trend": "RANGE", "atr_status": "OK", "news_risk": "LOW", "zone": [80.0, 80.5], "pattern": "ONE_BAR"},
        }

        with patch.object(fb1b, "_acquire_daily_decisions_lock", side_effect=TimeoutError("busy")):
            fb1b._persist_daily_decision("KO", level, "false_breakout_one_bar", result)

    def test_router_rejected_attempt_ranks_above_raw_unvalidated_signal(self) -> None:
        raw_signal = {"signal": "BUY", "confidence": 1.0}
        router_rejected = {"signal": "NONE", "confidence": 1.0, "router_status": "rejected"}
        router_accepted = {"signal": "BUY", "confidence": 1.0, "router_status": "accepted"}

        self.assertGreater(fb1b._decision_rank(router_rejected), fb1b._decision_rank(raw_signal))
        self.assertGreater(fb1b._decision_rank(router_accepted), fb1b._decision_rank(router_rejected))


if __name__ == "__main__":
    unittest.main()
