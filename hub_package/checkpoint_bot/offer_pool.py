from __future__ import annotations

import threading
from typing import Iterable

from .market_api import Offer


class OfferPool:
    """Thread-safe reservation so parallel wallets don't race the same offer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: set[int] = set()

    def claim(self, offer_id: int) -> bool:
        with self._lock:
            if offer_id in self._claimed:
                return False
            self._claimed.add(offer_id)
            return True

    def release(self, offer_id: int) -> None:
        with self._lock:
            self._claimed.discard(offer_id)

    def filter_available(self, offers: Iterable[Offer]) -> list[Offer]:
        with self._lock:
            return [o for o in offers if o.id not in self._claimed]
