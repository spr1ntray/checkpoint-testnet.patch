"""Checkpoint UI session inside an AdsPower profile.

HAR (2026-08-09): Dynamic connect via browser extension (Rabby), SIWE on the
EOA, then ZeroDev Kernel UserOps. Buy XP is indexed on the EOA only after
Kernel fills — not after raw EOA fillOffer.

This module:
  1. Attaches to AdsPower CDP
  2. Uses Rabby/MetaMask if the profile already has it and the account matches
  3. Otherwise injects a Rabby-like EIP-1193 signed by the Hub private key
  4. Opens checkpoint.exchange (with ?ref=) and clicks Connect Wallet
  5. Auto-confirms extension popups
  6. Leaves the page open so Kernel UserOps run with a live UI session
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount

from .kernel_aa import KernelAccount
from .utils import scrub_secrets

SITE = "https://checkpoint.exchange"
CHAIN_ID = 421614
CHAIN_HEX = hex(CHAIN_ID)

DISMISS_SELECTORS = (
    'button:has-text("Trade Points")',
    'button:has-text("Trade points")',
    'button:has-text("Got it")',
    'button[aria-label="Close"]',
    '[aria-label="Close"]',
)
CONNECT_SELECTORS = (
    'button:has-text("Connect Wallet")',
    'button:has-text("Connect wallet")',
    '[role="button"]:has-text("Connect Wallet")',
)
WALLET_SELECTORS = (
    'button:has-text("Rabby")',
    'div:has-text("Rabby")',
    'button:has-text("MetaMask")',
    'button:has-text("Browser")',
    'button:has-text("Injected")',
    '[data-testid*="rabby" i]',
    '[data-testid*="metamask" i]',
)
CONFIRM_BUTTONS = (
    'button:has-text("Connect")',
    'button:has-text("Sign")',
    'button:has-text("Confirm")',
    'button:has-text("Approve")',
    'button:has-text("Continue")',
    'button:has-text("Get Started")',
    'button:has-text("OK")',
)

PROVIDER_JS = r"""
(() => {
  if (window.__hubEthInstalled) return;
  window.__hubEthInstalled = true;
  const chainId = %CHAIN%;
  const address = %ADDRESS%;
  const call = async (payload) => {
    const raw = await window.__hubEthRequest(JSON.stringify(payload));
    if (typeof raw !== "string") return raw;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.__error) {
      const err = new Error(parsed.__error);
      err.code = parsed.__code || 4001;
      throw err;
    }
    return parsed.__result;
  };
  const provider = {
    isRabby: true,
    isMetaMask: false,
    isConnected: () => true,
    chainId,
    networkVersion: String(parseInt(chainId, 16)),
    selectedAddress: address,
    request: (args) => {
      const method = args && args.method ? args.method : args;
      const params = args && args.params ? args.params : [];
      return call({ method, params });
    },
    sendAsync: (payload, cb) => {
      call({ method: payload.method, params: payload.params || [] })
        .then((result) => cb(null, { id: payload.id, jsonrpc: "2.0", result }))
        .catch((err) => cb(err));
    },
    send: (methodOrPayload, paramsOrCb) => {
      if (typeof methodOrPayload === "string") {
        return call({ method: methodOrPayload, params: paramsOrCb || [] });
      }
      return call({
        method: methodOrPayload.method,
        params: methodOrPayload.params || [],
      });
    },
    enable: () => call({ method: "eth_requestAccounts", params: [] }),
    on: () => provider,
    removeListener: () => provider,
    removeAllListeners: () => provider,
  };
  const wrap = {
    configurable: false,
    enumerable: true,
    get: () => provider,
    set: () => {},
  };
  try {
    Object.defineProperty(window, "ethereum", wrap);
  } catch (e) {
    window.ethereum = provider;
  }
})();
"""


CancelCheck = Callable[[], None] | None


class BrowserFarmError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _safe(exc: BaseException) -> str:
    return scrub_secrets(str(exc).replace("\n", " ").strip())[:220] or type(exc).__name__


class BrowserFarm:
    def __init__(
        self,
        *,
        ws_url: str,
        account: LocalAccount,
        kernel: KernelAccount | None,
        cancel_check: CancelCheck = None,
        page_timeout_ms: int = 25000,
    ) -> None:
        self.ws_url = ws_url
        self.account = account
        self.kernel = kernel
        self.cancel_check = cancel_check
        self.page_timeout_ms = page_timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._injected = False
        self.connected = False
        self.used_extension = False

    def _cancel(self) -> None:
        if self.cancel_check:
            self.cancel_check()

    def __enter__(self) -> "BrowserFarm":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserFarmError(
                "playwright_missing",
                "Playwright не установлен в runtime Hub",
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(
            self.ws_url, timeout=self.page_timeout_ms
        )
        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = self._browser.new_context()
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Detach CDP only. Never Browser.close() — that would kill a profile
        # the operator already had open. AdsPowerClient.stop_if_started owns stop.
        self._page = None
        self._context = None
        self._browser = None
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None

    def connect(self, *, referrer: str | None = None) -> dict[str, Any]:
        self._cancel()
        page = self._page
        if page is None:
            raise BrowserFarmError("adspower_unavailable", "Нет browser page")
        target = SITE + "/"
        if referrer and referrer.startswith("0x") and len(referrer) == 42:
            target = f"{SITE}/?ref={referrer}"
        page.goto(target, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        page.bring_to_front()
        self._sleep(0.8, 1.6)
        self._dismiss_welcome()

        extension = self._detect_extension()
        if extension:
            self.used_extension = True
        else:
            self._install_injected_provider()
            page.reload(wait_until="domcontentloaded", timeout=self.page_timeout_ms)
            self._injected = True
            self._sleep(0.4, 1.0)
            self._dismiss_welcome()

        already = self._looks_connected()
        if not already:
            self._confirm_popups_once()
            self._click_connect()
            self._confirm_popups_once()
            self._sleep(0.8, 1.6)
            # Dynamic still asks which wallet even if Rabby is injected.
            self._click_wallet_choice()
            self._confirm_popups_once()
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                self._cancel()
                self._dismiss_welcome()
                self._confirm_popups_once()
                if self._looks_connected():
                    already = True
                    break
                self._sleep(0.7, 1.2)
        self.connected = self._looks_connected() or already
        if self.connected:
            try:
                market = f"{SITE}/market?address=evm%3A{self.account.address.lower()}"
                page.goto(market, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
            except Exception:
                pass
        return {
            "connected": bool(self.connected),
            "injected": bool(self._injected),
            "extension": bool(self.used_extension),
        }

    def _install_injected_provider(self) -> None:
        context = self._context
        if context is None:
            raise BrowserFarmError("adspower_unavailable", "Нет browser context")

        def _handler(raw: str) -> str:
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {}
            try:
                result = self._eth_request(
                    str(payload.get("method") or ""),
                    payload.get("params") or [],
                )
                return json.dumps({"__result": result})
            except BrowserFarmError as exc:
                return json.dumps({"__error": str(exc), "__code": 4001})
            except Exception as exc:
                return json.dumps({"__error": _safe(exc), "__code": -32603})

        try:
            context.expose_function("__hubEthRequest", _handler)
        except Exception:
            # Already exposed on this context (reattach).
            pass
        address = json.dumps(self.account.address)
        script = PROVIDER_JS.replace("%CHAIN%", json.dumps(CHAIN_HEX)).replace(
            "%ADDRESS%", address
        )
        context.add_init_script(script)

    def _eth_request(self, method: str, params: Any) -> Any:
        params = params or []
        if not isinstance(params, list):
            params = [params]
        name = (method or "").strip()
        addr = self.account.address
        if name in {"eth_requestAccounts", "eth_accounts"}:
            return [addr]
        if name == "eth_chainId":
            return CHAIN_HEX
        if name == "net_version":
            return str(CHAIN_ID)
        if name in {"wallet_switchEthereumChain", "wallet_addEthereumChain"}:
            return None
        if name == "wallet_requestPermissions":
            return [{"parentCapability": "eth_accounts"}]
        if name == "wallet_watchAsset":
            return True
        if name in {"personal_sign", "eth_sign"}:
            return self._personal_sign(name, params)
        if name in {"eth_signTypedData", "eth_signTypedData_v3", "eth_signTypedData_v4"}:
            return self._sign_typed(params)
        if name == "eth_sendTransaction":
            return self._send_via_kernel(params[0] if params else {})
        if name == "eth_getCode":
            return "0x"
        raise BrowserFarmError("unsupported_rpc", f"wallet method not supported: {name}")

    def _personal_sign(self, method: str, params: list[Any]) -> str:
        if method == "eth_sign":
            data = params[1] if len(params) > 1 else params[0]
        else:
            data = params[0] if params else "0x"
        message = _to_signable(data)
        signed = self.account.sign_message(message)
        sig = signed.signature
        hx = sig.hex() if isinstance(sig, (bytes, bytearray)) else str(sig)
        return hx if hx.startswith("0x") else "0x" + hx

    def _sign_typed(self, params: list[Any]) -> str:
        payload = params[1] if len(params) > 1 else (params[0] if params else {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        try:
            from eth_account.messages import encode_typed_data

            if isinstance(payload, dict) and "types" in payload:
                msg = encode_typed_data(full_message=payload)
            else:
                msg = encode_defunct(text=json.dumps(payload))
        except Exception:
            msg = encode_defunct(text=json.dumps(payload) if payload else "")
        signed = self.account.sign_message(msg)
        sig = signed.signature
        hx = sig.hex() if isinstance(sig, (bytes, bytearray)) else str(sig)
        return hx if hx.startswith("0x") else "0x" + hx

    def _send_via_kernel(self, tx: Any) -> str:
        if not isinstance(tx, dict):
            raise BrowserFarmError("bad_tx", "eth_sendTransaction без полей")
        if self.kernel is None:
            raise BrowserFarmError("kernel_missing", "Нет Kernel для sendTransaction")
        to = str(tx.get("to") or "")
        data_hex = str(tx.get("data") or tx.get("input") or "0x")
        value_raw = tx.get("value") or 0
        if isinstance(value_raw, str):
            value = int(value_raw, 16) if value_raw.startswith("0x") else int(value_raw or 0)
        else:
            value = int(value_raw or 0)
        data = bytes.fromhex(data_hex.replace("0x", "") or "")
        return self.kernel.send_calls([(to, data, value)])

    def _detect_extension(self) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            return bool(
                page.evaluate(
                    """() => {
                      const e = window.ethereum;
                      if (!e) return false;
                      return Boolean(e.isRabby || e.isMetaMask || e.isCoinbaseWallet);
                    }"""
                )
            )
        except Exception:
            return False

    def _looks_connected(self) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            return bool(
                page.evaluate(
                    r"""() => {
                      const t = (document.body && document.body.innerText) || "";
                      if (/connect wallet/i.test(t) && !/disconnect/i.test(t)) return false;
                      if (/0x[a-fA-F0-9]{4}\s?\.{2,3}\s?[a-fA-F0-9]{4}/.test(t)) return true;
                      if (/Disconnect/i.test(t)) return true;
                      return false;
                    }"""
                )
            )
        except Exception:
            return False

    def _dismiss_welcome(self) -> None:
        """The LIVE modal blocks Connect Wallet until Trade Points / X."""
        if self._click_first(DISMISS_SELECTORS, timeout_ms=700):
            self._sleep(0.3, 0.7)

    def _click_connect(self) -> None:
        self._click_first(CONNECT_SELECTORS, timeout_ms=8000)

    def _click_wallet_choice(self) -> None:
        self._click_first(WALLET_SELECTORS, timeout_ms=6000)

    def _click_first(self, selectors: tuple[str, ...], timeout_ms: int) -> bool:
        page = self._page
        if page is None:
            return False
        scopes: list[Any] = [page]
        try:
            scopes.extend(list(page.frames))
        except Exception:
            pass
        per = min(max(int(timeout_ms), 250), 1600)
        for scope in scopes:
            for selector in selectors:
                self._cancel()
                try:
                    loc = scope.locator(selector).first
                    loc.wait_for(state="visible", timeout=per)
                    loc.click(timeout=per)
                    return True
                except Exception:
                    continue
        return False

    def _confirm_popups_once(self) -> None:
        """Click Rabby/MetaMask confirm on extension pages. Main thread only."""
        context = self._context
        if context is None:
            return
        try:
            pages = list(context.pages)
        except Exception:
            return
        for popup in pages:
            try:
                url = popup.url or ""
            except Exception:
                continue
            if "chrome-extension://" not in url and "moz-extension://" not in url:
                continue
            try:
                if popup.locator('input[type="password"]').count() > 0:
                    continue
            except Exception:
                pass
            for selector in CONFIRM_BUTTONS:
                try:
                    btn = popup.locator(selector).first
                    if btn.is_visible():
                        btn.click(timeout=1500)
                        break
                except Exception:
                    continue

    def _sleep(self, lo: float, hi: float) -> None:
        import random

        delay = random.uniform(lo, hi)
        end = time.monotonic() + delay
        while time.monotonic() < end:
            self._cancel()
            time.sleep(min(0.25, end - time.monotonic()))


def _to_signable(data: Any):
    if isinstance(data, (bytes, bytearray)):
        return encode_defunct(primitive=bytes(data))
    text = str(data or "")
    if text.startswith("0x"):
        try:
            raw = bytes.fromhex(text[2:])
            try:
                decoded = raw.decode("utf-8")
                if decoded.isprintable() or "\n" in decoded:
                    return encode_defunct(text=decoded)
            except Exception:
                pass
            return encode_defunct(primitive=raw)
        except Exception:
            return encode_defunct(text=text)
    return encode_defunct(text=text)


def owner_account(private_key: str) -> LocalAccount:
    return Account.from_key(private_key)
