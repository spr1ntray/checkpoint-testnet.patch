from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "hub_package"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FaucetPreflightTests(unittest.TestCase):
    def test_healthy_sepolia_skips_the_whole_cycle(self) -> None:
        from checkpoint_bot.faucet import GAS_NEED_WEI, faucet_preflight

        self.assertEqual(faucet_preflight(GAS_NEED_WEI, 0, 0), "skip")
        self.assertEqual(faucet_preflight(10**16, 10**15, 0), "skip")

    def test_no_l1_eth_is_ineligible_without_swap(self) -> None:
        from checkpoint_bot.faucet import faucet_preflight

        self.assertEqual(faucet_preflight(0, 0, 0), "ineligible")
        self.assertEqual(faucet_preflight(0, 10**15 - 1, 12), "ineligible")

    def test_l1_eth_with_history_goes_straight_to_faucet(self) -> None:
        from checkpoint_bot.faucet import MAINNET_NEED_WEI, faucet_preflight

        self.assertEqual(faucet_preflight(0, MAINNET_NEED_WEI, 1), "faucet")
        self.assertEqual(faucet_preflight(0, 5 * 10**16, 40), "faucet")

    def test_l1_eth_without_nonce_needs_activation(self) -> None:
        from checkpoint_bot.faucet import MAINNET_NEED_WEI, faucet_preflight

        self.assertEqual(faucet_preflight(0, MAINNET_NEED_WEI, 0), "activate_then_faucet")
        self.assertEqual(faucet_preflight(1, 2 * 10**15, 0), "activate_then_faucet")

    def test_unknown_l1_still_tries_faucet_no_swap(self) -> None:
        from checkpoint_bot.faucet import faucet_preflight

        self.assertEqual(faucet_preflight(0, None, None), "faucet")
        self.assertEqual(faucet_preflight(0, 10**16, None), "faucet")


class SwapBudgetTests(unittest.TestCase):
    def test_swap_never_drops_below_quicknode_gate(self) -> None:
        from checkpoint_bot.faucet import MAINNET_NEED_WEI
        from checkpoint_bot.mainnet import TARGET_SWAP_WEI, swap_budget_wei

        balance = 15 * 10**14  # 0.0015 ETH — как 13 ACC на видео
        gas_cost = 2 * 10**14
        budget = swap_budget_wei(balance, gas_cost)
        self.assertGreater(budget, 0)
        self.assertLess(budget, TARGET_SWAP_WEI)
        leftover = balance - budget - gas_cost
        self.assertGreaterEqual(leftover, MAINNET_NEED_WEI)

    def test_rich_wallet_swaps_about_two_dollars(self) -> None:
        from checkpoint_bot.mainnet import TARGET_SWAP_WEI, swap_budget_wei

        budget = swap_budget_wei(10**16, 2 * 10**14)
        self.assertEqual(budget, TARGET_SWAP_WEI)

    def test_too_tight_to_swap_returns_zero(self) -> None:
        from checkpoint_bot.mainnet import swap_budget_wei, wrap_budget_wei

        self.assertEqual(swap_budget_wei(11 * 10**14, 2 * 10**14), 0)
        self.assertEqual(wrap_budget_wei(11 * 10**14, 2 * 10**14), 0)

    def test_wrap_dust_when_swap_does_not_fit(self) -> None:
        from checkpoint_bot.faucet import MAINNET_NEED_WEI
        from checkpoint_bot.mainnet import WRAP_WEI, wrap_budget_wei

        # 0.0013 ETH, cheap gas — wrap dust, keep the 0.001 gate
        balance = 13 * 10**14
        gas_cost = 5 * 10**13
        wrap = wrap_budget_wei(balance, gas_cost)
        self.assertEqual(wrap, WRAP_WEI)
        self.assertGreaterEqual(balance - wrap - gas_cost, MAINNET_NEED_WEI)

    def test_needs_history_only_on_fresh_nonce(self) -> None:
        from checkpoint_bot.mainnet import needs_mainnet_history

        self.assertTrue(needs_mainnet_history(0))
        self.assertFalse(needs_mainnet_history(1))
        self.assertFalse(needs_mainnet_history(9))

    def test_self_tx_keeps_quicknode_gate(self) -> None:
        from checkpoint_bot.faucet import MAINNET_NEED_WEI
        from checkpoint_bot.mainnet import can_self_tx

        self.assertTrue(can_self_tx(15 * 10**14, 2 * 10**14))
        self.assertFalse(can_self_tx(11 * 10**14, 2 * 10**14))
        self.assertTrue(can_self_tx(MAINNET_NEED_WEI + 1, 0))
        self.assertFalse(can_self_tx(MAINNET_NEED_WEI - 1, 0))


class FaucetBannerTests(unittest.TestCase):
    def test_history_banner_is_not_mislabeled_as_no_eth(self) -> None:
        from pathlib import Path

        faucet = (Path(__file__).resolve().parents[1] / "hub_package" / "checkpoint_bot" / "faucet.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("established transaction history", faucet)
        self.assertIn("HISTORY_ERROR_MSG", faucet)
        self.assertIn("_raise_if_history_gated", faucet)
        self.assertIn("faucet_preflight", faucet)
        self.assertIn("activate_mainnet_history", faucet)
        self.assertIn("FaucetEligibilityError(HISTORY_ERROR_MSG)", faucet)
        self.assertIn("FaucetEligibilityError(MAINNET_ERROR_MSG)", faucet)


class CheckChainSourceTests(unittest.TestCase):
    def test_healthy_accounts_never_hit_l1_or_ads(self) -> None:
        root = Path(__file__).resolve().parents[1] / "hub_package"
        main = (root / "plugin" / "main.py").read_text(encoding="utf-8")
        actions = (root / "checkpoint_bot" / "actions.py").read_text(encoding="utf-8")
        skip = main.index("ETH на газе хватает — кран и свап пропускаем")
        snap = main.index("mainnet_snapshot(client.address)")
        activate = main.index('decision == "activate_then_faucet"')
        ads = main.index("claim_sepolia_eth(")
        self.assertLess(skip, snap)
        self.assertLess(snap, activate)
        self.assertLess(activate, ads)
        self.assertIn("except FaucetEligibilityError:", actions)
        self.assertIn("except FaucetEligibilityError:", main)


if __name__ == "__main__":
    unittest.main()
