from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .client import CheckpointClient
from .config import AppConfig


@dataclass(frozen=True)
class Offer:
    id: int
    points_id: int
    points_amount: int  # raw 18-decimal style
    price: int          # total USDC raw (6-dec style from API)
    collateral_amount: int
    filled_amount: int
    status: int
    account: str

    @property
    def remaining_price(self) -> int:
        # API price is full offer notional; if partially filled, scale roughly
        if self.points_amount <= 0:
            return self.price
        remaining_pts = max(self.points_amount - self.filled_amount, 0)
        if remaining_pts == self.points_amount:
            return self.price
        return int(self.price * remaining_pts / self.points_amount)

    def usdc_notional(self, decimals: int = 6) -> Decimal:
        return Decimal(self.remaining_price) / Decimal(10 ** decimals)


def fetch_offers(client: CheckpointClient, cfg: AppConfig, points_id: int | None = None) -> list[Offer]:
    pid = cfg.points_id if points_id is None else points_id
    url = f"{cfg.market_api}/market/{pid}/offers?limit=500"
    resp = client.http_get(url)
    resp.raise_for_status()
    data = resp.json()
    offers: list[Offer] = []
    for row in data.get("offers") or []:
        try:
            status = int(row.get("status", 0))
            if status != 0:
                continue
            points_amount = int(row.get("pointsAmount") or 0)
            filled = int(row.get("filledAmount") or 0)
            if points_amount <= 0 or filled >= points_amount:
                continue
            offers.append(
                Offer(
                    id=int(row["id"]),
                    points_id=int(row.get("pointsId") or pid),
                    points_amount=points_amount,
                    price=int(row.get("price") or 0),
                    collateral_amount=int(row.get("collateralAmount") or 0),
                    filled_amount=filled,
                    status=status,
                    account=str(row.get("account") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    offers.sort(key=lambda o: o.remaining_price)
    return offers


def pick_fill_targets(
    offers: list[Offer],
    *,
    usdc_min: Decimal,
    usdc_max: Decimal,
    count: int,
    usdc_decimals: int = 6,
) -> list[tuple[Offer, int, bool]]:
    """Return list of (offer, usdc_amount_raw, full_fill).

    Spend toward usdc_max (testnet daily budget), not the cheapest dust.
    1) partial-fill oversized offers at exactly usdc_max
    2) full-fill the largest offers still under usdc_max
    3) leftover cheapest dust only if nothing else exists
    """
    import random

    picks: list[tuple[Offer, int, bool]] = []
    used_ids: set[int] = set()
    min_raw = int(usdc_min * Decimal(10 ** usdc_decimals))
    max_raw = int(usdc_max * Decimal(10 ** usdc_decimals))
    floor_raw = max(min_raw, int(max_raw * 0.25)) if max_raw > 0 else 0

    def take(offer: Offer, amount: int, full: bool) -> None:
        picks.append((offer, amount, full))
        used_ids.add(offer.id)

    # Prefer eating the configured budget via partial fills of large books.
    oversized = [
        o for o in offers
        if o.id not in used_ids and o.remaining_price > max_raw > 0
    ]
    random.shuffle(oversized)
    for offer in oversized:
        if len(picks) >= count:
            return picks[:count]
        take(offer, max_raw, False)

    # Then largest full fills in the upper band of the budget.
    in_budget = [
        o for o in offers
        if o.id not in used_ids and floor_raw <= o.remaining_price <= max_raw
    ]
    in_budget.sort(key=lambda o: o.remaining_price, reverse=True)
    for offer in in_budget:
        if len(picks) >= count:
            return picks[:count]
        take(offer, offer.remaining_price, True)

    # Last resort: any remaining full fill under max (including dust).
    leftovers = [
        o for o in offers
        if o.id not in used_ids and 0 < o.remaining_price <= max_raw
    ]
    leftovers.sort(key=lambda o: o.remaining_price, reverse=True)
    for offer in leftovers:
        if len(picks) >= count:
            break
        take(offer, offer.remaining_price, True)

    return picks[:count]


def market_overview(client: CheckpointClient, cfg: AppConfig) -> dict[str, Any]:
    resp = client.http_get(f"{cfg.market_api}/market/overview")
    resp.raise_for_status()
    return resp.json()
