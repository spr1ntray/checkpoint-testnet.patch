"""Internal anti-sybil pacing.

Deliberately not exposed as Soft Hub options — timings live in the core
and apply automatically on Work / Parse so the user is not burdened.
"""

from __future__ import annotations

import random
import time
from typing import Callable

CancelCheck = Callable[[], None] | None

# Base action gap used when config does not specify a range (seconds).
DEFAULT_ACTION_MIN = 6.0
DEFAULT_ACTION_MAX = 16.0


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


def account_start_delay(*, cancel_check: CancelCheck = None) -> float:
    """Stagger parallel workers so they do not open sessions in lockstep."""
    return sleep_jitter(0.5, 5.8, cancel_check=cancel_check)


def between_levels_delay(*, cancel_check: CancelCheck = None) -> float:
    """Pause after a referral-level barrier (parents finished → children)."""
    return sleep_jitter(1.8, 7.0, cancel_check=cancel_check)


def pre_http_delay(*, cancel_check: CancelCheck = None) -> float:
    """Short jitter before external Checkpoint HTTP calls."""
    return sleep_jitter(0.25, 2.0, cancel_check=cancel_check)


def action_delay(
    delay_min: float | None = None,
    delay_max: float | None = None,
    *,
    cancel_check: CancelCheck = None,
) -> float:
    """
    Human-ish gap between on-chain / heavy steps.

    Takes cfg delay_min/max as soft center and spreads them with jitter.
    ~12% of the time adds an extra long "think" pause.
    """
    lo = float(delay_min if delay_min is not None else DEFAULT_ACTION_MIN)
    hi = float(delay_max if delay_max is not None else DEFAULT_ACTION_MAX)
    if hi < lo:
        lo, hi = hi, lo
    spread_lo = max(0.8, lo * 0.65)
    spread_hi = max(spread_lo + 1.0, hi * 1.4)
    if random.random() < 0.12:
        spread_hi += random.uniform(2.5, 9.0)
    return sleep_jitter(spread_lo, spread_hi, cancel_check=cancel_check)


def short_tx_gap(*, cancel_check: CancelCheck = None) -> float:
    """Gap between approve and fill (or similar tight pairs)."""
    return sleep_jitter(1.0, 4.2, cancel_check=cancel_check)


def parse_account_gap(*, cancel_check: CancelCheck = None) -> float:
    """Light stagger for parse workers (read-only, still desyncs)."""
    return sleep_jitter(0.15, 1.6, cancel_check=cancel_check)
