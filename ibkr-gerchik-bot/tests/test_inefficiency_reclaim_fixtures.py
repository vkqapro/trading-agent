from __future__ import annotations

import json
import unittest
from pathlib import Path


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "irs"
EXPECTED_SCENARIOS = {
    "bullish_strict_gap_valid",
    "bullish_low_overlap_valid",
    "bearish_strict_gap_valid",
    "retrace_too_deep",
    "weak_volume",
    "no_target_space",
    "ambiguous_intrabar",
    "earnings_block",
}


class GoldenFixtureTests(unittest.TestCase):
    def test_required_scenarios_have_valid_contracts(self) -> None:
        discovered = {path.parent.name for path in FIXTURE_ROOT.glob("*/fixture.json")}
        self.assertEqual(discovered, EXPECTED_SCENARIOS)
        for scenario in sorted(EXPECTED_SCENARIOS):
            with self.subTest(scenario=scenario):
                path = FIXTURE_ROOT / scenario / "fixture.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["name"], scenario)
                self.assertIsInstance(payload["input_bars"], dict)
                self.assertTrue(payload["input_bars"])
                self.assertIsInstance(payload["config"], dict)
                self.assertIsInstance(payload["expected"], dict)
                self.assertIn("accepted", payload["expected"])


if __name__ == "__main__":
    unittest.main()
