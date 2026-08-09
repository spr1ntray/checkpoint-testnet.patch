from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

from eth_account import Account

from checkpoint_bot.accounts import AccountConfig, normalize_private_key, normalize_proxy
from checkpoint_bot.actions import WalletActionRunner
from checkpoint_bot.client import CheckpointClient
from checkpoint_bot.config import AppConfig
from checkpoint_bot.offer_pool import OfferPool
from soft_hub.sdk import CancelledError, HubAccount, HubContext


CHAIN_ID = 421614
WRITE_ACTIONS = frozenset({"daily_farm", "deposit", "full_cycle", "create_sell"})
ACTION_MODES = {
    "inspect": "parse",
    "daily_farm": "daily",
    "deposit": "deposit",
    "full_cycle": "full",
    "create_sell": "sell",
}

RPC_PRIMARY = "https://arbitrum-sepolia-rpc.publicnode.com"
RPC_FALLBACKS = [
    "https://sepolia-rollup.arbitrum.io/rpc",
    "https://arbitrum-sepolia.gateway.tenderly.co",
    "https://arbitrum-sepolia.public.blastapi.io",
]


def run(context: HubContext) -> dict[str, Any]:
    mode = ACTION_MODES.get(context.action_id)
    if mode is None:
        raise ValueError(f"unsupported_action:{context.action_id}")

    cfg = _build_config(context)
    offer_pool = OfferPool()
    capsolver_key = _optional_setting(context, "capsolver")
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
    }

    workers = max(1, min(int(getattr(context, "account_concurrency", 1) or 1), len(context.accounts) or 1))
    context.log(
        "Старт Checkpoint",
        data={
            "mode": mode,
            "accounts": len(context.accounts),
            "account_concurrency": workers,
        },
    )

    def worker(hub_account: HubAccount) -> str:
        # Expected failures stay inside worker so one account does not cancel the batch.
        try:
            status = _run_account(
                context,
                hub_account,
                cfg=cfg,
                mode=mode,
                offer_pool=offer_pool,
                capsolver_key=capsolver_key,
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
            # Lifecycle should already be terminal inside _run_account; fail-safe:
            try:
                context.account_state(
                    hub_account.id,
                    status="failed",
                    stage="automation_failed",
                    message="Необработанная ошибка worker",
                )
            except Exception:
                pass
            return "failed"
        with lock:
            counters[status] = counters.get(status, 0) + 1
        return status

    if hasattr(context, "map_accounts"):
        context.map_accounts(worker)
    else:
        # Legacy Soft Hub without map_accounts: sequential fallback
        for account in context.accounts:
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
        "chain_id": CHAIN_ID,
        "mode": mode,
        "account_concurrency": workers,
    }


def _run_account(
    context: HubContext,
    hub_account: HubAccount,
    *,
    cfg: AppConfig,
    mode: str,
    offer_pool: OfferPool,
    capsolver_key: str,
    counters: dict[str, int],
    counters_lock: threading.Lock,
) -> str:
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
    safe_summary: dict[str, Any] = {}

    try:
        account = _account_config(hub_account)
        # Fresh client per worker — no shared web3/http session
        client = CheckpointClient(cfg, account)
        _preflight(client, hub_account)

        context.account_state(
            hub_account.id,
            status="running",
            stage="automation",
            progress=0.15,
            message=f"Запускаем режим {mode}",
        )

        event_stats = {"failed": 0, "skipped": 0, "transactions": 0, "fills_ok": 0}

        def emit(event: dict[str, Any]) -> None:
            nonlocal write_may_have_happened
            context.check_cancelled()
            action = str(event.get("action") or "checkpoint")
            status = str(event.get("status") or "unknown")
            details = _public_data(event.get("details") or {})
            tx_hash = event.get("tx_hash")

            if status == "failed":
                event_stats["failed"] += 1
            if status == "skipped":
                event_stats["skipped"] += 1
            if action in {"mint_usdc", "approve_usdc", "fill", "deposit", "create_offer"} and status == "ok":
                write_may_have_happened = True
            if action == "fill" and status == "ok":
                event_stats["fills_ok"] += 1
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
                "siwe": 0.25,
                "mint_usdc": 0.35,
                "approve_usdc": 0.40,
                "fill": min(0.40 + 0.10 * max(event_stats["fills_ok"], 1), 0.85),
                "deposit": 0.30,
                "create_offer": 0.70,
                "xp_report": 0.92,
                "parse": 0.80,
            }
            if action in progress_map and status in {"ok", "skipped", "failed"}:
                stage = action if _valid_stage(action) else "automation"
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
            capsolver_api_key=capsolver_key,
            cancel_check=context.check_cancelled,
        ).run()

        write_without_tx = context.action_id in WRITE_ACTIONS and event_stats["transactions"] == 0
        if mode == "parse":
            terminal_status = "succeeded"
            terminal_stage = "completed"
            terminal_message = "XP и балансы прочитаны"
        elif write_without_tx and event_stats["failed"] == 0 and event_stats["skipped"] > 0:
            terminal_status = "blocked"
            terminal_stage = "blocked"
            terminal_message = "Нет подходящих offers / мало газа"
        elif write_without_tx or (event_stats["failed"] > 0 and event_stats["transactions"] == 0):
            terminal_status = "failed"
            terminal_stage = "automation_failed"
            terminal_message = "Транзакции не подтверждены"
        elif event_stats["failed"] > 0 and event_stats["transactions"] > 0:
            terminal_status = "partial"
            terminal_stage = "completed"
            terminal_message = "Часть fills подтверждена"
        else:
            terminal_status = "succeeded"
            terminal_stage = "completed"
            terminal_message = "Сценарий подтверждён"
            write_may_have_happened = False

        safe_summary = {
            "address": client.address,
            "transactions": event_stats["transactions"],
            "fills_ok": event_stats["fills_ok"],
            "failed_events": event_stats["failed"],
            "skipped_events": event_stats["skipped"],
            "mode": mode,
        }

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
            terminal_stage = "resource_missing" if code == "missing_secret" else "preflight_blocked"
            terminal_message = _safe_error(exc)
        else:
            terminal_status = "failed"
            terminal_stage = "automation_failed"
            terminal_message = _safe_error(exc)
        safe_summary = {"error_code": code}
        context.log(
            "Аккаунт завершился ошибкой",
            level="error",
            account_id=hub_account.id,
            data={"error_code": code},
        )

    if terminal_status in {"succeeded", "partial", "failed", "blocked"} and safe_summary:
        context.result(
            f"{hub_account.label}: Checkpoint {terminal_status}",
            kind="account_summary",
            status=terminal_status,
            account_id=hub_account.id,
            data=safe_summary,
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
    return terminal_status


def _build_config(context: HubContext) -> AppConfig:
    options = context.options or {}
    delay = _clamp_float(options.get("delay_seconds", 8), 0.0, 60.0, 8.0)
    trades = int(_clamp_float(options.get("trades", 5), 1, 5, 5))
    max_usdc = Decimal(
        str(_clamp_float(options.get("max_usdc_per_fill", 2), 0.01, 5.0, 2.0))
    )
    points = Decimal(
        str(_clamp_float(options.get("points_amount", 0.01), 0.001, 1.0, 0.01))
    )
    price = Decimal(
        str(_clamp_float(options.get("price_usdc_per_point", 50), 0.01, 1000.0, 50.0))
    )
    enable_siwe = False
    try_deposit = _as_bool(options.get("try_deposit", False))
    if context.action_id == "deposit":
        try_deposit = True
    if context.action_id == "full_cycle" and "try_deposit" not in options:
        try_deposit = False

    return AppConfig(
        max_workers=1,
        shuffle_wallets=False,
        delay_min=delay,
        delay_max=delay,
        gas_multiplier=1.25,
        receipt_timeout=180,
        min_eth_balance=Decimal("0.0002"),
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
        mint_usdc_if_below=Decimal("5"),
        mint_usdc_amount=Decimal("50"),
        prefer_full_fill=True,
        deposit_enabled=try_deposit,
        deposit_points_ids=[5],
        sell_enabled=context.action_id == "create_sell",
        sell_points_amount_min=points,
        sell_points_amount_max=points,
        sell_price_per_point_min=price,
        sell_price_per_point_max=price,
        sell_collateral=Decimal("0"),
        sell_repeats=1,
        siwe_enabled=enable_siwe,
        siwe_captcha=True,
        hcaptcha_sitekey="14c486da-cd2e-4648-8446-0f469696acee",
    )


def _account_config(account: HubAccount) -> AccountConfig:
    private_key = normalize_private_key(account.secret("evm_private_key"))
    try:
        proxy = normalize_proxy(account.secret("proxy"))
    except KeyError:
        proxy = None
    return AccountConfig(label=account.label, private_key=private_key, proxy=proxy)


def _optional_setting(context: HubContext, kind: str) -> str:
    try:
        return context.settings.secret(kind)
    except KeyError:
        return ""


def _preflight(client: CheckpointClient, account: HubAccount) -> None:
    derived = Account.from_key(client.account_cfg.private_key).address.lower()
    if account.evm_address and derived != account.evm_address.lower():
        raise RuntimeError("bad_key: private key ≠ Hub address")
    actual_chain = int(client.w3.eth.chain_id)
    if actual_chain != CHAIN_ID:
        raise RuntimeError(f"wrong_chain: RPC chainId={actual_chain}, need {CHAIN_ID}")


def _valid_stage(stage: str) -> bool:
    import re

    return bool(re.fullmatch(r"^[a-z][a-z0-9_.-]{0,63}$", stage))


def _public_data(value: Any) -> Any:
    forbidden = (
        "private",
        "secret",
        "signature",
        "authorization",
        "jwt",
        "captcha",
        "password",
        "proxy",
        "token",
        "api_key",
        "apikey",
    )
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if any(word in str(key).lower() for word in forbidden):
                continue
            out[str(key)] = _public_data(item)
        return out
    if isinstance(value, list):
        return [_public_data(item) for item in value[:40]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 500:
            return value[:500]
        return value
    return str(value)[:300]


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) > 280:
        text = text[:280]
    return text or type(exc).__name__


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "секрет" in text or "secret" in text or "не был выдан" in text:
        return "missing_secret"
    if "private key" in text or "bad_key" in text:
        return "bad_key"
    if "wrong_chain" in text or "chainid" in text:
        return "wrong_chain"
    if "мало eth" in text or "min_eth" in text or "insufficient funds" in text:
        return "low_gas"
    if "capsolver" in text:
        return "capsolver_error"
    if "cancelled" in text or "отмен" in text:
        return "cancelled"
    return "runtime_error"


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _as_bool(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
