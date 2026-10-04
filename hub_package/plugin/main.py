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
from checkpoint_bot.faucet import (
    FaucetEligibilityError,
    MAINNET_NEED_WEI,
    claim_sepolia_eth,
    faucet_preflight,
    native_wei,
    needs_faucet,
)
from checkpoint_bot.mainnet import activate_mainnet_history, mainnet_snapshot
from checkpoint_bot.identity import BrowserIdentity, resolve_identity
from checkpoint_bot.offer_pool import OfferPool
from checkpoint_bot.referral import (
    address_from_account,
    fetch_referral_status,
    make_session,
    register_portfolio_address,
    submit_referral,
)
from checkpoint_bot.timing import (
    account_start_delay,
    parse_account_gap,
    pre_http_delay,
    roll_session,
)
from checkpoint_bot.utils import scrub_secrets
from plugin.adspower import (
    AdsPowerClient,
    AdsPowerError,
    find_duplicate_profile_accounts,
    normalize_api_key,
    normalize_profile_id,
)
from soft_hub.sdk import CancelledError, HubAccount, HubContext


CHAIN_ID = 421614
# Soft floor for Arbitrum Sepolia — L2 fills are cheap; old 0.0002 blocked funded-but-dust wallets.
# No artificial gas floor. L2 fill is cheap; skip only if wallet has literally 0 ETH.
MIN_ETH = Decimal("0")
ACTION_MODES = {
    "inspect": "parse",
    "farm": "daily",
}
RPC_PRIMARY = "https://sepolia-rollup.arbitrum.io/rpc"
RPC_FALLBACKS = [
    "https://arbitrum-sepolia.drpc.org",
    "https://arbitrum-sepolia.gateway.tenderly.co",
    "https://arbitrum-sepolia.public.blastapi.io",
    "https://arbitrum-sepolia-rpc.publicnode.com",
]
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_TERMINAL_ACCOUNT = frozenset(
    {"succeeded", "partial", "failed", "skipped", "blocked", "cancelled"}
)
_PROGRESS_LOCK = threading.Lock()
_LAST_PROGRESS: dict[str, float] = {}
_TERMINAL_SENT: set[str] = set()


def _reset_account_lifecycle() -> None:
    with _PROGRESS_LOCK:
        _LAST_PROGRESS.clear()
        _TERMINAL_SENT.clear()


def _account_state(
    context: HubContext,
    account_id: str,
    *,
    status: str,
    stage: str,
    progress: float | None = None,
    message: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    """Hub kills the run after 3 invalid frames (progress back / already terminal)."""
    if status == "needs_attention":
        status = "failed"
        if not _STAGE_RE.fullmatch(stage):
            stage = "external_outcome_unknown"
    with _PROGRESS_LOCK:
        if account_id in _TERMINAL_SENT:
            return
        if progress is not None:
            try:
                value = round(float(progress), 3)
            except (TypeError, ValueError):
                value = _LAST_PROGRESS.get(account_id, 0.0)
            value = min(1.0, max(0.0, value))
            last = _LAST_PROGRESS.get(account_id, 0.0)
            if value < last:
                value = last
            _LAST_PROGRESS[account_id] = value
            progress = value
        if status in _TERMINAL_ACCOUNT:
            _TERMINAL_SENT.add(account_id)
    try:
        context.account_state(
            account_id,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            data=data,
        )
    except Exception:
        with _PROGRESS_LOCK:
            _TERMINAL_SENT.discard(account_id)


def run(context: HubContext) -> dict[str, Any]:
    mode = ACTION_MODES.get(context.action_id)
    if mode is None:
        raise ValueError(f"unsupported_action:{context.action_id}")

    _reset_account_lifecycle()
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

    blocked_pre: set[str] = set()
    identities: dict[str, BrowserIdentity] = {}
    if mode == "daily":
        blocked_pre, identities = _preflight_farm(context)
        if blocked_pre:
            with lock:
                counters["blocked"] = counters.get("blocked", 0) + len(blocked_pre)

    context.log(
        "Старт Checkpoint",
        data={
            "mode": mode,
            "accounts": len(context.accounts),
            "levels": len(levels),
            "account_concurrency": workers,
            "farm_parallel": "all_after_register" if mode == "daily" else "flat",
            "auto_register": mode == "daily",
            "adspower": mode == "daily",
            "http_farm": mode == "daily",
            "faucet": "quicknode" if mode == "daily" else False,
            "siwe": False,
        },
    )
    if mode == "daily":
        context.log(
            "SIWE пропускаем — Capsolver не решает hCaptcha. Kernel fills без логина",
            data={"siwe": False},
        )
        context.log(
            "Ads Chrome открывается только для крана QuickNode, если Sepolia ETH = 0. "
            "Если газ уже есть — фарм идёт по HTTP без окна профиля.",
            data={"ads_chrome": "faucet_only"},
        )

    def _map(fn, accounts) -> None:
        if hasattr(context, "map_accounts"):
            context.map_accounts(fn, accounts=accounts)
        else:
            for account in accounts:
                context.check_cancelled()
                fn(account)

    def _finish_worker(status: str, meta: dict[str, bool]) -> str:
        with lock:
            counters[status] = counters.get(status, 0) + 1
            if meta.get("registered"):
                counters["registered"] = counters.get("registered", 0) + 1
            if meta.get("referral_linked"):
                counters["referral_linked"] = counters.get("referral_linked", 0) + 1
            if meta.get("referral_mismatch"):
                counters["referral_mismatch"] = counters.get("referral_mismatch", 0) + 1
        return status

    def _crash_worker() -> str:
        with lock:
            counters["failed"] = counters.get("failed", 0) + 1
        return "failed"

    register_failed: set[str] = set()

    def register_worker(hub_account: HubAccount) -> str:
        if hub_account.id in blocked_pre:
            return "blocked"
        try:
            status, meta = _run_account(
                context,
                hub_account,
                cfg=cfg,
                mode=mode,
                offer_pool=offer_pool,
                counters=counters,
                counters_lock=lock,
                session=None,
                identity=identities.get(hub_account.id),
                phase="register",
            )
        except CancelledError:
            with lock:
                counters["cancelled"] = counters.get("cancelled", 0) + 1
            raise
        except Exception:
            _account_state(
                context,
                hub_account.id,
                status="failed",
                stage="automation_failed",
                message="Ошибка регистрации аккаунта",
            )
            register_failed.add(hub_account.id)
            return _crash_worker()
        if status == "failed":
            register_failed.add(hub_account.id)
            return _finish_worker(status, meta)
        with lock:
            if meta.get("registered"):
                counters["registered"] = counters.get("registered", 0) + 1
            if meta.get("referral_linked"):
                counters["referral_linked"] = counters.get("referral_linked", 0) + 1
            if meta.get("referral_mismatch"):
                counters["referral_mismatch"] = counters.get("referral_mismatch", 0) + 1
        return status

    def farm_worker(hub_account: HubAccount) -> str:
        if hub_account.id in blocked_pre:
            return "blocked"
        if hub_account.id in register_failed:
            return "failed"
        finished = False
        try:
            session = roll_session()
            if mode == "daily":
                account_start_delay(cancel_check=context.check_cancelled, style=session)
            else:
                parse_account_gap(cancel_check=context.check_cancelled)
            status, meta = _run_account(
                context,
                hub_account,
                cfg=cfg,
                mode=mode,
                offer_pool=offer_pool,
                counters=counters,
                counters_lock=lock,
                session=session,
                identity=identities.get(hub_account.id),
                phase="farm" if mode == "daily" else "all",
            )
            finished = True
            return _finish_worker(status, meta)
        except CancelledError:
            _account_state(
                context,
                hub_account.id,
                status="cancelled",
                stage="cancelled",
                message="Остановлено",
            )
            with lock:
                counters["cancelled"] = counters.get("cancelled", 0) + 1
            finished = True
            raise
        except Exception:
            _account_state(
                context,
                hub_account.id,
                status="failed",
                stage="automation_failed",
                message="Ошибка обработки аккаунта",
            )
            return _crash_worker()
        finally:
            if not finished:
                _account_state(
                    context,
                    hub_account.id,
                    status="failed",
                    stage="automation_failed",
                    message="Ран оборвался без итогового статуса",
                )

    if mode == "daily":
        for level_index, level in enumerate(levels):
            context.check_cancelled()
            context.log(
                f"Регистрация, уровень {level_index}",
                data={"accounts": len(level), "level": level_index, "phase": "register"},
            )
            _map(register_worker, level)
        context.log(
            "Фарм всех выбранных аккаунтов параллельно",
            data={
                "accounts": len(context.accounts),
                "account_concurrency": workers,
                "phase": "farm",
            },
        )
        _map(farm_worker, context.accounts)
    else:
        _map(farm_worker, context.accounts)

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


def _block_account(context: HubContext, account: HubAccount, message: str) -> None:
    _account_state(context,
        account.id,
        status="blocked",
        stage="preflight_blocked",
        message=message,
    )
    context.log(
        message,
        level="warning",
        account_id=account.id,
        data={"service": "adspower"},
    )


def _identity_seed(account: HubAccount) -> str:
    addr = (getattr(account, "evm_address", None) or "").strip()
    return addr.lower() if addr else str(account.id)


def _preflight_farm(context: HubContext) -> tuple[set[str], dict[str, BrowserIdentity]]:
    """Farm needs a unique Ads profile: QuickNode drip runs in that Chrome window."""
    blocked: set[str] = set()
    identities: dict[str, BrowserIdentity] = {}
    pairs: list[tuple[str, str]] = []
    for account in context.accounts:
        context.check_cancelled()
        try:
            profile_id = normalize_profile_id(account.secret("adspower_profile"))
        except KeyError:
            profile_id = ""
        if hasattr(context, "protect_secret") and len(profile_id) >= 4:
            try:
                context.protect_secret(profile_id)
            except Exception:
                pass
        if len(profile_id) < 4:
            _block_account(
                context,
                account,
                "Нет AdsPower профиля — кран QuickNode открывается в окне Ads",
            )
            blocked.add(account.id)
            continue
        pairs.append((account.id, profile_id))

    for group in find_duplicate_profile_accounts(pairs):
        for account in context.accounts:
            if account.id in group:
                _block_account(
                    context,
                    account,
                    "Один AdsPower-профиль нельзя назначать двум аккаунтам",
                )
                blocked.add(account.id)

    try:
        api_key = normalize_api_key(context.settings.secret("adspower_api"))
    except KeyError:
        api_key = ""
    api_key = normalize_api_key(api_key)
    if hasattr(context, "protect_secret") and len(api_key or "") >= 4:
        try:
            context.protect_secret(api_key)
        except Exception:
            pass

    if len(api_key) < 4:
        for account in context.accounts:
            if account.id not in blocked:
                _block_account(
                    context,
                    account,
                    "Нет AdsPower API key в настройках Hub",
                )
                blocked.add(account.id)
        return blocked, identities

    ads: AdsPowerClient | None = None
    by_id = dict(pairs)
    try:
        ads = AdsPowerClient(api_key)
        ads.health()
        context.log("AdsPower Local API доступен", data={"service": "adspower"})
    except AdsPowerError:
        for account in context.accounts:
            if account.id not in blocked:
                _block_account(
                    context,
                    account,
                    "AdsPower Local API недоступен — запусти AdsPower (кран идёт через профиль)",
                )
                blocked.add(account.id)
        return blocked, identities

    try:
        for account in context.accounts:
            context.check_cancelled()
            if account.id in blocked:
                continue
            profile_id = by_id.get(account.id, "")
            row = None
            try:
                row = ads.get_profile(profile_id)
            except AdsPowerError as exc:
                _block_account(context, account, str(exc) or "AdsPower не нашёл профиль")
                blocked.add(account.id)
                continue
            identity = resolve_identity(_identity_seed(account), row)
            identities[account.id] = identity
            if identity.source == "ads":
                context.log(
                    "Ads-профиль прочитан",
                    account_id=account.id,
                    data={"service": "adspower", **identity.summary()},
                )
            else:
                context.log(
                    "Профиль Ads есть, UA из локальной базы",
                    account_id=account.id,
                    data=identity.summary(),
                )
    finally:
        if ads is not None:
            ads.close()
        api_key = ""
    return blocked, identities


def _claim_gas_if_needed(
    context: HubContext,
    hub_account: HubAccount,
    client: CheckpointClient,
) -> bool:
    """Open QuickNode drip in Ads only when the EOA cannot cover Kernel gas."""
    start = native_wei(client)
    if not needs_faucet(start):
        context.log(
            "ETH на газе хватает — кран и свап пропускаем",
            account_id=hub_account.id,
            data={"eth_sepolia": str(client.eth_balance())},
        )
        return False

    snap_wei: int | None = None
    snap_nonce: int | None = None
    try:
        snap = mainnet_snapshot(client.address)
        snap_wei = snap.wei
        snap_nonce = snap.nonce
    except Exception as exc:
        context.log(
            f"Ethereum mainnet не проверили ({_safe_error(exc)}) — кран всё равно попробуем",
            level="warning",
            account_id=hub_account.id,
        )

    decision = faucet_preflight(start, snap_wei, snap_nonce)
    if decision == "skip":
        return False
    if decision == "ineligible":
        from web3 import Web3

        have = str(Web3.from_wei(int(snap_wei or 0), "ether"))
        need = str(Web3.from_wei(MAINNET_NEED_WEI, "ether"))
        msg = (
            f"На Ethereum mainnet недостаточно ETH для крана QuickNode: {have} "
            f"(нужно ≥ {need}). Кран не открываем."
        )
        context.log(
            msg,
            level="error",
            account_id=hub_account.id,
            data={"eth_mainnet": have, "need": need, "network": "ethereum", "nonce": snap_nonce},
        )
        raise FaucetEligibilityError(msg)
    if decision == "activate_then_faucet":
        _account_state(
            context,
            hub_account.id,
            status="running",
            stage="mainnet",
            progress=0.16,
            message="Нет истории txs на Ethereum — делаю маленький свап для крана",
        )
        context.log(
            "QuickNode не пускает кошельки без транзакций в Ethereum. "
            "Делаю свап на пару долларов, оставляю ≥ 0.001 ETH.",
            account_id=hub_account.id,
            data={"eth_mainnet": str(snap_wei), "nonce": snap_nonce},
        )
        try:
            after = activate_mainnet_history(
                client.account,
                log=lambda msg: context.log(msg, account_id=hub_account.id),
                cancel=context.check_cancelled,
            )
            snap_wei, snap_nonce = after.wei, after.nonce
            if after.wei is not None and after.wei < MAINNET_NEED_WEI:
                from web3 import Web3

                have = str(Web3.from_wei(after.wei, "ether"))
                msg = (
                    f"После свапа на Ethereum осталось {have} ETH "
                    f"(нужно ≥ 0.001). Кран не откроется."
                )
                context.log(msg, level="error", account_id=hub_account.id)
                raise FaucetEligibilityError(msg)
        except FaucetEligibilityError:
            raise
        except Exception as exc:
            context.log(
                f"Свап на Ethereum не удался ({_safe_error(exc)}) — кран всё равно попробуем",
                level="warning",
                account_id=hub_account.id,
            )

    _account_state(context,
        hub_account.id,
        status="running",
        stage="faucet",
        progress=0.18,
        message="Мало Sepolia ETH — открываю QuickNode drip в Ads",
    )
    try:
        profile_id = normalize_profile_id(hub_account.secret("adspower_profile"))
        api_key = normalize_api_key(context.settings.secret("adspower_api"))
    except KeyError:
        context.log(
            "Нет AdsPower секрета — кран пропущен",
            level="warning",
            account_id=hub_account.id,
        )
        return False
    if hasattr(context, "protect_secret"):
        try:
            if len(profile_id) >= 4:
                context.protect_secret(profile_id)
            if len(api_key) >= 4:
                context.protect_secret(api_key)
        except Exception:
            pass

    ads: AdsPowerClient | None = None
    after = start
    try:
        ads = AdsPowerClient(api_key)
        after = claim_sepolia_eth(
            client=client,
            ads=ads,
            profile_id=profile_id,
            log=lambda msg: context.log(msg, account_id=hub_account.id),
            cancel=context.check_cancelled,
        )
    except CancelledError:
        raise
    except FaucetEligibilityError:
        raise
    except AdsPowerError as exc:
        context.log(str(exc), level="warning", account_id=hub_account.id)
    except Exception as exc:
        context.log(
            f"Кран не удался: {_safe_error(exc)}",
            level="warning",
            account_id=hub_account.id,
        )
    finally:
        if ads is not None:
            ads.close()
        api_key = ""

    _account_state(context,
        hub_account.id,
        status="running",
        stage="funded",
        progress=0.24,
        message="Кран отработал",
    )
    claimed = after > start
    context.log(
        f"После крана: {client.eth_balance()} ETH",
        account_id=hub_account.id,
        data={"claimed": claimed, "eth": str(client.eth_balance())},
    )
    return claimed


def _ensure_registered(
    context: HubContext,
    hub_account: HubAccount,
    *,
    cfg: AppConfig,
    account: AccountConfig,
    child_address: str,
    identity: BrowserIdentity | None = None,
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
    session = make_session(account.proxy, identity.headers() if identity else None)

    _account_state(context,
        hub_account.id,
        status="running",
        stage="register",
        progress=0.08,
        message="Проверяем / регистрируем в Checkpoint",
    )
    context.check_cancelled()
    pre_http_delay(cancel_check=context.check_cancelled)

    try:
        register_portfolio_address(session, cfg, child_address)
        meta["registered"] = True
    except Exception as exc:
        # Portfolio re-index is soft; farm can continue even if it flakes.
        context.log(
            "Portfolio register soft-fail — продолжаем",
            level="warning",
            account_id=hub_account.id,
            data={"error": _safe_error(exc)[:160]},  # scrubbed
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

    _account_state(context,
        hub_account.id,
        status="running",
        stage="referral",
        progress=0.12,
        message="Сажаем на рефералку parent (если нужно)",
    )
    context.check_cancelled()
    pre_http_delay(cancel_check=context.check_cancelled)

    try:
        existing = fetch_referral_status(session, cfg, child_address)
    except Exception as exc:
        context.log(
            "Не удалось прочитать referral status — пробуем создать",
            level="warning",
            account_id=hub_account.id,
            data={"error": _safe_error(exc)[:160]},  # scrubbed
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
        pre_http_delay(cancel_check=context.check_cancelled)
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
            data={"error": _safe_error(exc)[:160]},  # scrubbed
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
    session=None,
    identity: BrowserIdentity | None = None,
    phase: str = "all",
) -> tuple[str, dict[str, bool]]:
    context.check_cancelled()
    if phase == "farm":
        _account_state(context,
            hub_account.id,
            status="running",
            stage="automation",
            progress=0.15,
            message="Запускаем фарм",
        )
    else:
        _account_state(context,
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
        "faucet_claimed": False,
    }
    try:
        account = _account_config(hub_account, context)
        child_address = address_from_account(account)
        if hub_account.evm_address and child_address.lower() != hub_account.evm_address.lower():
            raise RuntimeError("bad_key: private key ≠ Hub address")

        # Work mode: auto-register new wallets along Hub referral topology first.
        if mode == "daily" and phase in {"all", "register"}:
            reg_meta = _ensure_registered(
                context,
                hub_account,
                cfg=cfg,
                account=account,
                child_address=child_address,
                identity=identity,
            )
            meta.update(reg_meta)
            if phase == "register":
                return "ok", meta

        if identity is None:
            identity = resolve_identity(child_address)
        client = CheckpointClient(cfg, account, identity=identity)
        _preflight(client, hub_account)

        capsolver_key = ""
        if mode == "daily" and identity is not None:
            context.log(
                "HTTP-сессия с отпечатком",
                account_id=hub_account.id,
                data=identity.summary(),
            )

        faucet_tried = False

        def fund_gas() -> bool:
            nonlocal faucet_tried
            if faucet_tried:
                return False
            faucet_tried = True
            claimed = _claim_gas_if_needed(context, hub_account, client)
            meta["faucet_claimed"] = bool(claimed)
            return bool(claimed)

        _account_state(context,
            hub_account.id,
            status="running",
            stage="automation",
            progress=0.28 if mode == "daily" else 0.15,
            message="Собираем данные" if mode == "parse" else "Запускаем фарм",
        )

        event_stats = {
            "failed": 0,
            "skipped": 0,
            "transactions": 0,
            "fills_ok": 0,
            "fills_target": 0,
            "gas_blocked": False,
            "no_offers": False,
        }
        max_progress = 0.28 if mode == "daily" else 0.15

        def emit(event: dict[str, Any]) -> None:
            nonlocal write_may_have_happened, last_skip_reason, parse_row, max_progress
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
                    last_skip_reason = f"Мало ETH: {details.get('eth')} (нужно {details.get('need') or '> 0'})"
                elif reason in {"no_offers", "no suitable free offers"}:
                    event_stats["no_offers"] = True
                    last_skip_reason = "Нет подходящих offers в заданном диапазоне USDC"
                elif details.get("reason"):
                    last_skip_reason = str(details.get("reason"))[:160]
            if action in {"mint_usdc", "approve_usdc", "fill"} and status == "ok":
                write_may_have_happened = True
            if action == "session_plan" and status == "ok":
                event_stats["fills_target"] = int(details.get("trades") or 0)
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
                "session_plan": 0.36,
                "mint_usdc": 0.40,
                "approve_usdc": 0.45,
                "fill": round(min(0.50 + 0.08 * max(event_stats["fills_ok"], 1), 0.90), 3),
                "xp_report": 0.94,
                "gas_check": 0.33,
            }
            if action in progress_map and status in {"ok", "skipped", "failed"}:
                stage = action if _STAGE_RE.fullmatch(action) else "automation"
                prog = float(progress_map[action])
                if prog < max_progress:
                    prog = max_progress
                max_progress = prog
                _account_state(context,
                    hub_account.id,
                    status="running",
                    stage=stage,
                    progress=prog,
                    message=f"{action}: {status}",
                )
            elif action in {"offer_wait", "offer_switch"} and status == "ok":
                pid = details.get("points_id") or details.get("from_points_id")
                if action == "offer_wait":
                    msg = f"Ждём ордера на рынке {pid} до {details.get('wait_s')}с"
                else:
                    msg = (
                        f"Рынок {details.get('from_points_id')} пустой — "
                        f"идём на {details.get('to_points_id')}"
                    )
                _account_state(context,
                    hub_account.id,
                    status="running",
                    stage=action,
                    progress=max_progress,
                    message=msg,
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
            session=session,
            fund_gas=fund_gas if mode == "daily" else None,
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
            elif event_stats["fills_ok"] >= max(
                int(event_stats.get("fills_target") or 0),
                int(cfg.trades_min),
            ):
                # Planned fills done. Mid-loop retries must not mark the day partial.
                terminal_status = "succeeded"
                terminal_stage = "completed"
                terminal_message = "Работа завершена"
                write_may_have_happened = False
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
                    "faucet_claimed": meta.get("faucet_claimed", False),
                    "mode": mode,
                },
            )

    except CancelledError as error:
        cancelled = error
        if write_may_have_happened:
            terminal_status = "failed"
            terminal_stage = "external_outcome_unknown"
            terminal_message = "Остановка после возможного write — сверьте chain"
        else:
            terminal_status = "cancelled"
            terminal_stage = "cancelled"
            terminal_message = "Остановлено до внешнего write"
    except Exception as exc:
        code = _classify_error(exc)
        if write_may_have_happened:
            terminal_status = "failed"
            terminal_stage = "external_outcome_unknown"
            terminal_message = "Возможный write без подтверждения — сверьте chain"
        elif code in {"missing_secret", "bad_key", "wrong_chain", "low_gas", "blocked"}:
            terminal_status = "blocked"
            terminal_stage = "preflight_blocked"
            terminal_message = _safe_error(exc)
        elif code == "faucet_ineligible":
            terminal_status = "failed"
            terminal_stage = "faucet_blocked"
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
        else:
            context.result(
                f"{hub_account.label}: Checkpoint {terminal_status}",
                kind="account_summary",
                status=terminal_status,
                account_id=hub_account.id,
                data={
                    "error": _safe_error(exc),
                    "error_code": code,
                    "mode": mode,
                    "faucet_claimed": False,
                },
            )
        context.log(
            "Аккаунт завершился ошибкой",
            level="error",
            account_id=hub_account.id,
            data={"error_code": code},
        )
    finally:
        capsolver_key = ""

    _account_state(context,
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
    # Action pacing is core-owned (anti-sybil). Not a user-facing option.
    # timing.action_delay spreads this range further with jitter / long pauses.
    delay_min = 6.0
    delay_max = 16.0
    tmin = int(_clamp_float(options.get("trades_min", 5), 5, 10, 5))
    tmax = int(_clamp_float(options.get("trades_max", 7), 5, 10, 7))
    if tmax < tmin:
        tmin, tmax = tmax, tmin
    # Software owns size: ~$420–500/session split across the rolled fill count.
    max_usdc = Decimal("500")
    mint_amount = Decimal("500")

    return AppConfig(
        max_workers=1,
        shuffle_wallets=False,
        delay_min=delay_min,
        delay_max=delay_max,
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
        trades_min=tmin,
        trades_max=tmax,
        trades_per_day=tmax,
        trade_usdc_min=Decimal("0.01"),
        trade_usdc_max=max_usdc,
        mint_usdc_if_below=Decimal("50"),
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


def _account_config(account: HubAccount, context: HubContext | None = None) -> AccountConfig:
    private_key = normalize_private_key(account.secret("evm_private_key"))
    try:
        proxy = normalize_proxy(account.secret("proxy"))
    except KeyError:
        proxy = None
    # Defense-in-depth: register Vault secrets with host Redactor so any accidental
    # echo (exception text, proxy URL variants) is scrubbed from events/logs.
    if context is not None and hasattr(context, "protect_secret"):
        try:
            if private_key and 4 <= len(private_key) <= 4096:
                context.protect_secret(private_key)
            if private_key.startswith("0x") and 4 <= len(private_key[2:]) <= 4096:
                context.protect_secret(private_key[2:])
            if proxy and 4 <= len(proxy) <= 4096:
                context.protect_secret(proxy)
            if proxy:
                bare = proxy.replace("http://", "").replace("https://", "")
                if bare != proxy and 4 <= len(bare) <= 4096:
                    context.protect_secret(bare)
        except Exception:
            pass
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
        "password", "proxy", "token", "api_key", "apikey", "cookie", "set-cookie",
        "private_key", "referrer_code", "referral_code",
    )
    if isinstance(value, dict):
        return {
            str(k): _public_data(v)
            for k, v in value.items()
            if not any(word in str(k).lower() for word in forbidden)
        }
    if isinstance(value, list):
        return [_public_data(item) for item in value[:40]]
    if isinstance(value, str):
        clean = scrub_secrets(value)
        return clean[:500] if len(clean) > 500 else clean
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub_secrets(str(value)[:300])


def _safe_error(exc: Exception) -> str:
    text = scrub_secrets(str(exc).replace("\n", " ").strip())
    return (text[:280] if len(text) > 280 else text) or type(exc).__name__


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, FaucetEligibilityError):
        return "faucet_ineligible"
    if isinstance(exc, AdsPowerError):
        if exc.code in {
            "missing_secret",
            "profile_missing",
            "profile_ambiguous",
            "unsafe_endpoint",
            "adspower_unavailable",
        }:
            return "blocked"
        return "runtime_error"
    text = str(exc).lower()
    if "secret" in text or "секрет" in text:
        return "missing_secret"
    if "private key" in text or "bad_key" in text:
        return "bad_key"
    if "wrong_chain" in text or "chainid" in text:
        return "wrong_chain"
    if "мало eth" in text or "low_gas" in text or "insufficient funds" in text:
        return "low_gas"
    if "adspower" in text or "profile_missing" in text or "capsolver" in text:
        return "blocked"
    if "cancelled" in text or "отмен" in text:
        return "cancelled"
    return "runtime_error"


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))
