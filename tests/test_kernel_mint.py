from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "hub_package"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class KernelMintBatchTests(unittest.TestCase):
    """UI mints test USDC in the same Kernel UserOp as the first fill.

    Standalone mint on an undeployed Kernel reverts with reason 0x
    (eth_estimateUserOperationGas). Batch mint+approve+fill matches Checkpoint.
    """

    def test_kernel_fill_calls_without_mint(self) -> None:
        from checkpoint_bot.actions import kernel_fill_calls

        calls = kernel_fill_calls(
            usdc="0xUSDC",
            market="0xMKT",
            approve_data=b"\x01",
            fill_data=b"\x02",
        )
        self.assertEqual(
            calls,
            [("0xUSDC", b"\x01", 0), ("0xMKT", b"\x02", 0)],
        )

    def test_kernel_fill_calls_prepends_mint(self) -> None:
        from checkpoint_bot.actions import kernel_fill_calls

        calls = kernel_fill_calls(
            usdc="0xUSDC",
            market="0xMKT",
            approve_data=b"\x01",
            fill_data=b"\x02",
            mint_data=b"\x99",
        )
        self.assertEqual(
            calls,
            [
                ("0xUSDC", b"\x99", 0),
                ("0xUSDC", b"\x01", 0),
                ("0xMKT", b"\x02", 0),
            ],
        )

    def test_trades_does_not_send_standalone_mint(self) -> None:
        src = (ROOT / "checkpoint_bot" / "actions.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "send_calls([(usdc, mint_data, 0)])",
            src,
            "standalone mint UserOp reverts on undeployed Kernel (reason: 0x)",
        )
        self.assertIn("kernel_fill_calls", src)
        self.assertIn("kernel_needs_mint", src)

    def test_kernel_needs_mint_when_fill_exceeds_balance(self) -> None:
        from checkpoint_bot.actions import kernel_needs_mint

        self.assertTrue(kernel_needs_mint(0, 144_409_198))
        self.assertTrue(kernel_needs_mint(56_000_000, 147_847_965))
        self.assertFalse(kernel_needs_mint(500_000_000, 147_847_965))
        self.assertFalse(kernel_needs_mint(147_847_965, 147_847_965))

    def test_erc20_revert_is_low_usdc_not_low_gas(self) -> None:
        from checkpoint_bot.actions import _friendly_fill_error, _is_erc20_usdc, _is_low_gas

        err = RuntimeError(
            "zerodev eth_estimateUserOperationGas: UserOperation reverted "
            "during simulation with reason: 000000264552433230"
        )
        self.assertTrue(_is_erc20_usdc(err))
        self.assertFalse(_is_low_gas(err))
        self.assertIn("USDC", _friendly_fill_error(err))


if __name__ == "__main__":
    unittest.main()
