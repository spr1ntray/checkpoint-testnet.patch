from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .accounts import AccountConfig
from .actions import WalletActionRunner
from .client import CheckpointClient
from .config import AppConfig
from .console import get_logger, setup_console_logger
from .logger import JsonlLogger
from .offer_pool import OfferPool
from .utils import redact_text, short_address, short_hash


def run_accounts(
    cfg: AppConfig,
    accounts: list[AccountConfig],
    *,
    mode: str,
    capsolver_api_key: str = "",
) -> int:
    setup_console_logger()
    system_log = get_logger("SYSTEM")
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    logger = JsonlLogger(Path("logs") / f"checkpoint_{run_id}.jsonl")
    offer_pool = OfferPool()

    wallets = list(accounts)
    if cfg.shuffle_wallets:
        random.shuffle(wallets)

    if cfg.siwe_enabled and cfg.siwe_captcha:
        if not capsolver_api_key:
            system_log.warning(
                "SIWE captcha ON, Capsolver key пустой — SIWE fail. "
                "Положи key в input/capsolver_api_key.txt и пересоздай базу."
            )
        else:
            try:
                from .capsolver import get_balance, normalize_api_key

                bal = get_balance(normalize_api_key(capsolver_api_key))
                system_log.info(f"Capsolver OK, balance={bal}")
            except Exception as exc:
                system_log.error(
                    f"Capsolver key невалиден: {exc}. "
                    "SIWE/hCaptcha не заработают, пока не поправишь key в базе."
                )

    system_log.info(
        f"Режим: {mode} | кошельков: {len(wallets)} | потоков: {cfg.max_workers} | log: {logger.path}"
    )
    if not wallets:
        system_log.warning("Нет кошельков")
        return 0

    max_workers = max(1, int(cfg.max_workers))
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _run_one, cfg, account, mode, logger, offer_pool, capsolver_api_key
            ): account
            for account in wallets
        }
        for future in as_completed(futures):
            account = futures[future]
            try:
                future.result()
            except Exception as exc:
                failed += 1
                error = redact_text(str(exc), [account.private_key, account.proxy, capsolver_api_key])
                logger.emit(
                    {
                        "label": account.label,
                        "address": None,
                        "action": "account_error",
                        "status": "failed",
                        "tx_hash": None,
                        "details": {"error": error},
                    }
                )
                get_logger(account.label).error(f"Ошибка аккаунта: {error}")
    return 1 if failed else 0


def _run_one(
    cfg: AppConfig,
    account: AccountConfig,
    mode: str,
    logger: JsonlLogger,
    offer_pool: OfferPool,
    capsolver_api_key: str,
) -> None:
    client = CheckpointClient(cfg, account)

    def emit(event: dict[str, Any]) -> None:
        logger.emit(event)
        _log_event(event)

    WalletActionRunner(
        cfg,
        client,
        emit,
        mode=mode,
        offer_pool=offer_pool,
        capsolver_api_key=capsolver_api_key,
    ).run()


def _log_event(event: dict[str, Any]) -> None:
    wallet = _event_wallet(event)
    level, message = _event_line(event)
    getattr(get_logger(wallet), level)(message)


def _event_wallet(event: dict[str, Any]) -> str:
    address = short_address(event["address"]) if event.get("address") else "-"
    return f"{event.get('label', '?')} {address}"


def _event_line(event: dict[str, Any]) -> tuple[str, str]:
    action = event.get("action", "?")
    status = event.get("status", "?")
    details = event.get("details") or {}
    tx = short_hash(event.get("tx_hash"))
    tx_s = "" if tx == "-" else f" | tx {tx}"

    if action == "start":
        return "info", f"Старт ({details.get('mode')})"
    if action == "siwe":
        return ("success" if status == "ok" else "warning"), f"SIWE: {status}{tx_s}" + (
            f" | {details.get('error')}" if status != "ok" and details.get("error") else ""
        )
    if action == "mint_usdc":
        return ("success" if status == "ok" else "error"), f"Mint USDC: {status}{tx_s}"
    if action == "approve_usdc":
        return "info", f"Approve USDC{tx_s}"
    if action == "fill":
        if status == "ok":
            return "success", (
                f"Fill #{details.get('offer_id')} {details.get('kind')} "
                f"{details.get('usdc')} USDC{tx_s}"
            )
        if status == "skipped":
            return "warning", f"Fill skip: {details.get('reason')}"
        return "error", f"Fill fail #{details.get('offer_id')}: {details.get('error')}"
    if action == "deposit":
        if status == "ok":
            return "success", f"Deposit pointsId={details.get('points_id')}{tx_s}"
        return "warning", f"Deposit skip: {details.get('error')}"
    if action == "create_offer":
        if status == "ok":
            return "success", f"Sell offer {details.get('points')} pts @ {details.get('price_usdc')} USDC{tx_s}"
        return "error", f"Sell fail: {details.get('error')}"
    if action == "xp_baseline":
        if status == "ok":
            return "info", f"XP baseline: {details.get('summary')}"
        return "warning", f"XP baseline fail: {details.get('error')}"
    if action == "xp_report":
        if status == "ok":
            return "success", (
                f"XP {details.get('before')} → {details.get('after')} "
                f"(Δ {details.get('delta')}) | {details.get('summary')}"
            )
        return "warning", f"XP report fail: {details.get('error')}"
    if action == "parse":
        return "info", (
            f"ETH={details.get('eth')} USDC={details.get('usdc')} | {details.get('summary') or details}"
        )
    if action == "gas_check":
        return "warning", f"Мало ETH: {details.get('eth')} (need {details.get('need')})"
    if status == "failed":
        return "error", f"{action}: {details.get('error', status)}"
    return "info", f"{action}: {status}{tx_s}"
