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

    Prefers cheapest full fills under usdc_max. If none, tries partial fill
    of the cheapest oversized offer up to usdc_max. usdc_min is a soft floor
    used only to deprioritize dust after enough normal picks exist.
    """
    picks: list[tuple[Offer, int, bool]] = []
    used_ids: set[int] = set()
    min_raw = int(usdc_min * Decimal(10 ** usdc_decimals))
    max_raw = int(usdc_max * Decimal(10 ** usdc_decimals))

    # Pass 1: full fills within [0, max]
    for offer in offers:
        if len(picks) >= count:
            break
        if offer.id in used_ids:
            continue
        notional = offer.remaining_price
        if notional <= 0 or notional > max_raw:
            continue
        picks.append((offer, notional, True))
        used_ids.add(offer.id)

    # Pass 2: if still short, partial-fill larger offers
    if len(picks) < count:
        for offer in offers:
            if len(picks) >= count:
                break
            if offer.id in used_ids:
                continue
            notional = offer.remaining_price
            if notional <= max_raw:
                continue
            picks.append((offer, max_raw, False))
            used_ids.add(offer.id)

    # Soft: if we somehow only have dust and user set a min, still keep them
    # (max XP = cheapest trades). No further filter.
    _ = min_raw
    return picks[:count]


def market_overview(client: CheckpointClient, cfg: AppConfig) -> dict[str, Any]:
    resp = client.http_get(f"{cfg.market_api}/market/overview")
    resp.raise_for_status()
    return resp.json()
