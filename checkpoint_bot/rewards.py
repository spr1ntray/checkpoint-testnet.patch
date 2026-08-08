from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import CheckpointClient
from .config import AppConfig


@dataclass(frozen=True)
class RewardsSnapshot:
    address: str
    total_points: float
    rank: int | None
    breakdown: dict[str, float]
    raw: dict[str, Any]

    def summary(self) -> str:
        bd = self.breakdown
        parts = [
            f"total={self.total_points:g}",
            f"base={bd.get('baseWalletPoints', 0):g}",
            f"buy={bd.get('buyPoints', 0):g}",
            f"sell={bd.get('sellPoints', 0):g}",
            f"deposit={bd.get('depositPoints', 0):g}",
        ]
        if self.rank is not None:
            parts.append(f"rank={self.rank}")
        return " ".join(parts)


def fetch_rewards(client: CheckpointClient, cfg: AppConfig, address: str | None = None) -> RewardsSnapshot:
    addr = address or client.address
    url = f"{cfg.rewards_api}/users/{addr}"
    resp = client.http_get(url, headers={"Referer": "https://checkpoint.exchange/"})
    resp.raise_for_status()
    data = resp.json()
    breakdown_raw = data.get("pointsBreakdown") or {}
    breakdown = {str(k): float(v or 0) for k, v in breakdown_raw.items()}
    rank = data.get("rank")
    return RewardsSnapshot(
        address=str(data.get("address") or addr),
        total_points=float(data.get("totalPoints") or 0),
        rank=int(rank) if rank is not None else None,
        breakdown=breakdown,
        raw=data,
    )
