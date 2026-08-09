from __future__ import annotations

import re
import threading
from decimal import Decimal
from typing import Any

from eth_account import Account

from checkpoint_bot.accounts import AccountConfig, normalize_private_key, normalize_proxy
from checkpoint_bot.actions import WalletActionRunner
from checkpoint_bot.client import CheckpointClient
from checkpoint_bot.config import AppConfig
from checkpoint_bot.offer_pool import OfferPool
from checkpoint_bot.referral import (
    address_from_account,
    fetch_referral_status,
    make_session,
    register_portfolio_address,
    submit_referral,
)
from soft_hub.sdk import CancelledError, HubAccount, HubContext


CHAIN_ID = 421614
# Soft floor for Arbitrum Sepolia — L2 fills are cheap; old 0.0002 blocked funded-but-dust wallets.
MIN_ETH = Decimal("0.00005")
ACTION_MODES = {
    "inspect": "parse",
    "farm": "daily",
}
RPC_PRIMARY = "https://arbitrum-sepolia-rpc.publicnode.com"
RPC_FALLBACKS = [
    "https://sepolia-rollup.arbitrum.io/rpc",
    "https://arbitrum-sepolia.gateway.tenderly.co",
    "https://arbitrum-sepolia.public.blastapi.io",
]
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def run(context: HubContext) -> dict[str, Any]:
    mode = ACTION_MODES.get(context.action_id)
    if mode is None:
        raise ValueError(f"unsupported_action:{context.action_id}")

    cfg = _build_config(context)
    offer_pool = OfferPool()
    lock = threading.Lock()
    counters: dict[str, int] = {
        "total": len(context.accounts),
        "succeeded": 0,
        "partial": 0,
        "failed": 0,
        "skipped": 0,
        "blocked": 0,
        "needs_attention": 0,
        "cancelled": 0,
        "transactions": 0,
        "registered": 0,
        "referral_linked": 0,
        "referral_mismatch": 0,
    }
    workers = max(
        1,
        min(int(getattr(context, "account_concurrency", 1) or 1), len(context.accounts) or 1),
    )

    # Work (farm) respects referral levels so parents register before children.
    # Parse has no referral side effects — process all accounts flat.
    if mode == "daily":
        levels = _referral_levels(context)
    else:
        levels = (tuple(context.accounts),)

    context.log(
        "Старт Checkpoint",
        data={
            "mode": mode,
            "accounts": len(context.accounts),
            "levels": len(levels),
            "account_concurrency": workers,
            "auto_register": mode == "daily",
        },
    )

    def worker(hub_account: HubAccount) -> str:
        try:
            status, meta = _run_account(
                context,
                hub_account,
                cfg=cfg,
                mode=mode,
                offer_pool=offer_pool,
                counters=counters,
                counters_lock=lock,
            )
        except CancelledError:
            with lock:
                counters["cancelled"] = counters.get("cancelled", 0) + 1
            raise
        except Exception:
            with lock:
                counters["failed"] = counters.get("failed", 0) + 1
            try:
                context.account_state(
                    hub_account.id,
                    status="failed",
                    stage="automation_failed",
                    message="Ошибка обработки аккаунта",
                )
            except Exception:
                pass
            return "failed"
        with lock:
            counters[status] = counters.get(status, 0) + 1
            if meta.get("registered"):
                counters["registered"] = counters.get("registered", 0) + 1
            if meta.get("referral_linked"):
                counters["referral_linked"] = counters.get("referral_linked", 0) + 1
            if meta.get("referral_mismatch"):
                counters["referral_mismatch"] = counters.get("referral_mismatch", 0) + 1
        return status

    for level_index, level in enumerate(levels):
        context.check_cancelled()
        if mode == "daily" and len(levels) > 1:
            context.log(
                f"Уровень реферальной цепи {level_index}",
                data={"accounts": len(level), "level": level_index},
            )
        if hasattr(context, "map_accounts"):
            context.map_accounts(worker, accounts=level)
        else:
            for account in level:
                context.check_cancelled()
                worker(account)

    return {
        "total": counters["total"],
        "succeeded": counters["succeeded"],
        "partial": counters["partial"],
        "failed": counters["failed"],
        "skipped": counters["skipped"],
        "blocked": counters["blocked"],
        "needs_attention": counters["needs_attention"],
        "cancelled": counters["cancelled"],
        "transactions": counters["transactions"],
        "registered": counters.get("registered", 0),
        "referral_linked": counters.get("referral_linked", 0),
        "referral_mismatch": counters.get("referral_mismatch", 0),
        "levels": len(levels),
        "chain_id": CHAIN_ID,
        "mode": mode,
        "account_concurrency": workers,
    }


def _referral_levels(context: HubContext) -> tuple[tuple[HubAccount, ...], ...]:
    levels = getattr(context, "referral_levels", None)
    if levels is not None:
        try:
            materialised = tuple(tuple(level) for level in levels)
            if materialised:
                return materialised
        except Exception:
            pass
    return (tuple(context.accounts),)


def _parent_for(context: HubContext, child: HubAccount) -> HubAccount | None:
    referrals = getattr(context, "referrals", None)
    if referrals is None:
        return None
    parent_for = getattr(referrals, "parent_for", None)
    if not callable(parent_for):
        return None
    try:
        return parent_for(child.id)
    except KeyError:
        return None


def _ensure_registered(
    context: HubContext,
    hub_account: HubAccount,
    *,
    cfg: AppConfig,
    account: AccountConfig,
    child_address: str,
) -> dict[str, bool]:
    """
    Portfolio index + Hub referral topology (if parent exists).

    New accounts are registered automatically during Work.
    Wrong existing referrer → log + continue (never hard-fail the farm).
    """
    meta = {
        "registered": False,
        "referral_linked": False,
        "referral_mismatch": False,
        "is_root": False,
    }
    session = make_session(account.proxy)

    context.account_state(
        hub_account.id,
        status="running",
        stage="register",
        progress=0.08,
        message="Проверяем / регистрируем в Checkpoint",
    )
    context.check_cancelled()

    try:
        register_portfolio_address(session, cfg, child_address)
        meta["registered"] = True
    except Exception as exc:
        # Portfolio re-index is soft; farm can continue even if it flakes.
        context.log(
            "Portfolio register soft-fail — продолжаем",
            level="warning",
            account_id=hub_account.id,
            data={"error": _safe_error(exc)[:160]},
        )

    parent = _parent_for(context, hub_account)
    if parent is None:
        meta["is_root"] = True
        context.log(
            "Root / без parent в топологии — реферал не нужен",
            account_id=hub_account.id,
            data={"is_root": True},
        )
        return meta

    project_code = (getattr(parent, "evm_address", None) or "").strip()
    if not project_code or not project_code.startswith("0x") or len(project_code) < 42:
        context.log(
            "У parent нет EVM-адреса — реферал пропущен",
            level="warning",
            account_id=hub_account.id,
        )
        return meta

    if hasattr(context, "protect_secret"):
        project_code = context.protect_secret(project_code)

    context.account_state(
        hub_account.id,
        status="running",
        stage="referral",
        progress=0.12,
        message="Сажаем на рефералку parent (если нужно)",
    )
    context.check_cancelled()

    try:
        existing = fetch_referral_status(session, cfg, child_address)
    except Exception as exc:
        context.log(
            "Не удалось прочитать referral status — пробуем создать",
            level="warning",
            account_id=hub_account.id,
            data={"error": _safe_error(exc)[:160]},
        )
        existing = {}

    existing_by = str(existing.get("referredBy") or "").strip().lower()
    if existing_by:
        if existing_by == project_code.lower():
            meta["referral_linked"] = True
            context.log(
                "Реферал уже на parent из цепи Hub",
                account_id=hub_account.id,
                data={"status": existing.get("status")},
            )
        else:
            # User instruction: log and ignore — do not block farm.
            meta["referral_mismatch"] = True
            context.log(
                "Аккаунт уже на другом referrer (не из цепи Hub) — оставляем как есть",
                level="warning",
                account_id=hub_account.id,
                data={"mismatch": True, "status": existing.get("status")},
            )
        project_code = ""
        return meta

    try:
        submit = submit_referral(
            session,
            cfg,
            referee=child_address,
            referrer=project_code,
        )
        meta["referral_linked"] = True
        context.log(
            "Новый аккаунт посажен на parent из цепи Hub",
            account_id=hub_account.id,
            data={
                "created": bool(submit.get("created")),
                "status": submit.get("status") or "pending",
            },
        )
    except Exception as exc:
        # Soft: registration flake must not kill the whole farm run.
        context.log(
            "Реферал не создался — фарм продолжаем",
            level="warning",
            account_id=hub_account.id,
            data={"error": _safe_error(exc)[:160]},
        )
    finally:
        project_code = ""

    return meta


def _run_account(
    context: HubContext,
    hub_account: HubAccount,
    *,
    cfg: AppConfig,
    mode: str,
    offer_pool: OfferPool,
    counters: dict[str, int],
    counters_lock: threading.Lock,
) -> tuple[str, dict[str, bool]]:
    context.check_cancelled()
    context.account_state(
        hub_account.id,
        status="running",
        stage="preflight",
        progress=0.05,
        message="Проверяем ключ, proxy и RPC",
    )

    write_may_have_happened = False
    cancelled: CancelledError | None = None
    terminal_status = "failed"
    terminal_stage = "automation_failed"
    terminal_message = "Сценарий завершился ошибкой"
    parse_row: dict[str, Any] = {}
    last_skip_reason = ""
    meta = {
        "registered": False,
        "referral_linked": False,
        "referral_mismatch": False,
        "is_root": False,
    }

    try:
        account = _account_config(hub_account)
        child_address = address_from_account(account)
        if hub_account.evm_address and child_address.lower() != hub_account.evm_address.lower():
            raise RuntimeError("bad_key: private key ≠ Hub address")

        # Work mode: auto-register new wallets along Hub referral topology first.
        if mode == "daily":
            reg_meta = _ensure_registered(
                context,
                hub_account,
                cfg=cfg,
                account=account,
                child_address=child_address,
            )
            meta.update(reg_meta)

        client = CheckpointClient(cfg, account)
        _preflight(client, hub_account)

        context.account_state(
            hub_account.id,
            status="running",
            stage="automation",
            progress=0.18 if mode == "daily" else 0.15,
            message="Собираем данные" if mode == "parse" else "Запускаем фарм",
        )

        event_stats = {
            "failed": 0,
            "skipped": 0,
            "transactions": 0,
            "fills_ok": 0,
            "gas_blocked": False,
            "no_offers": False,
        }

        def emit(event: dict[str, Any]) -> None:
            nonlocal write_may_have_happened, last_skip_reason, parse_row
            context.check_cancelled()
            action = str(event.get("action") or "checkpoint")
            status = str(event.get("status") or "unknown")
            details = _public_data(event.get("details") or {})
            tx_hash = event.get("tx_hash")

            if status == "failed":
                event_stats["failed"] += 1
            if status == "skipped":
                event_stats["skipped"] += 1
                reason = str(details.get("reason") or "")
                if reason == "low_gas" or action == "gas_check":
                    event_stats["gas_blocked"] = True
                    last_skip_reason = (
                        f"Мало ETH: {details.get('eth')} "
                        f"(нужно ≥ {details.get('need')})"
                    )
                elif reason in {"no_offers", "no suitable free offers"}:
                    event_stats["no_offers"] = True
                    last_skip_reason = "Нет подходящих offers в заданном диапазоне USDC"
                elif details.get("reason"):
                    last_skip_reason = str(details.get("reason"))[:160]
            if action in {"mint_usdc", "approve_usdc", "fill"} and status == "ok":
                write_may_have_happened = True
            if action == "fill" and status == "ok":
                event_stats["fills_ok"] += 1
            if action == "parse" and status in {"ok", "failed"}:
                parse_row = {
                    "eth": details.get("eth", "0"),
                    "usdc": details.get("usdc", "0"),
                    "xp_total": int(float(details.get("xp") or 0)),
                    "xp_buy": int(float(details.get("buy_xp") or 0)),
                    "xp_deposit": int(float(details.get("deposit_xp") or 0)),
                    "xp_sell": int(float(details.get("sell_xp") or 0)),
                    "rank": details.get("rank"),
                }
                if parse_row["rank"] is None:
                    parse_row["rank"] = 0
            if tx_hash:
                event_stats["transactions"] += 1
                with counters_lock:
                    counters["transactions"] += 1
                details = {**details, "tx_hash": str(tx_hash)}
                context.result(
                    f"{action}: tx подтверждена",
                    kind="checkpoint_tx",
                    status="succeeded",
                    account_id=hub_account.id,
                    data=details,
                )

            progress_map = {
                "parse": 0.70,
                "mint_usdc": 0.40,
                "approve_usdc": 0.45,
                "fill": min(0.50 + 0.08 * max(event_stats["fills_ok"], 1), 0.90),
                "xp_report": 0.94,
                "gas_check": 0.25,
            }
            if action in progress_map and status in {"ok", "skipped", "failed"}:
                stage = action if _STAGE_RE.fullmatch(action) else "automation"
                context.account_state(
                    hub_account.id,
                    status="running",
                    stage=stage,
                    progress=float(progress_map[action]),
                    message=f"{action}: {status}",
                )

            level = "error" if status == "failed" else "warning" if status == "skipped" else "info"
            context.log(
                f"{action}: {status}",
                level=level,
                account_id=hub_account.id,
                data=details,
            )

        WalletActionRunner(
            cfg,
            client,
            emit,
            mode=mode,
            offer_pool=offer_pool,
            capsolver_api_key="",
            cancel_check=context.check_cancelled,
        ).run()

        if mode == "parse":
            if event_stats["failed"]:
                terminal_status = "failed"
                terminal_stage = "automation_failed"
                terminal_message = "Не удалось прочитать XP/балансы"
            else:
                terminal_status = "succeeded"
                terminal_stage = "completed"
                terminal_message = "Парсинг завершён"
            context.result(
                f"{hub_account.label}: статистика",
                kind="account_snapshot",
                status=terminal_status if terminal_status in {"succeeded", "failed"} else "failed",
                account_id=hub_account.id,
                data={
                    "eth": str(parse_row.get("eth", "0")),
                    "usdc": str(parse_row.get("usdc", "0")),
                    "xp_total": int(parse_row.get("xp_total") or 0),
                    "xp_buy": int(parse_row.get("xp_buy") or 0),
                    "xp_deposit": int(parse_row.get("xp_deposit") or 0),
                    "xp_sell": int(parse_row.get("xp_sell") or 0),
                    "rank": int(parse_row.get("rank") or 0),
                },
            )
        else:
            write_without_tx = event_stats["transactions"] == 0
            if event_stats["gas_blocked"] and write_without_tx:
                terminal_status = "blocked"
                terminal_stage = "blocked"
                terminal_message = last_skip_reason or "Мало ETH на газ (Arbitrum Sepolia)"
            elif event_stats["no_offers"] and write_without_tx:
                terminal_status = "blocked"
                terminal_stage = "blocked"
                terminal_message = last_skip_reason or "Нет подходящих offers"
            elif write_without_tx and event_stats["skipped"] and not event_stats["failed"]:
                terminal_status = "blocked"
                terminal_stage = "blocked"
                terminal_message = last_skip_reason or "Фарм пропущен"
            elif write_without_tx or (event_stats["failed"] and not event_stats["transactions"]):
                terminal_status = "failed"
                terminal_stage = "automation_failed"
                terminal_message = "Транзакции не подтверждены"
            elif event_stats["failed"] and event_stats["transactions"]:
                terminal_status = "partial"
                terminal_stage = "completed"
                terminal_message = "Часть fills подтверждена"
            else:
                terminal_status = "succeeded"
                terminal_stage = "completed"
                terminal_message = "Работа завершена"
                write_may_have_happened = False
            context.result(
                f"{hub_account.label}: Checkpoint {terminal_status}",
                kind="account_summary",
                status=terminal_status,
                account_id=hub_account.id,
                data={
                    "address": client.address,
                    "transactions": event_stats["transactions"],
                    "fills_ok": event_stats["fills_ok"],
                    "failed_events": event_stats["failed"],
                    "skipped_events": event_stats["skipped"],
                    "registered": meta.get("registered", False),
                    "referral_linked": meta.get("referral_linked", False),
                    "referral_mismatch": meta.get("referral_mismatch", False),
                    "mode": mode,
                },
            )

    except CancelledError as error:
        cancelled = error
        if write_may_have_happened:
            terminal_status = "needs_attention"
            terminal_stage = "needs_reconciliation"
            terminal_message = "Остановка после возможного write — сверьте chain"
        else:
            terminal_status = "cancelled"
            terminal_stage = "cancelled"
            terminal_message = "Остановлено до внешнего write"
    except Exception as exc:
        code = _classify_error(exc)
        if write_may_have_happened:
            terminal_status = "needs_attention"
            terminal_stage = "needs_reconciliation"
            terminal_message = "Возможный write без подтверждения — сверьте chain"
        elif code in {"missing_secret", "bad_key", "wrong_chain", "low_gas"}:
            terminal_status = "blocked"
            terminal_stage = "preflight_blocked"
            terminal_message = _safe_error(exc)
        else:
            terminal_status = "failed"
            terminal_stage = "automation_failed"
            terminal_message = _safe_error(exc)
        if mode == "parse":
            context.result(
                f"{hub_account.label}: статистика",
                kind="account_snapshot",
                status="failed" if terminal_status == "failed" else "blocked",
                account_id=hub_account.id,
                data={
                    "eth": "0",
                    "usdc": "0",
                    "xp_total": 0,
                    "xp_buy": 0,
                    "xp_deposit": 0,
                    "xp_sell": 0,
                    "rank": 0,
                },
            )
        context.log(
            "Аккаунт завершился ошибкой",
            level="error",
            account_id=hub_account.id,
            data={"error_code": code},
        )

    context.account_state(
        hub_account.id,
        status=terminal_status,
        stage=terminal_stage,
        progress=1.0 if terminal_status in {"succeeded", "partial"} else None,
        message=terminal_message,
    )

    if cancelled is not None:
        raise cancelled
    return terminal_status, meta


def _build_config(context: HubContext) -> AppConfig:
    options = context.options or {}
    delay = _clamp_float(options.get("delay_seconds", 8), 0.0, 60.0, 8.0)
    # Checkpoint XP: +5 per trade, max 5 trades/day → 25 XP daily cap (UI: "25 XP daily cap").
    trades = int(_clamp_float(options.get("trades", 5), 1, 5, 5))
    # Per-fill notional is independent of XP: bigger fill ≠ more XP. Soft default
    # raised so offers within the ~$500/day testnet buy budget can be taken.
    max_usdc = Decimal(str(_clamp_float(options.get("max_usdc_per_fill", 100), 0.01, 500.0, 100.0)))
    # Mint enough test USDC for a full day of fills under the notional cap.
    mint_floor = max(Decimal("50"), max_usdc * Decimal(str(trades)))
    mint_amount = min(Decimal("500"), mint_floor)

    return AppConfig(
        max_workers=1,
        shuffle_wallets=False,
        delay_min=delay,
        delay_max=delay,
        gas_multiplier=1.25,
        receipt_timeout=180,
        min_eth_balance=MIN_ETH,
        rpc_url=RPC_PRIMARY,
        rpc_fallbacks=list(RPC_FALLBACKS),
        chain_id=CHAIN_ID,
        rpc_via_proxy=False,
        market="0xf2869aCE6170F7Ab1aba1C55a3483eD7E2f8AaAE",
        registry="0xC9b2b7138ECF35f980036BbDB466d8e6437B4d9F",
        oracle="0x6973c1506a81707F431f8E93D8465a9840e8B458",
        usdc="0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773",
        market_api="https://checkpoint-data-market-api.dorimebest.workers.dev",
        oracle_api="https://oracle.checkpoint.exchange",
        rewards_api="https://checkpoint.exchange/api/rewards",
        dynamic_env_id="ad89a660-574d-46d0-9a19-3d8834d535c7",
        dynamic_api="https://app.dynamicauth.com/api/v0/sdk",
        request_timeout=45,
        points_id=5,
        trades_per_day=trades,
        trade_usdc_min=Decimal("0.01"),
        trade_usdc_max=max_usdc,
        mint_usdc_if_below=min(Decimal("50"), max_usdc),
        mint_usdc_amount=mint_amount,
        prefer_full_fill=True,
        deposit_enabled=False,
        deposit_points_ids=[5],
        sell_enabled=False,
        sell_points_amount_min=Decimal("0.01"),
        sell_points_amount_max=Decimal("0.01"),
        sell_price_per_point_min=Decimal("50"),
        sell_price_per_point_max=Decimal("50"),
        sell_collateral=Decimal("0"),
        sell_repeats=1,
        siwe_enabled=False,
        siwe_captcha=False,
        hcaptcha_sitekey="14c486da-cd2e-4648-8446-0f469696acee",
    )


def _account_config(account: HubAccount) -> AccountConfig:
    private_key = normalize_private_key(account.secret("evm_private_key"))
    try:
        proxy = normalize_proxy(account.secret("proxy"))
    except KeyError:
        proxy = None
    return AccountConfig(label=account.label, private_key=private_key, proxy=proxy)


def _preflight(client: CheckpointClient, account: HubAccount) -> None:
    derived = Account.from_key(client.account_cfg.private_key).address.lower()
    if account.evm_address and derived != account.evm_address.lower():
        raise RuntimeError("bad_key: private key ≠ Hub address")
    actual_chain = int(client.w3.eth.chain_id)
    if actual_chain != CHAIN_ID:
        raise RuntimeError(f"wrong_chain: RPC chainId={actual_chain}, need {CHAIN_ID}")


def _public_data(value: Any) -> Any:
    forbidden = (
        "private", "secret", "signature", "authorization", "jwt", "captcha",
        "password", "proxy", "token", "api_key", "apikey",
    )
    if isinstance(value, dict):
        return {
            str(k): _public_data(v)
            for k, v in value.items()
            if not any(word in str(k).lower() for word in forbidden)
        }
    if isinstance(value, list):
        return [_public_data(item) for item in value[:40]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value[:500] if isinstance(value, str) and len(value) > 500 else value
    return str(value)[:300]


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return (text[:280] if len(text) > 280 else text) or type(exc).__name__


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "secret" in text or "секрет" in text:
        return "missing_secret"
    if "private key" in text or "bad_key" in text:
        return "bad_key"
    if "wrong_chain" in text or "chainid" in text:
        return "wrong_chain"
    if "мало eth" in text or "low_gas" in text or "insufficient funds" in text:
        return "low_gas"
    if "cancelled" in text or "отмен" in text:
        return "cancelled"
    return "runtime_error"


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))
