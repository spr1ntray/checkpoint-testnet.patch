"""Per-account farm plan: random daily spend 420–500 USDC split across N fills."""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

TRADES_FLOOR = 5
TRADES_CEILING = 10
BUDGET_MIN = Decimal("420.00")
BUDGET_MAX = Decimal("500.00")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class SessionPlan:
    budget_usdc: Decimal
    trades: int
    fills: tuple[Decimal, ...]


def clamp_trades_range(lo: int, hi: int) -> tuple[int, int]:
    a = max(TRADES_FLOOR, min(TRADES_CEILING, int(lo)))
    b = max(TRADES_FLOOR, min(TRADES_CEILING, int(hi)))
    if b < a:
        a, b = b, a
    return a, b


def split_budget(budget: Decimal, trades: int) -> tuple[Decimal, ...]:
    n = max(1, int(trades))
    total = Decimal(budget).quantize(CENT, rounding=ROUND_HALF_UP)
    weights = [random.uniform(0.75, 1.25) for _ in range(n)]
    wsum = sum(weights) or 1.0
    parts = [
        (total * Decimal(str(w / wsum))).quantize(CENT, rounding=ROUND_HALF_UP)
        for w in weights
    ]
    parts[-1] = (total - sum(parts[:-1])).quantize(CENT, rounding=ROUND_HALF_UP)
    for i, part in enumerate(parts):
        if part < CENT:
            donor = max(range(n), key=lambda j: parts[j])
            need = CENT - part
            if parts[donor] - need >= CENT:
                parts[donor] = (parts[donor] - need).quantize(CENT)
                parts[i] = CENT
    parts[-1] = (total - sum(parts[:-1])).quantize(CENT, rounding=ROUND_HALF_UP)
    if parts[-1] < CENT:
        parts[-1] = CENT
        drift = sum(parts) - total
        donor = max(range(n - 1), key=lambda j: parts[j], default=0)
        parts[donor] = (parts[donor] - drift).quantize(CENT)
    return tuple(parts)


def plan_session(*, trades_min: int, trades_max: int) -> SessionPlan:
    lo, hi = clamp_trades_range(trades_min, trades_max)
    trades = random.randint(lo, hi)
    cents = random.randint(int(BUDGET_MIN * 100), int(BUDGET_MAX * 100))
    budget = (Decimal(cents) / Decimal(100)).quantize(CENT)
    return SessionPlan(budget_usdc=budget, trades=trades, fills=split_budget(budget, trades))
