from __future__ import annotations

import random
import sys
from decimal import Decimal
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "hub_package"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class SessionPlanTests(unittest.TestCase):
    def test_clamp_trades_range(self) -> None:
        from checkpoint_bot.session_plan import clamp_trades_range

        self.assertEqual(clamp_trades_range(5, 10), (5, 10))
        self.assertEqual(clamp_trades_range(10, 5), (5, 10))
        self.assertEqual(clamp_trades_range(1, 20), (5, 10))
        self.assertEqual(clamp_trades_range(7, 7), (7, 7))

    def test_plan_session_budget_splits_across_trades(self) -> None:
        from checkpoint_bot.session_plan import plan_session

        random.seed(42)
        plan = plan_session(trades_min=5, trades_max=8)
        self.assertGreaterEqual(plan.trades, 5)
        self.assertLessEqual(plan.trades, 8)
        self.assertGreaterEqual(plan.budget_usdc, Decimal("420"))
        self.assertLessEqual(plan.budget_usdc, Decimal("500"))
        self.assertEqual(len(plan.fills), plan.trades)
        self.assertEqual(sum(plan.fills), plan.budget_usdc)
        self.assertTrue(all(f >= Decimal("0.01") for f in plan.fills))

    def test_split_budget_exact_sum(self) -> None:
        from checkpoint_bot.session_plan import split_budget

        random.seed(7)
        budget = Decimal("473.25")
        fills = split_budget(budget, 6)
        self.assertEqual(len(fills), 6)
        self.assertEqual(sum(fills), budget)


if __name__ == "__main__":
    unittest.main()
