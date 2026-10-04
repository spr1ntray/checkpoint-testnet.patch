from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "hub_package"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


ADDR_A = "0x1111111111111111111111111111111111111111"
ADDR_B = "0x2222222222222222222222222222222222222222"


class DailyActionCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "daily_actions.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_limit_is_twelve(self) -> None:
        from checkpoint_bot.daily_cap import DAILY_ACTION_LIMIT

        self.assertEqual(DAILY_ACTION_LIMIT, 12)

    def test_fresh_address_has_full_quota(self) -> None:
        from checkpoint_bot.daily_cap import DAILY_ACTION_LIMIT, DailyActionCap

        cap = DailyActionCap(self.path)
        self.assertEqual(cap.used(ADDR_A), 0)
        self.assertEqual(cap.remaining(ADDR_A), DAILY_ACTION_LIMIT)

    def test_consume_stops_at_limit(self) -> None:
        from checkpoint_bot.daily_cap import DailyActionCap

        cap = DailyActionCap(self.path, limit=12)
        self.assertEqual(cap.consume(ADDR_A, 12), 12)
        self.assertEqual(cap.remaining(ADDR_A), 0)
        self.assertFalse(cap.try_consume(ADDR_A))
        self.assertEqual(cap.used(ADDR_A), 12)
        self.assertEqual(cap.consume(ADDR_A, 3), 0)

    def test_accounts_are_independent(self) -> None:
        from checkpoint_bot.daily_cap import DailyActionCap

        cap = DailyActionCap(self.path, limit=12)
        cap.consume(ADDR_A, 12)
        self.assertEqual(cap.remaining(ADDR_B), 12)

    def test_new_day_resets_count(self) -> None:
        from checkpoint_bot.daily_cap import DailyActionCap

        cap = DailyActionCap(self.path, limit=12)
        cap.consume(ADDR_A, 9)
        self.path.write_text(
            json.dumps({ADDR_A.lower(): {"day": "1999-01-01", "count": 9}}),
            encoding="utf-8",
        )
        self.assertEqual(cap.used(ADDR_A), 0)
        self.assertEqual(cap.remaining(ADDR_A), 12)

    def test_state_path_lives_next_to_plugin_versions(self) -> None:
        from checkpoint_bot.daily_cap import state_path

        plugin_root = Path(self.tmp.name) / "io.sprintray.checkpoint-testnet" / "1.7.24"
        plugin_root.mkdir(parents=True)
        path = state_path(str(plugin_root))
        self.assertEqual(path.parent.name, "io.sprintray.checkpoint-testnet")
        self.assertEqual(path.name, "daily_actions.json")

    def test_trades_loop_no_longer_retries_twelve_times_per_fill(self) -> None:
        actions = (ROOT / "checkpoint_bot" / "actions.py").read_text(encoding="utf-8")
        self.assertNotIn("target_fills * 12", actions)
        self.assertIn("DAILY_ACTION_LIMIT", actions)
        self.assertIn("daily_cap", actions)
        self.assertIn("daily_cap", (ROOT / "plugin" / "main.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
