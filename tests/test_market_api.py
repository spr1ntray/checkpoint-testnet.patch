from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "hub_package"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class MarketApiTests(unittest.TestCase):
    def test_status_2_partial_is_live(self) -> None:
        from checkpoint_bot.market_api import offers_from_payload

        data = {
            "offers": [
                {
                    "id": "284328",
                    "pointsId": "5",
                    "status": 2,
                    "pointsAmount": "60000000000000000000",
                    "filledAmount": "5614454612",
                    "price": "6000000000",
                    "collateralAmount": "0",
                    "account": "0xabc",
                },
                {
                    "id": "1",
                    "pointsId": "5",
                    "status": 1,
                    "pointsAmount": "1000000000000000000",
                    "filledAmount": "0",
                    "price": "100000000",
                },
                {
                    "id": "2",
                    "pointsId": "5",
                    "status": 2,
                    "pointsAmount": "1000000000000000000",
                    "filledAmount": "50000000",
                    "price": "50000000",
                },
            ]
        }
        offers = offers_from_payload(data, points_id=5)
        self.assertEqual([o.id for o in offers], [284328])
        self.assertEqual(offers[0].remaining_price, 6000000000 - 5614454612)
        self.assertAlmostEqual(float(offers[0].usdc_notional()), 385.545388, places=4)

    def test_status_0_untouched_kept(self) -> None:
        from checkpoint_bot.market_api import offers_from_payload

        data = {
            "offers": [
                {
                    "id": "281560",
                    "pointsId": "5",
                    "status": 0,
                    "pointsAmount": "10000000000000000000",
                    "filledAmount": "0",
                    "price": "1500000000",
                    "collateralAmount": "0",
                }
            ]
        }
        offers = offers_from_payload(data, points_id=5)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].remaining_price, 1500000000)

    def test_rank_markets_prefers_checkpoint_xp(self) -> None:
        from checkpoint_bot.market_api import MarketInfo, rank_markets

        markets = [
            MarketInfo(1, 212, "999"),
            MarketInfo(2, 0, "0"),
            MarketInfo(5, 26004, "100"),
            MarketInfo(6, 969, "50"),
            MarketInfo(7, 0, "0"),
        ]
        ranked = rank_markets(markets, preferred_id=5)
        self.assertEqual([m.points_id for m in ranked], [5, 6, 1, 2, 7])

    def test_pick_partial_on_status_2_remaining(self) -> None:
        from checkpoint_bot.market_api import Offer, pick_fill_targets

        offer = Offer(
            id=284328,
            points_id=5,
            points_amount=60 * 10**18,
            price=6_000_000_000,
            collateral_amount=0,
            filled_amount=5_614_454_612,
            status=2,
            account="0x",
        )
        picks = pick_fill_targets(
            [offer],
            usdc_min=Decimal("20"),
            usdc_max=Decimal("80"),
            count=1,
        )
        self.assertEqual(len(picks), 1)
        chosen, amount, full = picks[0]
        self.assertEqual(chosen.id, 284328)
        self.assertFalse(full)
        self.assertEqual(amount, 80_000_000)


if __name__ == "__main__":
    unittest.main()
