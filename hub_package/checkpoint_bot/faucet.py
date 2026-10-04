"""QuickNode Multi-Chain drip via AdsPower.

Live UI (HAR + index-C1gRuWTS.js):

- Native <select name="chain"> / <select name="network">, not comboboxes.
  Placeholder "Chain..." is a disabled <option> — Playwright get_by_text
  treats it as hidden, so we must NOT infer "already selected" from that.
- Wallet: <input name="wallet">. Read-only while Ads wallet is connected.
- Continue: submit _action=step-one, enabled only when chain + network +
  valid 0x address are set AND QuickNode mainnet-balance check passes.
- Amount: submit _action=step-two-skip ("Send to 0x…") on 0.05 ETH card.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from checkpoint_bot.client import CheckpointClient
from checkpoint_bot.mainnet import (
    activate_mainnet_history,
    mainnet_snapshot,
    needs_mainnet_history,
)
from checkpoint_bot.timing import sleep_jitter
from plugin.adspower import AdsPowerClient

FAUCET_URL = "https://faucet.quicknode.com/drip"
CHAIN_VALUE = "arbitrum"
NETWORK_VALUE = "sepolia"
GAS_NEED_WEI = 80_000_000_000_000  # 0.00008 ETH Arbitrum Sepolia
MAINNET_NEED_WEI = 10**15  # 0.001 ETH Ethereum — QuickNode drip gate
CONTINUE_WAIT_SEC = 45.0
SEND_WAIT_SEC = 90.0
TRANSFER_WAIT_SEC = 150.0
MAINNET_CHECK_SEC = 16.0
AFTER_SEND_SETTLE_SEC = 8.0
ONCHAIN_WAIT_SEC = 75.0


class FaucetEligibilityError(RuntimeError):
    """EOA cannot pass QuickNode's Ethereum mainnet balance / history gate."""


def needs_faucet(wei: int, *, need_wei: int = GAS_NEED_WEI) -> bool:
    return int(wei) < int(need_wei)


def needs_mainnet_for_faucet(wei: int, *, need_wei: int = MAINNET_NEED_WEI) -> bool:
    return int(wei) < int(need_wei)


def faucet_preflight(
    sepolia_wei: int,
    mainnet_wei: int | None,
    mainnet_nonce: int | None,
) -> str:
    """Who needs the faucet cycle. Healthy wallets return 'skip' immediately.

    skip                 — Sepolia gas already enough, Ads/swap never run
    ineligible           — L1 ETH < 0.001, swap would not help QuickNode
    activate_then_faucet — L1 ETH ok but nonce=0, need a tiny mainnet swap
    faucet               — L1 ETH + history ok (or L1 unread), open Ads drip
    """
    if not needs_faucet(sepolia_wei):
        return "skip"
    if mainnet_wei is not None and needs_mainnet_for_faucet(mainnet_wei):
        return "ineligible"
    if (
        mainnet_wei is not None
        and mainnet_nonce is not None
        and needs_mainnet_history(mainnet_nonce)
    ):
        return "activate_then_faucet"
    return "faucet"


def mainnet_eth_wei(
    address: str,
    *,
    http: Any | None = None,
    timeout: float = 12.0,
) -> int:
    """Read Ethereum mainnet ETH without opening QuickNode.

    Always a direct session: account proxies lie or timeout on L1 RPCs.
    (session.trust_env = False is in checkpoint_bot.mainnet)
    """
    del http
    return mainnet_snapshot(address, timeout=timeout).wei


def native_wei(client: CheckpointClient) -> int:
    return int(client.w3.eth.get_balance(client.address))


def claim_sepolia_eth(
    *,
    client: CheckpointClient,
    ads: AdsPowerClient,
    profile_id: str,
    log: Callable[[str], None],
    cancel: Callable[[], None],
    target_wei: int = GAS_NEED_WEI,
) -> int:
    start = native_wei(client)
    if not needs_faucet(start, need_wei=target_wei):
        return start

    session = ads.start_or_attach(profile_id)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        ads.stop_if_started(profile_id, session.started_by_us)
        raise RuntimeError("playwright не установлен в окружении софта") from exc

    try:
        with sync_playwright() as playwright:
            cancel()
            browser = playwright.chromium.connect_over_cdp(session.ws_url, timeout=30_000)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                try:
                    page.goto(FAUCET_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception:
                    page.goto(FAUCET_URL, wait_until="commit", timeout=45_000)
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                _drive_drip(
                    page,
                    client=client,
                    log=log,
                    cancel=cancel,
                    start=start,
                )
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    finally:
        ads.stop_if_started(profile_id, session.started_by_us)
    return native_wei(client)


def _drive_drip(
    page: Any,
    *,
    client: CheckpointClient,
    log: Callable[[str], None],
    cancel: Callable[[], None],
    start: int,
) -> int:
    page.wait_for_selector('select[name="chain"]', timeout=20_000)
    page.wait_for_selector('input[name="wallet"]', timeout=20_000)
    _dismiss_cookies(page, log=log)
    _close_wallet_popups(page)
    _disconnect_if_needed(page)

    if not _fill_wallet(page, client.address):
        raise RuntimeError("Не нашёл поле wallet на кране QuickNode")
    log("Адрес вставлен на faucet.quicknode.com/drip")
    sleep_jitter(0.8, 1.8, cancel_check=cancel)

    _select_chain_network(page, log=log, cancel=cancel)
    _dismiss_cookies(page, log=log, wait_sec=8.0)
    _wait_mainnet_check(page, cancel=cancel, log=log)
    if not _click_enabled_continue(page, cancel=cancel):
        _raise_if_faucet_gated(page)
        raise RuntimeError("Continue на кране так и не включился")
    log("Нажал Continue")

    if not _wait_bonus_or_error(page, cancel=cancel):
        _raise_if_rate_limited(page)
        _raise_if_faucet_gated(page)
        raise RuntimeError("После Continue не открылась страница 0.05 ETH")
    if "/transaction" not in _url(page):
        _wait_recaptcha_ready(page, cancel=cancel, log=log)
        log("Страница 0.05 ETH — жму Send to")
        if not _click_send_to(page):
            _raise_if_faucet_gated(page)
            raise RuntimeError("Не нашёл кнопку Send to (step-two-skip)")
        log("Нажал Send to. Если вылезла картинка reCAPTCHA — реши в окне Ads")
        sleep_jitter(AFTER_SEND_SETTLE_SEC * 0.6, AFTER_SEND_SETTLE_SEC, cancel_check=cancel)

    if not _wait_tx_or_error(page, cancel=cancel):
        _raise_if_rate_limited(page)
        _raise_if_faucet_gated(page)
        raise RuntimeError("QuickNode не открыл страницу транзакции после Send to")
    log("QuickNode принял drip — ждём Transfer Completed")

    completed = _wait_transfer_completed(page, cancel=cancel)
    last = native_wei(client)
    deadline = time.monotonic() + ONCHAIN_WAIT_SEC
    while last <= start and time.monotonic() < deadline:
        cancel()
        time.sleep(1.5)
        last = native_wei(client)
    if last > start:
        log(f"Кран капнул: {client.eth_balance()} ETH")
    elif completed:
        log("QuickNode показал Transaction Completed, ончейн ещё догоняет")
    else:
        log("Кран не подтвердил drip за отведённое время")
    return last


MAINNET_ERROR_MARKERS = (
    "invalid eth mainnet",
    "mainnet balance",
    "small mainnet balance",
    "low balance for this chain",
)

MAINNET_ERROR_MSG = (
    "QuickNode отказал: на адресе нет ETH в Ethereum mainnet "
    "(нужно ≥ 0.001 ETH в сети Ethereum, не Sepolia). Это антибот крана, не газ Checkpoint."
)

HISTORY_ERROR_MARKERS = (
    "established transaction history",
    "more established transaction",
    "does not currently meet this criteria",
)

HISTORY_ERROR_MSG = (
    "QuickNode отказал: на адресе нет истории транзакций в Ethereum. "
    "Нужен хотя бы один свап/tx в сети Ethereum, и после него ≥ 0.001 ETH."
)

RATE_LIMIT_MARKERS = (
    "please come back in 12 hours",
    "come back in 12 hour",
)

RATE_LIMIT_MSG = (
    "QuickNode: drip уже был за последние 12 часов (этот адрес или IP). "
    "Подожди окно и запусти снова."
)


def _dismiss_cookies(page: Any, *, log: Callable[[str], None], wait_sec: float = 0.8) -> None:
    """HubSpot banner is delayed — it often appears after chain/network, not on first paint."""
    deadline = time.monotonic() + max(0.2, wait_sec)
    while time.monotonic() < deadline:
        for name in ("Decline", "Reject", "Accept"):
            try:
                btn = page.get_by_role("button", name=name, exact=True)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=3_000)
                    log(f"Закрыл cookie-баннер ({name})")
                    time.sleep(0.3)
                    return
            except Exception:
                continue
        time.sleep(0.2)


def _close_wallet_popups(page: Any) -> None:
    """Connect Wallet / Rabby unlock must not steal the drip form."""
    _dismiss_modals(page)
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
        except Exception:
            break
        time.sleep(0.15)


def _disconnect_if_needed(page: Any) -> None:
    """Connected Ads wallet makes input[name=wallet] readOnly."""
    try:
        btn = page.get_by_role("button", name=re.compile(r"^Disconnect$", re.I))
        if btn.count() > 0 and btn.first.is_visible():
            btn.first.click(timeout=4_000)
            page.wait_for_selector('input[name="wallet"]:not([readonly])', timeout=8_000)
    except Exception:
        return


def _fill_wallet(page: Any, address: str) -> bool:
    loc = page.locator('input[name="wallet"]')
    try:
        loc.wait_for(state="visible", timeout=8_000)
        loc.click(timeout=3_000)
        loc.fill(address, timeout=8_000)
        try:
            loc.evaluate(
                """(el, value) => {
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                address,
            )
        except Exception:
            pass
        try:
            loc.press("Tab")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _select_chain_network(
    page: Any,
    *,
    log: Callable[[str], None],
    cancel: Callable[[], None],
) -> None:
    chain = page.locator('select[name="chain"]')
    network = page.locator('select[name="network"]')
    chain.wait_for(state="visible", timeout=8_000)
    _wait_option(page, 'select[name="chain"]', CHAIN_VALUE, timeout=15.0, cancel=cancel)
    cancel()
    _set_select(chain, value=CHAIN_VALUE, label="Arbitrum")
    log("Выбрал chain=arbitrum")
    _wait_select_enabled(page, 'select[name="network"]', timeout=15.0, cancel=cancel)
    _wait_option(page, 'select[name="network"]', NETWORK_VALUE, timeout=15.0, cancel=cancel)
    sleep_jitter(0.2, 0.5, cancel_check=cancel)
    cancel()
    _set_select(network, value=NETWORK_VALUE, label="Sepolia")
    log("Выбрал network=sepolia")
    _wait_url(page, "/arbitrum/sepolia", timeout=12.0, cancel=cancel)


def _set_select(locator: Any, *, value: str, label: str) -> None:
    try:
        locator.select_option(value=value, timeout=8_000)
    except Exception:
        locator.select_option(label=label, timeout=8_000)
    try:
        locator.dispatch_event("input")
        locator.dispatch_event("change")
    except Exception:
        pass


def _wait_option(
    page: Any,
    selector: str,
    value: str,
    *,
    timeout: float,
    cancel: Callable[[], None],
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cancel()
        try:
            raw = page.locator(selector).first.evaluate(
                "el => [...el.options].map(o => o.value).join(',')"
            )
            if value in str(raw).split(","):
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"В селекте {selector} нет option {value}")


def _wait_select_enabled(
    page: Any,
    selector: str,
    *,
    timeout: float,
    cancel: Callable[[], None],
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cancel()
        try:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_enabled():
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("Селект сети Sepolia так и не включился после Arbitrum")


def _page_body(page: Any) -> str:
    try:
        return (page.inner_text("body", timeout=2_000) or "").lower()
    except Exception:
        return ""


def _visible_has(page: Any, markers: tuple[str, ...]) -> bool:
    """True only if the error is actually on screen — Remix keeps copy in the DOM hidden."""
    for marker in markers:
        try:
            loc = page.get_by_text(re.compile(re.escape(marker), re.I))
            n = loc.count()
            for i in range(min(n, 8)):
                item = loc.nth(i)
                try:
                    if item.is_visible():
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _mainnet_gated(page: Any) -> bool:
    return _visible_has(page, MAINNET_ERROR_MARKERS)


def _history_gated(page: Any) -> bool:
    return _visible_has(page, HISTORY_ERROR_MARKERS)


def _raise_if_history_gated(page: Any) -> None:
    if _history_gated(page):
        raise FaucetEligibilityError(HISTORY_ERROR_MSG)


def _raise_if_mainnet_gated(page: Any) -> None:
    if _visible_has(page, MAINNET_ERROR_MARKERS):
        raise FaucetEligibilityError(MAINNET_ERROR_MSG)


def _raise_if_faucet_gated(page: Any) -> None:
    _raise_if_history_gated(page)
    _raise_if_mainnet_gated(page)


def _rate_limited(page: Any) -> bool:
    return _visible_has(page, RATE_LIMIT_MARKERS)


def _raise_if_rate_limited(page: Any) -> None:
    if _rate_limited(page):
        raise RuntimeError(RATE_LIMIT_MSG)


def _wait_recaptcha_ready(
    page: Any,
    *,
    cancel: Callable[[], None],
    log: Callable[[str], None],
) -> None:
    """Bonus page paints Send to before grecaptcha finishes. Clicking early looks like 12h."""
    deadline = time.monotonic() + 20.0
    seen = False
    while time.monotonic() < deadline:
        cancel()
        try:
            if page.locator('iframe[src*="recaptcha"]').count() > 0:
                seen = True
                break
        except Exception:
            pass
        time.sleep(0.4)
    if seen:
        log("reCAPTCHA подгрузилась — жду, пока кран додумает")
        sleep_jitter(3.0, 6.0, cancel_check=cancel)
    else:
        log("reCAPTCHA iframe не виден — всё равно даю крану паузу")
        sleep_jitter(6.0, 10.0, cancel_check=cancel)


def _wait_mainnet_check(
    page: Any,
    *,
    cancel: Callable[[], None],
    log: Callable[[str], None],
) -> None:
    """Merkle/mainnet check is slow — Continue looks enabled, then the red banner appears."""
    deadline = time.monotonic() + MAINNET_CHECK_SEC
    while time.monotonic() < deadline:
        cancel()
        _dismiss_cookies(page, log=log, wait_sec=0.2)
        if _history_gated(page):
            raise FaucetEligibilityError(HISTORY_ERROR_MSG)
        if _visible_has(page, MAINNET_ERROR_MARKERS):
            raise FaucetEligibilityError(MAINNET_ERROR_MSG)
        time.sleep(0.45)
    _raise_if_faucet_gated(page)


def _wait_bonus_or_error(page: Any, *, cancel: Callable[[], None]) -> bool:
    deadline = time.monotonic() + CONTINUE_WAIT_SEC
    while time.monotonic() < deadline:
        cancel()
        url = _url(page)
        if "/bonus" in url or "/transaction" in url:
            return True
        body = _page_body(page)
        if "choose your amount" in body or "send to" in body:
            return True
        if (
            _history_gated(page)
            or _visible_has(page, MAINNET_ERROR_MARKERS)
            or _visible_has(page, RATE_LIMIT_MARKERS)
        ):
            return False
        time.sleep(0.45)
    return "/bonus" in _url(page) or "/transaction" in _url(page)


def _wait_tx_or_error(page: Any, *, cancel: Callable[[], None]) -> bool:
    deadline = time.monotonic() + SEND_WAIT_SEC
    while time.monotonic() < deadline:
        cancel()
        if "/transaction" in _url(page):
            return True
        body = _page_body(page)
        if "transaction completed" in body or "transfer completed" in body:
            return True
        if (
            _visible_has(page, RATE_LIMIT_MARKERS)
            or _history_gated(page)
            or _visible_has(page, MAINNET_ERROR_MARKERS)
        ):
            return False
        time.sleep(0.5)
    return "/transaction" in _url(page)


def _click_enabled_continue(page: Any, *, cancel: Callable[[], None]) -> bool:
    btn = page.locator('button[name="_action"][value="step-one"]')
    deadline = time.monotonic() + CONTINUE_WAIT_SEC
    while time.monotonic() < deadline:
        cancel()
        try:
            if btn.count() > 0 and btn.first.is_enabled() and btn.first.is_visible():
                label = (btn.first.inner_text() or "").lower()
                if "loading" in label:
                    time.sleep(0.35)
                    continue
                btn.first.click(timeout=5_000)
                return True
        except Exception:
            pass
        time.sleep(0.35)
    return False


def _click_send_to(page: Any) -> bool:
    loc = page.locator('button[name="_action"][value="step-two-skip"]')
    try:
        loc.first.wait_for(state="visible", timeout=25_000)
        for i in range(loc.count()):
            item = loc.nth(i)
            try:
                if item.is_enabled() and item.is_visible():
                    item.click(timeout=5_000)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    fallback = page.get_by_role("button", name=re.compile(r"Send to", re.I))
    try:
        if fallback.count() > 0 and fallback.first.is_enabled():
            fallback.first.click(timeout=5_000)
            return True
    except Exception:
        return False
    return False


def _wait_transfer_completed(page: Any, *, cancel: Callable[[], None]) -> bool:
    deadline = time.monotonic() + TRANSFER_WAIT_SEC
    while time.monotonic() < deadline:
        cancel()
        try:
            body = (page.inner_text("body", timeout=1_500) or "").lower()
        except Exception:
            body = ""
        if "transaction completed" in body or "transfer completed" in body:
            return True
        if "already received" in body or "already requested" in body:
            return False
        time.sleep(0.6)
    return False


def _wait_url(page: Any, token: str, *, timeout: float, cancel: Callable[[], None]) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cancel()
        if token in _url(page):
            return True
        time.sleep(0.25)
    return token in _url(page)


def _url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


def _dismiss_modals(page: Any) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        return
