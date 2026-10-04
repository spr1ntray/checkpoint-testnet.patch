from __future__ import annotations

import random
import time
from decimal import Decimal
from typing import Any, Callable

from eth_abi import encode
from web3 import Web3

from .auth_siwe import siwe_login
from .client import CheckpointClient
from .config import AppConfig
from .daily_cap import DAILY_ACTION_LIMIT, DailyActionCap, DailyCapError
from .deposit import try_deposit
from .faucet import FaucetEligibilityError
from .kernel_aa import KernelAccount, LowGasError
from .market import create_offer
from .market_api import (
    OFFER_POLL_MAX_SEC,
    OFFER_POLL_MIN_SEC,
    OFFER_WAIT_SECONDS,
    fetch_offers,
    list_markets,
    pick_fill_targets,
)
from .offer_pool import OfferPool
from .rewards import fetch_rewards
from .session_plan import SessionPlan, plan_session, split_budget
from .usdc import approve_market
from .timing import SessionStyle, action_delay, sleep_jitter
from .utils import scrub_secrets
from .abis import SEL_FILL_FULL, SEL_FILL_PARTIAL

SEL_MINT = bytes.fromhex("40c10f19")
SEL_APPROVE = bytes.fromhex("095ea7b3")


Emit = Callable[[dict[str, Any]], None]


def kernel_fill_calls(
    *,
    usdc: str,
    market: str,
    approve_data: bytes,
    fill_data: bytes,
    mint_data: bytes | None = None,
) -> list[tuple[str, bytes, int]]:
    """Kernel execute list for one fill. Mint is first when Kernel USDC is low.

    Checkpoint UI mints test USDC in the same UserOp as the first daily trade.
    A mint-only UserOp on an undeployed Kernel reverts in
    eth_estimateUserOperationGas with reason 0x.
    """
    calls: list[tuple[str, bytes, int]] = []
    if mint_data:
        calls.append((usdc, mint_data, 0))
    calls.append((usdc, approve_data, 0))
    calls.append((market, fill_data, 0))
    return calls


def kernel_needs_mint(kernel_usdc_raw: int, fill_raw: int) -> bool:
    """Mint more test USDC when this fill would exceed Kernel balance."""
    return int(kernel_usdc_raw) < int(fill_raw)


def _safe_exc(exc: Exception, limit: int = 240) -> str:
    """Exception text safe for Hub log/result (no proxy/key/jwt fragments)."""
    text = scrub_secrets(str(exc).replace("\n", " ").strip())
    return (text[:limit] if len(text) > limit else text) or type(exc).__name__


def _is_low_gas(exc: Exception) -> bool:
    """Only our explicit empty-EOA signal. Bundler AA21/AA23 is not an empty wallet."""
    return isinstance(exc, LowGasError)


def _is_erc20_usdc(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "erc20",
            "4552433230",  # ASCII "ERC20" in revert hex
            "transfer amount exceeds",
            "insufficient allowance",
        )
    )


def _friendly_fill_error(exc: Exception) -> str:
    text = scrub_secrets(str(exc))
    if "0xe28caf6a" in text or "CannotFillOffer" in text:
        return "CannotFillOffer — offer уже занят/заполнен (гонка)"
    if _is_erc20_usdc(exc):
        return "Не хватает USDC на Kernel — доминтим со следующей сделкой"
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
        session: SessionStyle | None = None,
        offer_wait_seconds: float | None = None,
        fund_gas: Callable[[], bool] | None = None,
        daily_cap: DailyActionCap | None = None,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.emit = emit
        self.mode = mode
        self.offer_pool = offer_pool or OfferPool()
        self.capsolver_api_key = capsolver_api_key
        self.cancel_check = cancel_check
        self.session = session
        self.fund_gas = fund_gas
        self.daily_cap = daily_cap
        self.kernel: KernelAccount | None = None
        self.offer_wait_seconds = (
            OFFER_WAIT_SECONDS if offer_wait_seconds is None else float(offer_wait_seconds)
        )

    def _cancel(self) -> None:
        if self.cancel_check:
            self.cancel_check()

    def _send_user_op(self, kernel: KernelAccount, calls: list[tuple[str, bytes, int]]) -> str:
        if self.daily_cap is not None and not self.daily_cap.try_consume(self.client.address):
            raise DailyCapError("daily action cap")
        return kernel.send_calls(calls)

    def _best_rewards(self) -> Any:
        snaps = [fetch_rewards(self.client, self.cfg)]
        if self.kernel is not None:
            try:
                snaps.append(fetch_rewards(self.client, self.cfg, self.kernel.sender))
            except Exception:
                pass
        return max(snaps, key=lambda s: s.total_points)

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

        if self.mode in {"full", "daily"}:
            try:
                self.kernel = KernelAccount(c, self.cfg)
            except Exception:
                self.kernel = None

        before = None
        try:
            before = self._best_rewards()
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
                    "details": {"error": _safe_exc(exc)},
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
            after = self._best_rewards()
            delta = after.total_points - (before.total_points if before else 0)
            if delta == 0 and self.mode in {"full", "daily"}:
                sleep_jitter(8, 14, cancel_check=self.cancel_check)
                after = self._best_rewards()
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
                    "details": {"error": _safe_exc(exc)},
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
            try:
                self.kernel = self.kernel or KernelAccount(c, self.cfg)
            except Exception:
                pass
            xp = self._best_rewards()
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
                "error": _safe_exc(exc, 200),
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
            # SIWE is optional for buy XP (Kernel UserOps). Don't fail the farm.
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "siwe",
                    "status": "skipped",
                    "tx_hash": None,
                    "details": {"error": _safe_exc(exc, 400)},
                }
            )

    def _skip_low_gas(self, action: str, exc: Exception | None = None) -> None:
        eth = ""
        need = "> 0"
        if isinstance(exc, LowGasError):
            eth = exc.eth
            need = exc.need
        else:
            try:
                eth = f"{self.client.eth_balance():.9f}"
            except Exception:
                eth = "0"
        self.emit(
            {
                "label": self.client.label,
                "address": self.client.address,
                "action": action,
                "status": "skipped",
                "tx_hash": None,
                "details": {
                    "reason": "low_gas",
                    "error": _safe_exc(exc) if exc is not None else None,
                    "eth": eth or "0",
                    "need": need,
                    "hint": "На кошельке мало ETH (Arbitrum Sepolia)",
                },
            }
        )

    def _ensure_gas(self) -> bool:
        c = self.client
        eth = c.eth_balance()
        # Do not invent a "minimum gas" reserve. One Sepolia fill is tiny.
        # Block only empty wallets; otherwise let the tx fail on-chain if needed.
        if eth <= 0:
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
                        "need": "> 0",
                        "hint": "На кошельке 0 ETH (Arbitrum Sepolia)",
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
                        "details": {"points_id": pid, "error": _safe_exc(exc)},
                    }
                )
            action_delay(
                self.cfg.delay_min,
                self.cfg.delay_max,
                cancel_check=self.cancel_check,
                style=self.session,
            )

    def _xp_address(self, kernel: KernelAccount | None) -> str:
        return kernel.sender if kernel is not None else self.client.address

    def _trades(self) -> None:
        # Don't skip on empty EOA: ZeroDev paymaster can sponsor Kernel UserOps
        # on Arbitrum Sepolia without ETH on the owner.
        c = self.client
        cfg = self.cfg
        kernel = self.kernel
        try:
            if kernel is None:
                kernel = KernelAccount(c, cfg)
                self.kernel = kernel
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "kernel",
                    "status": "ok",
                    "tx_hash": None,
                    "details": {"kernel": kernel.sender, "deployed": kernel.is_deployed()},
                }
            )
        except Exception as exc:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "kernel",
                    "status": "failed",
                    "tx_hash": None,
                    "details": {"error": _safe_exc(exc)},
                }
            )
            return

        usdc = Web3.to_checksum_address(cfg.usdc)
        market = Web3.to_checksum_address(cfg.market)
        decimals = c.usdc_decimals()

        try:
            usdc_raw = int(c.usdc.functions.balanceOf(kernel.sender).call())
        except Exception as exc:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "mint_usdc",
                    "status": "failed",
                    "tx_hash": None,
                    "details": {"error": _safe_exc(exc)},
                }
            )
            return

        mint_units = c.to_usdc_units(cfg.mint_usdc_amount)
        quota = (
            self.daily_cap.remaining(c.address)
            if self.daily_cap is not None
            else DAILY_ACTION_LIMIT
        )
        if quota <= 0:
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "fill",
                    "status": "skipped",
                    "tx_hash": None,
                    "details": {
                        "reason": "daily_cap",
                        "limit": DAILY_ACTION_LIMIT,
                        "hint": "Дневной лимит 12 действий исчерпан",
                    },
                }
            )
            return
        plan = plan_session(trades_min=cfg.trades_min, trades_max=cfg.trades_max)
        if plan.trades > quota:
            plan = SessionPlan(
                budget_usdc=plan.budget_usdc,
                trades=quota,
                fills=split_budget(plan.budget_usdc, quota),
            )
        self.emit(
            {
                "label": c.label,
                "address": c.address,
                "action": "session_plan",
                "status": "ok",
                "tx_hash": None,
                "details": {
                    "budget_usdc": str(plan.budget_usdc),
                    "trades": plan.trades,
                    "fills": [str(x) for x in plan.fills],
                    "daily_quota": quota,
                    "daily_limit": DAILY_ACTION_LIMIT,
                },
            }
        )

        filled = 0
        attempts = 0
        target_fills = plan.trades
        max_attempts = min(quota, DAILY_ACTION_LIMIT)
        markets = list_markets(c, cfg)
        market_idx = 0
        dry_since: float | None = None
        halted = False

        while filled < target_fills and attempts < max_attempts:
            self._cancel()
            if market_idx >= len(markets):
                break
            points_id = markets[market_idx].points_id
            try:
                offers = self.offer_pool.filter_available(
                    fetch_offers(c, cfg, points_id=points_id)
                )
            except Exception as exc:
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "fetch_offers",
                        "status": "failed",
                        "tx_hash": None,
                        "details": {"error": _safe_exc(exc), "points_id": points_id},
                    }
                )
                offers = []

            planned = plan.fills[filled]
            usdc_min = max(
                cfg.trade_usdc_min,
                (planned * Decimal("0.35")).quantize(Decimal("0.01")),
            )
            targets = pick_fill_targets(
                offers,
                usdc_min=usdc_min,
                usdc_max=planned,
                count=1,
                usdc_decimals=decimals,
            )
            if not targets:
                targets = pick_fill_targets(
                    offers,
                    usdc_min=cfg.trade_usdc_min,
                    usdc_max=planned,
                    count=1,
                    usdc_decimals=decimals,
                )
            if not targets:
                now = time.monotonic()
                wait_s = max(0.0, self.offer_wait_seconds)
                if dry_since is None and wait_s > 0:
                    dry_since = now
                    self.emit(
                        {
                            "label": c.label,
                            "address": c.address,
                            "action": "offer_wait",
                            "status": "ok",
                            "tx_hash": None,
                            "details": {
                                "points_id": points_id,
                                "wait_s": int(wait_s),
                                "active": markets[market_idx].active_offers,
                            },
                        }
                    )
                if dry_since is None or now - dry_since >= wait_s:
                    nxt = market_idx + 1
                    self.emit(
                        {
                            "label": c.label,
                            "address": c.address,
                            "action": "offer_switch",
                            "status": "ok",
                            "tx_hash": None,
                            "details": {
                                "from_points_id": points_id,
                                "to_points_id": (
                                    markets[nxt].points_id if nxt < len(markets) else None
                                ),
                                "waited_s": int(0 if dry_since is None else now - dry_since),
                            },
                        }
                    )
                    market_idx = nxt
                    dry_since = None
                    continue
                poll_hi = min(OFFER_POLL_MAX_SEC, wait_s)
                poll_lo = min(OFFER_POLL_MIN_SEC, poll_hi)
                sleep_jitter(poll_lo, poll_hi, cancel_check=self.cancel_check)
                continue

            dry_since = None
            offer, amount_raw, full = targets[0]
            if not self.offer_pool.claim(offer.id):
                continue
            attempts += 1

            try:
                approve_amount = max(amount_raw, c.to_usdc_units(cfg.mint_usdc_amount))
                approve_data = SEL_APPROVE + encode(
                    ["address", "uint256"], [market, approve_amount]
                )
                if full:
                    fill_data = SEL_FILL_FULL + encode(["uint256"], [int(offer.id)])
                    kind = "full"
                else:
                    fill_data = SEL_FILL_PARTIAL + encode(
                        ["uint256", "uint256"], [int(offer.id), int(amount_raw)]
                    )
                    kind = "partial"
                batched_mint = kernel_needs_mint(usdc_raw, amount_raw)
                mint_data = None
                if batched_mint:
                    mint_data = SEL_MINT + encode(
                        ["address", "uint256"], [kernel.sender, mint_units]
                    )
                calls = kernel_fill_calls(
                    usdc=usdc,
                    market=market,
                    approve_data=approve_data,
                    fill_data=fill_data,
                    mint_data=mint_data,
                )
                try:
                    tx_hash = self._send_user_op(kernel, calls)
                except DailyCapError:
                    self.emit(
                        {
                            "label": c.label,
                            "address": c.address,
                            "action": "fill",
                            "status": "skipped",
                            "tx_hash": None,
                            "details": {
                                "reason": "daily_cap",
                                "limit": DAILY_ACTION_LIMIT,
                                "hint": "Дневной лимит 12 действий исчерпан",
                            },
                        }
                    )
                    halted = True
                    break
                except Exception as send_exc:
                    if (
                        _is_low_gas(send_exc)
                        and not _is_erc20_usdc(send_exc)
                        and self.fund_gas
                        and self.fund_gas()
                    ):
                        tx_hash = self._send_user_op(kernel, calls)
                    else:
                        raise
                if batched_mint:
                    usdc_raw += mint_units
                    self.emit(
                        {
                            "label": c.label,
                            "address": c.address,
                            "action": "mint_usdc",
                            "status": "ok",
                            "tx_hash": tx_hash,
                            "details": {
                                "amount": str(cfg.mint_usdc_amount),
                                "via": "kernel_with_fill",
                                "offer_id": offer.id,
                            },
                        }
                    )
                usdc_raw -= amount_raw
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
                            "via": "kernel",
                            "minted": batched_mint,
                        },
                    }
                )
            except DailyCapError:
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "fill",
                        "status": "skipped",
                        "tx_hash": None,
                        "details": {
                            "reason": "daily_cap",
                            "limit": DAILY_ACTION_LIMIT,
                            "hint": "Дневной лимит 12 действий исчерпан",
                        },
                    }
                )
                halted = True
                break
            except FaucetEligibilityError:
                raise
            except Exception as exc:
                if _is_low_gas(exc) and not _is_erc20_usdc(exc):
                    self._skip_low_gas("fill", exc)
                    halted = True
                    break
                if _is_erc20_usdc(exc):
                    usdc_raw = 0
                    self.emit(
                        {
                            "label": c.label,
                            "address": c.address,
                            "action": "fill",
                            "status": "skipped",
                            "tx_hash": None,
                            "details": {
                                "offer_id": offer.id,
                                "reason": "low_usdc",
                                "hint": _friendly_fill_error(exc),
                            },
                        }
                    )
                    continue
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
                            "batched_mint": kernel_needs_mint(usdc_raw, amount_raw),
                        },
                    }
                )
            action_delay(
                cfg.delay_min,
                cfg.delay_max,
                cancel_check=self.cancel_check,
                style=self.session,
            )

        if filled < target_fills and not halted:
            if filled == 0 and kernel_needs_mint(usdc_raw, mint_units):
                self.emit(
                    {
                        "label": c.label,
                        "address": c.address,
                        "action": "mint_usdc",
                        "status": "skipped",
                        "tx_hash": None,
                        "details": {
                            "reason": "no_offers",
                            "hint": "Mint только вместе со сделкой; offers нет — mint не шлём",
                        },
                    }
                )
            self.emit(
                {
                    "label": c.label,
                    "address": c.address,
                    "action": "fill",
                    "status": "skipped",
                    "tx_hash": None,
                    "details": {
                        "reason": "no_offers",
                        "hint": "Нет подходящих offers — ждали рынки и ушли дальше",
                    },
                }
            )

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
                        "details": {"error": _safe_exc(exc)},
                    }
                )
            action_delay(cfg.delay_min, cfg.delay_max, cancel_check=self.cancel_check)
