from __future__ import annotations

from decimal import Decimal
from typing import Any

import requests
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

from .accounts import AccountConfig
from .abis import ERC20_ABI
from .config import AppConfig
from .utils import short_address


def _inject_poa(w3: Web3) -> None:
    try:
        from web3.middleware import ExtraDataToPOAMiddleware

        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        return
    except Exception:
        pass
    try:
        from web3.middleware import geth_poa_middleware

        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    except Exception:
        pass


class CheckpointClient:
    def __init__(self, cfg: AppConfig, account: AccountConfig) -> None:
        self.cfg = cfg
        self.account_cfg = account
        self.account: LocalAccount = Account.from_key(account.private_key)
        self.address = Web3.to_checksum_address(self.account.address)
        self.label = account.label
        self.proxy = account.proxy

        self.http = requests.Session()
        self.http.headers.update(
            {
                "User-Agent": "CheckpointXPFarmer/0.1",
                "Accept": "application/json",
            }
        )
        if self.proxy:
            self.http.proxies.update({"http": self.proxy, "https": self.proxy})

        self.w3 = self._connect_rpc()
        self.usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(cfg.usdc),
            abi=ERC20_ABI,
        )
        self._usdc_decimals: int | None = None
        self.jwt: str | None = None

    def __repr__(self) -> str:
        return f"CheckpointClient(label={self.label!r}, address={self.address})"

    def _rpc_urls(self) -> list[str]:
        urls = [self.cfg.rpc_url, *self.cfg.rpc_fallbacks]
        out: list[str] = []
        for u in urls:
            if u and u not in out:
                out.append(u)
        return out

    def _connect_rpc(self) -> Web3:
        last_err: Exception | None = None
        for url in self._rpc_urls():
            try:
                request_kwargs: dict[str, Any] = {"timeout": self.cfg.request_timeout}
                if self.cfg.rpc_via_proxy and self.proxy:
                    request_kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs=request_kwargs))
                _inject_poa(w3)
                # light probe
                _ = w3.eth.chain_id
                self.rpc_url = url
                return w3
            except Exception as exc:
                last_err = exc
                continue
        raise RuntimeError(f"все RPC недоступны: {last_err}")

    def short(self) -> str:
        return f"{self.label} {short_address(self.address)}"

    def eth_balance(self) -> Decimal:
        wei = self.w3.eth.get_balance(self.address)
        return Decimal(wei) / Decimal(10**18)

    def usdc_decimals(self) -> int:
        if self._usdc_decimals is None:
            try:
                self._usdc_decimals = int(self.usdc.functions.decimals().call())
            except Exception:
                self._usdc_decimals = 6
        return self._usdc_decimals

    def usdc_balance(self) -> Decimal:
        raw = self.usdc.functions.balanceOf(self.address).call()
        return Decimal(raw) / Decimal(10 ** self.usdc_decimals())

    def to_usdc_units(self, amount: Decimal | str | float) -> int:
        return int(Decimal(str(amount)) * Decimal(10 ** self.usdc_decimals()))

    def from_usdc_units(self, raw: int) -> Decimal:
        return Decimal(raw) / Decimal(10 ** self.usdc_decimals())

    def send_tx(self, tx: dict[str, Any]) -> str:
        tx = dict(tx)
        tx.setdefault("from", self.address)
        tx.setdefault("nonce", self.w3.eth.get_transaction_count(self.address))
        tx.setdefault("chainId", self.cfg.chain_id)
        if "gas" not in tx:
            estimated = self.w3.eth.estimate_gas(tx)
            tx["gas"] = int(estimated * self.cfg.gas_multiplier)
        if "maxFeePerGas" not in tx and "gasPrice" not in tx:
            try:
                block = self.w3.eth.get_block("latest")
                base = block.get("baseFeePerGas") or self.w3.eth.gas_price
                tip = self.w3.to_wei(0.01, "gwei")
                tx["maxPriorityFeePerGas"] = tip
                tx["maxFeePerGas"] = int(base * 2) + tip
                tx["type"] = 2
            except Exception:
                tx["gasPrice"] = int(self.w3.eth.gas_price * self.cfg.gas_multiplier)

        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        receipt = self.w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=self.cfg.receipt_timeout
        )
        hx = tx_hash.hex() if hasattr(tx_hash, "hex") else self.w3.to_hex(tx_hash)
        if not str(hx).startswith("0x"):
            hx = "0x" + str(hx)
        if receipt.status != 1:
            raise RuntimeError(f"tx reverted: {hx}")
        return hx

    def build_contract_tx(self, function_call: Any) -> dict[str, Any]:
        return function_call.build_transaction(
            {
                "from": self.address,
                "nonce": self.w3.eth.get_transaction_count(self.address),
                "chainId": self.cfg.chain_id,
            }
        )

    def http_get(self, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.cfg.request_timeout)
        return self.http.get(url, **kwargs)

    def http_post(self, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.cfg.request_timeout)
        return self.http.post(url, **kwargs)
