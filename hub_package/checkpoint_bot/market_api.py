from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .client import CheckpointClient
from .config import AppConfig

# 0 = untouched open book, 2 = partially filled and still live on the UI.
# Fully filled / cancelled (1, 3, …) are skipped.
OPEN_OFFER_STATUSES = frozenset({0, 2})
OFFER_WAIT_SECONDS = 300.0
OFFER_POLL_MIN_SEC = 8.0
OFFER_POLL_MAX_SEC = 15.0


@dataclass(frozen=True)
class Offer:
    id: int
    points_id: int
    points_amount: int  # raw 18-decimal style
    price: int          # total USDC raw (6-dec style from API)
    collateral_amount: int
    filled_amount: int  # USDC already taken, same units as price
    status: int
    account: str

    @property
    def remaining_price(self) -> int:
        """USDC still fillable. HAR: filledAmount is USDC, not points."""
        if self.price <= 0:
            return 0
        if 0 <= self.filled_amount <= self.price:
            return self.price - self.filled_amount
        if self.points_amount <= 0:
            return 0
        remaining_pts = max(self.points_amount - self.filled_amount, 0)
        if remaining_pts <= 0:
            return 0
        if remaining_pts == self.points_amount:
            return self.price
        return int(self.price * remaining_pts / self.points_amount)

    def usdc_notional(self, decimals: int = 6) -> Decimal:
        return Decimal(self.remaining_price) / Decimal(10 ** decimals)


@dataclass(frozen=True)
class MarketInfo:
    points_id: int
    active_offers: int
    best_price: str = "0"


def offers_from_payload(data: dict[str, Any], *, points_id: int) -> list[Offer]:
    """Parse /market/{id}/offers JSON. Live book is status 0 and 2."""
    offers: list[Offer] = []
    for row in data.get("offers") or []:
        try:
            status = int(row.get("status", 0))
            if status not in OPEN_OFFER_STATUSES:
                continue
            points_amount = int(row.get("pointsAmount") or 0)
            filled = int(row.get("filledAmount") or 0)
            price = int(row.get("price") or 0)
            if points_amount <= 0 or price <= 0:
                continue
            offer = Offer(
                id=int(row["id"]),
                points_id=int(row.get("pointsId") or points_id),
                points_amount=points_amount,
                price=price,
                collateral_amount=int(row.get("collateralAmount") or 0),
                filled_amount=filled,
                status=status,
                account=str(row.get("account") or ""),
            )
            if offer.remaining_price <= 0:
                continue
            offers.append(offer)
        except (KeyError, TypeError, ValueError):
            continue
    offers.sort(key=lambda o: o.remaining_price)
    return offers


def fetch_offers(client: CheckpointClient, cfg: AppConfig, points_id: int | None = None) -> list[Offer]:
    pid = cfg.points_id if points_id is None else points_id
    url = f"{cfg.market_api}/market/{pid}/offers?limit=500"
    resp = client.http_get(url)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        return []
    return offers_from_payload(data, points_id=pid)


def markets_from_overview(data: dict[str, Any]) -> list[MarketInfo]:
    out: list[MarketInfo] = []
    for row in data.get("markets") or []:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                MarketInfo(
                    points_id=int(row["pointsId"]),
                    active_offers=int(row.get("activeOffers") or 0),
                    best_price=str(row.get("bestPrice") or "0"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def rank_markets(
    markets: list[MarketInfo],
    *,
    preferred_id: int,
) -> list[MarketInfo]:
    """Checkpoint XP first (own points), then fattest live books."""
    preferred = [m for m in markets if m.points_id == preferred_id]
    rest = [m for m in markets if m.points_id != preferred_id]
    rest.sort(key=lambda m: (-m.active_offers, m.points_id))
    return preferred + rest


def list_markets(client: CheckpointClient, cfg: AppConfig) -> list[MarketInfo]:
    try:
        data = market_overview(client, cfg)
    except Exception:
        return [MarketInfo(points_id=int(cfg.points_id), active_offers=1)]
    if not isinstance(data, dict):
        return [MarketInfo(points_id=int(cfg.points_id), active_offers=1)]
    ranked = rank_markets(markets_from_overview(data), preferred_id=int(cfg.points_id))
    if ranked:
        return ranked
    return [MarketInfo(points_id=int(cfg.points_id), active_offers=1)]


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
