"""Hard cap: one EOA may not send more than 12 Kernel UserOps per local day.

Retries, failed AA23 and a second Hub run the same day all share the quota.
State lives next to installed plugin versions so it survives zip upgrades.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover — darwin/linux Hub runtime has fcntl
    fcntl = None  # type: ignore[assignment]

DAILY_ACTION_LIMIT = 12
_THREAD = threading.Lock()


def today() -> str:
    return date.today().isoformat()


def state_path(plugin_root: str | None = None) -> Path:
    if plugin_root:
        return Path(plugin_root).resolve().parent / "daily_actions.json"
    return Path.home() / ".checkpoint-testnet" / "daily_actions.json"


def _key(address: str) -> str:
    return str(address or "").strip().lower()


class DailyCapError(RuntimeError):
    """Quota exhausted — stop sending UserOps, do not fail the farm."""


class DailyActionCap:
    def __init__(self, path: str | Path, *, limit: int = DAILY_ACTION_LIMIT) -> None:
        self.path = Path(path)
        self.limit = int(limit)

    def used(self, address: str) -> int:
        key = _key(address)
        if not key:
            return 0
        day = today()
        with self._locked() as data:
            row = data.get(key) or {}
            if str(row.get("day") or "") != day:
                return 0
            return max(0, int(row.get("count") or 0))

    def remaining(self, address: str) -> int:
        return max(0, self.limit - self.used(address))

    def consume(self, address: str, n: int = 1) -> int:
        """Count up to n actions. Returns how many actually landed in the quota."""
        key = _key(address)
        take = max(0, int(n))
        if not key or take == 0:
            return 0
        day = today()
        with self._locked() as data:
            row = data.get(key) or {}
            if str(row.get("day") or "") != day:
                row = {"day": day, "count": 0}
            have = max(0, int(row.get("count") or 0))
            room = max(0, self.limit - have)
            used = min(room, take)
            row["day"] = day
            row["count"] = have + used
            data[key] = row
            self._write(data, keep_day=day)
            return used

    def try_consume(self, address: str) -> bool:
        return self.consume(address, 1) == 1

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with _THREAD:
            with open(lock_path, "a+", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield self._read()
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw

    def _write(self, data: dict[str, Any], *, keep_day: str) -> None:
        pruned = {
            key: row
            for key, row in data.items()
            if isinstance(row, dict) and str(row.get("day") or "") == keep_day
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(pruned, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)
