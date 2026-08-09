from __future__ import annotations

import random
from decimal import Decimal
from typing import Any, Callable

from .auth_siwe import siwe_login
from .client import CheckpointClient
from .config import AppConfig
from .deposit import try_deposit
from .market import create_offer, fill_offer_full, fill_offer_partial
from .market_api import fetch_offers, pick_fill_targets
from .offer_pool import OfferPool
from .rewards import fetch_rewards
from .usdc import approve_market, ensure_usdc
from .utils import sleep_range


Emit = Callable[[dict[str, Any]], None]


def _friendly_fill_error(exc: Exception) -> str:
    text = str(exc)
    if "0xe28caf6a" in text or "CannotFillOffer" in text:
        return "CannotFillOffer — offer уже занят/заполнен (гонка)"
    if "403" in text and "rpc" in text.lower():
        return "RPC 403 Forbidden — смени RPC / выключи RPC_VIA_PROXY"
    return text[:300]


class WalletActionRunner:
    def __init__(
        self,
        cfg: AppConfig,
        client: CheckpointClient,
        emit: Emit,
        mode: str,
        *,
        offer_pool: OfferPool | None = None,
        capsolver_api_key: str = "",
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.emit = emit
        self.mode = mode
        self.offer_pool = offer_pool or OfferPool()
        self.capsolver_api_key = capsolver_api_key
        self.cancel_check = cancel_check

    def _cancel(self) -> None:
        if self.cancel_check:
            self.cancel_check()

    def run(self) -> None:
        self._cancel()
        c = self.client
        self.emit(
            {
                "label": c.label,
                "address": c.address,
                "action": "start",
                "status": "ok",
                "tx_hash": None,
                "details": {"mode": self.mode, "rpc": getattr(c, "rpc_url", "")},
            }
        )

        if self.mode == "parse":
            self._parse()
            return

        if self.cfg.siwe_enabled:
            self._siwe()

        before = None
        try:
            before = fetch_rewards(c, self.cfg)
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "xp_baseline",
                    "status": "ok",
                    "tx_hash": None,
                    "details": {"xp": before.total_points, "summary": before.summary()},
                }
            )
        except Exception as exc:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "xp_baseline",
                    "status": "failed",
                    "tx_hash": None,
                    "details": {"error": str(exc)[:300]},
                }
            )

        if self.mode in {"full", "deposit"} and self.cfg.deposit_enabled:
            self._deposits()

        if self.mode in {"full", "daily"}:
            self._trades()

        if self.mode == "sell" or (self.mode == "full" and self.cfg.sell_enabled):
            self._sells()

        # Indexer XP often lags 1–15+ minutes; quick recheck after short wait
        try:
            after = fetch_rewards(c, self.cfg)
            delta = after.total_points - (before.total_points if before else 0)
            if delta == 0 and self.mode in {"full", "daily"}:
                sleep_range(8, 12)
                after = fetch_rewards(c, self.cfg)
                delta = after.total_points - (before.total_points if before else 0)
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "xp_report",
                    "status": "ok",
                    "tx_hash": None,
                    "details": {
                        "before": before.total_points if before else None,
                        "after": after.total_points,
                        "delta": delta,
                        "summary": after.summary(),
                        "note": "если Δ=0 — перепроверь parse через 10–30 мин (indexer lag)",
                    },
                }
            )
        except Exception as exc:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "xp_report",
                    "status": "failed",
                    "tx_hash": None,
                    "details": {"error": str(exc)[:300]},
                }
            )

    def _parse(self) -> None:
        c = self.client
        eth = c.eth_balance()
        try:
            usdc = c.usdc_balance()
        except Exception:
            usdc = None
        try:
            xp = fetch_rewards(c, self.cfg)
            bd = xp.breakdown
            xp_details = {
                "xp": float(xp.total_points),
                "base_xp": float(bd.get("baseWalletPoints", 0) or 0),
                "buy_xp": float(bd.get("buyPoints", 0) or 0),
                "sell_xp": float(bd.get("sellPoints", 0) or 0),
                "deposit_xp": float(bd.get("depositPoints", 0) or 0),
                "rank": int(xp.rank) if xp.rank is not None else None,
                "summary": xp.summary(),
            }
            status = "ok"
        except Exception as exc:
            xp_details = {
                "xp": 0.0,
                "base_xp": 0.0,
                "buy_xp": 0.0,
                "sell_xp": 0.0,
                "deposit_xp": 0.0,
                "rank": None,
                "error": str(exc)[:200],
            }
            status = "failed"
        self.emit(
            {
                "label": c.label,
                "address": c.address,
                "action": "parse",
                "status": status,
                "tx_hash": None,
                "details": {
                    "eth": f"{eth:.8f}".rstrip("0").rstrip("."),
                    "usdc": (
                        f"{usdc:.6f}".rstrip("0").rstrip(".")
                        if usdc is not None
                        else "0"
                    ),
                    **xp_details,
                },
            }
        )

    def _siwe(self) -> None:
        c = self.client
        try:
            siwe_login(c, self.cfg, capsolver_api_key=self.capsolver_api_key)
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "siwe",
                    "status": "ok",
                    "tx_hash": None,
                    "details": {},
                }
            )
        except Exception as exc:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "siwe",
                    "status": "failed",
                    "tx_hash": None,
                    "details": {"error": str(exc)[:400]},
                }
            )

    def _ensure_gas(self) -> bool:
        c = self.client
        eth = c.eth_balance()
        # Arbitrum Sepolia fills are cheap; keep a low floor so dust wallets still try.
        need = self.cfg.min_eth_balance
        if eth < need:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "gas_check",
                    "status": "skipped",
                    "tx_hash": None,
                    "details": {
                        "reason": "low_gas",
                        "eth": f"{eth:.9f}",
                        "need": f"{need:.9f}",
                        "hint": "Пополни ETH на Arbitrum Sepolia",
                    },
                }
            )
            return False
        return True

    def _deposits(self) -> None:
        if not self._ensure_gas():
            return
        c = self.client
        for pid in self.cfg.deposit_points_ids:
            try:
                details = try_deposit(c, self.cfg, pid)
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "deposit",
                        "status": "ok",
                        "tx_hash": details.get("tx_hash"),
                        "details": details,
                    }
                )
            except Exception as exc:
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "deposit",
                        "status": "skipped",
                        "tx_hash": None,
                        "details": {"points_id": pid, "error": str(exc)[:300]},
                    }
                )
            sleep_range(self.cfg.delay_min, self.cfg.delay_max)

    def _trades(self) -> None:
        if not self._ensure_gas():
            return
        c = self.client
        cfg = self.cfg

        try:
            tx = ensure_usdc(c, cfg)
            if tx:
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "mint_usdc",
                        "status": "ok",
                        "tx_hash": tx,
                        "details": {"amount": str(cfg.mint_usdc_amount)},
                    }
                )
                sleep_range(cfg.delay_min, cfg.delay_max)
        except Exception as exc:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "mint_usdc",
                    "status": "failed",
                    "tx_hash": None,
                    "details": {"error": str(exc)[:300]},
                }
            )
            return

        filled = 0
        attempts = 0
        max_attempts = cfg.trades_per_day * 4

        while filled < cfg.trades_per_day and attempts < max_attempts:
            self._cancel()
            attempts += 1
            try:
                offers = self.offer_pool.filter_available(fetch_offers(c, cfg))
            except Exception as exc:
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "fetch_offers",
                        "status": "failed",
                        "tx_hash": None,
                        "details": {"error": str(exc)[:300]},
                    }
                )
                break

            targets = pick_fill_targets(
                offers,
                usdc_min=cfg.trade_usdc_min,
                usdc_max=cfg.trade_usdc_max,
                count=1,
                usdc_decimals=c.usdc_decimals(),
            )
            if not targets:
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "fill",
                        "status": "skipped",
                        "tx_hash": None,
                        "details": {"reason": "no suitable free offers"},
                    }
                )
                break

            offer, amount_raw, full = targets[0]
            if not self.offer_pool.claim(offer.id):
                continue

            try:
                approve_tx = approve_market(c, cfg, amount_raw)
                if approve_tx:
                    self.emit(
                        {
                            "label": c.label,
                            "address": c.address,
                            "action": "approve_usdc",
                            "status": "ok",
                            "tx_hash": approve_tx,
                            "details": {"amount_raw": amount_raw},
                        }
                    )
                    sleep_range(2, 5)

                if full and cfg.prefer_full_fill:
                    tx_hash = fill_offer_full(c, cfg, offer.id)
                    kind = "full"
                else:
                    tx_hash = fill_offer_partial(c, cfg, offer.id, amount_raw)
                    kind = "partial"

                filled += 1
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "fill",
                        "status": "ok",
                        "tx_hash": tx_hash,
                        "details": {
                            "offer_id": offer.id,
                            "kind": kind,
                            "usdc": str(c.from_usdc_units(amount_raw)),
                            "points_id": offer.points_id,
                        },
                    }
                )
            except Exception as exc:
                # leave claimed so others don't retry a dead offer immediately
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "fill",
                        "status": "failed",
                        "tx_hash": None,
                        "details": {
                            "offer_id": offer.id,
                            "error": _friendly_fill_error(exc),
                        },
                    }
                )
            sleep_range(cfg.delay_min, cfg.delay_max)

    def _sells(self) -> None:
        if not self._ensure_gas():
            return
        c = self.client
        cfg = self.cfg
        for _ in range(cfg.sell_repeats):
            pts = Decimal(
                str(
                    random.uniform(
                        float(cfg.sell_points_amount_min),
                        float(cfg.sell_points_amount_max),
                    )
                )
            )
            price_per = Decimal(
                str(
                    random.uniform(
                        float(cfg.sell_price_per_point_min),
                        float(cfg.sell_price_per_point_max),
                    )
                )
            )
            points_raw = int(pts * Decimal(10**18))
            price_raw = int(price_per * pts * Decimal(10 ** c.usdc_decimals()))
            collateral_raw = c.to_usdc_units(cfg.sell_collateral)
            try:
                if collateral_raw > 0:
                    approve_market(c, cfg, collateral_raw)
                tx_hash = create_offer(
                    c,
                    cfg,
                    points_id=cfg.points_id,
                    points_amount_raw=points_raw,
                    price_raw=price_raw,
                    collateral_raw=collateral_raw,
                )
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "create_offer",
                        "status": "ok",
                        "tx_hash": tx_hash,
                        "details": {
                            "points": str(pts),
                            "price_usdc": str(c.from_usdc_units(price_raw)),
                        },
                    }
                )
            except Exception as exc:
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "create_offer",
                        "status": "failed",
                        "tx_hash": None,
                        "details": {"error": str(exc)[:300]},
                    }
                )
            sleep_range(cfg.delay_min, cfg.delay_max)
