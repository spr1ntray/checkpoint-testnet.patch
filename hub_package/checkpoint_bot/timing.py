"""Internal anti-sybil pacing.

Deliberately not exposed as Soft Hub options — timings live in the core
and apply automatically on Work / Parse so the user is not burdened.

Each account rolls a SessionStyle so two runs never share the same cadence.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

CancelCheck = Callable[[], None] | None

# Base action gap used when config does not specify a range (seconds).
DEFAULT_ACTION_MIN = 6.0
DEFAULT_ACTION_MAX = 16.0


@dataclass(frozen=True)
class SessionStyle:
    """Per-account random personality for one run."""

    start_lo: float
    start_hi: float
    action_lo: float
    action_hi: float
    think_p: float
    think_extra_lo: float
    think_extra_hi: float
    http_lo: float
    http_hi: float
    level_lo: float
    level_hi: float
    tx_lo: float
    tx_hi: float
    usdc_factor: float
    trades_extra_skip: int  # 0 or 1: still aim at cap, maybe skip last if unlucky


def roll_session() -> SessionStyle:
    """Pick a human-ish tempo and jitter every bound."""
    family = random.choice(("snappy", "steady", "leisure", "bursty"))
    if family == "snappy":
        action_lo, action_hi = 0.4, 1.6
        start_lo, start_hi = 0.0, 0.25
        think_p = 0.02
    elif family == "leisure":
        action_lo, action_hi = 0.8, 2.4
        start_lo, start_hi = 0.05, 0.4
        think_p = 0.04
    elif family == "bursty":
        action_lo, action_hi = 0.3, 2.0
        start_lo, start_hi = 0.0, 0.35
        think_p = 0.03
    else:
        action_lo, action_hi = 0.5, 1.8
        start_lo, start_hi = 0.0, 0.3
        think_p = 0.03

    def j(a: float, b: float) -> tuple[float, float]:
        lo = max(0.05, a * random.uniform(0.75, 1.15))
        hi = max(lo + 0.2, b * random.uniform(0.85, 1.25))
        return lo, hi

    slo, shi = j(start_lo, start_hi)
    alo, ahi = j(action_lo, action_hi)
    hlo, hhi = j(0.15, 2.4)
    llo, lhi = j(1.2, 8.0)
    tlo, thi = j(0.8, 4.8)
    return SessionStyle(
        start_lo=slo,
        start_hi=shi,
        action_lo=alo,
        action_hi=ahi,
        think_p=min(0.35, think_p * random.uniform(0.5, 1.6)),
        think_extra_lo=random.uniform(2.0, 6.0),
        think_extra_hi=random.uniform(7.0, 18.0),
        http_lo=hlo,
        http_hi=hhi,
        level_lo=llo,
        level_hi=lhi,
        tx_lo=tlo,
        tx_hi=thi,
        usdc_factor=random.uniform(0.42, 1.0),
        trades_extra_skip=1 if random.random() < 0.08 else 0,
    )


def sleep_jitter(
    lo: float,
    hi: float,
    *,
    cancel_check: CancelCheck = None,
) -> float:
    """Sleep a random duration in [lo, hi], cancellable in small chunks."""
    lo_f = max(0.0, float(lo))
    hi_f = max(lo_f, float(hi))
    if hi_f <= 0:
        return 0.0
    delay = random.uniform(lo_f, hi_f)
    deadline = time.monotonic() + delay
    while True:
        if cancel_check is not None:
            cancel_check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.35, remaining))
    return delay


def account_start_delay(
    *,
    cancel_check: CancelCheck = None,
    style: SessionStyle | None = None,
) -> float:
    """Stagger parallel workers so they do not open sessions in lockstep."""
    if style:
        return sleep_jitter(style.start_lo, style.start_hi, cancel_check=cancel_check)
    return sleep_jitter(0.5, 5.8, cancel_check=cancel_check)


def between_levels_delay(
    *,
    cancel_check: CancelCheck = None,
    style: SessionStyle | None = None,
) -> float:
    """Pause after a referral-level barrier (parents finished → children)."""
    if style:
        return sleep_jitter(style.level_lo, style.level_hi, cancel_check=cancel_check)
    return sleep_jitter(1.8, 7.0, cancel_check=cancel_check)


def pre_http_delay(
    *,
    cancel_check: CancelCheck = None,
    style: SessionStyle | None = None,
) -> float:
    """Short jitter before external Checkpoint HTTP calls."""
    if style:
        return sleep_jitter(style.http_lo, style.http_hi, cancel_check=cancel_check)
    return sleep_jitter(0.25, 2.0, cancel_check=cancel_check)


def action_delay(
    delay_min: float | None = None,
    delay_max: float | None = None,
    *,
    cancel_check: CancelCheck = None,
    style: SessionStyle | None = None,
) -> float:
    """
    Human-ish gap between on-chain / heavy steps.

    Takes cfg delay_min/max as soft center and spreads them with jitter.
    ~12% of the time adds an extra long "think" pause.
    """
    if style is not None:
        lo, hi = style.action_lo, style.action_hi
        think_p = style.think_p
        extra_lo, extra_hi = style.think_extra_lo, style.think_extra_hi
    else:
        lo = float(delay_min if delay_min is not None else DEFAULT_ACTION_MIN)
        hi = float(delay_max if delay_max is not None else DEFAULT_ACTION_MAX)
        think_p = 0.12
        extra_lo, extra_hi = 2.5, 9.0
    if hi < lo:
        lo, hi = hi, lo
    spread_lo = max(0.4, lo * 0.65)
    spread_hi = max(spread_lo + 0.5, hi * 1.4)
    if random.random() < think_p:
        spread_hi += random.uniform(extra_lo, extra_hi)
    return sleep_jitter(spread_lo, spread_hi, cancel_check=cancel_check)


def short_tx_gap(
    *,
    cancel_check: CancelCheck = None,
    style: SessionStyle | None = None,
) -> float:
    """Gap between approve and fill (or similar tight pairs)."""
    if style:
        return sleep_jitter(style.tx_lo, style.tx_hi, cancel_check=cancel_check)
    return sleep_jitter(1.0, 4.2, cancel_check=cancel_check)


def parse_account_gap(*, cancel_check: CancelCheck = None) -> float:
    """Light stagger for parse workers (read-only, still desyncs)."""
    return sleep_jitter(0.15, 1.6, cancel_check=cancel_check)
