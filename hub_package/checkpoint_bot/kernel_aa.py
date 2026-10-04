"""Checkpoint UI farms XP via ZeroDev Kernel (EntryPoint 0.7), not raw EOA.

EOA fillOffer confirms on-chain but buy XP does not move. The live HAR
sends UserOperations through rpc.zerodev.app with ECDSA validator + paymaster.
"""

from __future__ import annotations

import re
import time
from typing import Any

import requests
from eth_abi import encode
from eth_account.messages import encode_defunct
from web3 import Web3

from .client import CheckpointClient
from .config import AppConfig
from .utils import scrub_secrets


class LowGasError(RuntimeError):
    """Empty wallet — stop the farm, not a software failure."""

    def __init__(self, message: str = "Мало ETH на газ", *, eth: str = "0", need: str = "> 0") -> None:
        super().__init__(message)
        self.eth = eth
        self.need = need

ENTRY_POINT = Web3.to_checksum_address("0x0000000071727De22E5E9d8BAf0edAc6f37da032")
ECDSA_VALIDATOR = Web3.to_checksum_address("0x845ADb2C711129d4f3966735eD98a9F09fC4cE57")
META_FACTORY = Web3.to_checksum_address("0xd703aaE79538628d27099B8c4f621bE4CCd142d5")
KERNEL_FACTORY = Web3.to_checksum_address("0x2577507b78c2008Ff367261CB6285d44ba5eF2E9")
ZERODEV_RPC = (
    "https://rpc.zerodev.app/api/v3/"
    "3205195c-2fe9-413e-8058-f82a6bef7934/chain/421614?selfFunded=true"
)
ZERODEV_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://checkpoint.exchange",
    "Referer": "https://checkpoint.exchange/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
}
SEL_EXECUTE = bytes.fromhex("e9ae5c53")
SEL_CREATE = bytes.fromhex("c5265d5d")
SEL_INIT = bytes.fromhex("3c3b752b")
SEL_GET_ADDRESS = bytes.fromhex("0ba64edb")  # getAddress(bytes,bytes32) — filled at runtime
SEL_GET_NONCE = Web3.keccak(text="getNonce(address,uint192)")[:4]
SEL_GET_USER_OP_HASH = Web3.keccak(
    text="getUserOpHash((address,uint256,bytes,bytes,bytes32,uint256,bytes32,bytes,bytes))"
)[:4]
# viem / permissionless dummy ECDSA sig. 65 zero bytes fail paymaster simulation.
DUMMY_SIG = bytes.fromhex(
    "fffffffffffffffffffffffffffffff000000000000000000000000000000000"
    "7aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1c"
)
# ZeroDev bundler (EP 0.7) rejects UserOps below this verification gas.
MIN_VERIFICATION_GAS = 10_000
_VGL_NEED_RE = re.compile(r"verificationGasLimit must be at least (\d+)", re.I)


def _sel(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


SEL_GET_ADDRESS = _sel("getAddress(bytes,bytes32)")
SEL_GET_NONCE = _sel("getNonce(address,uint192)")


def _paymaster_refused(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "sponsoring",
            "paymaster",
            "erc20 gas",
            "gas token",
            "gas sponsoring",
        )
    )


def _should_retry_self_funded(exc: Exception) -> bool:
    """Paymaster stub lied or bundler wants native on the Kernel sender."""
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "aa21",
            "aa23",
            "aa13",
            "didn't pay prefund",
            "did not pay prefund",
            "paymaster",
            "insufficient funds",
            "not enough native",
            "exceeds the balance of the account",
        )
    )


def _rpc_snippet(resp: requests.Response) -> str:
    try:
        payload = resp.json()
        text = str(payload)
    except Exception:
        text = resp.text or ""
    return scrub_secrets(text.replace("\n", " ").strip())[:160]


def _hex(value: int | bytes | str) -> str:
    if isinstance(value, int):
        return hex(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    raise TypeError(type(value))


def _int(x: Any) -> int:
    if x is None or x == "":
        return 0
    if isinstance(x, int):
        return x
    return int(str(x), 16) if str(x).startswith("0x") else int(x)


def clamp_user_op_gas(user_op: dict[str, Any], *, floor: int | None = None) -> dict[str, Any]:
    """Bundler rejects verificationGasLimit below 10000 even if estimate returned 0."""
    need = MIN_VERIFICATION_GAS if floor is None else max(MIN_VERIFICATION_GAS, int(floor))
    current = _int(user_op.get("verificationGasLimit") or 0)
    if current < need:
        user_op["verificationGasLimit"] = _hex(need)
    return user_op


def _verification_gas_floor_from_error(exc: Exception) -> int | None:
    match = _VGL_NEED_RE.search(str(exc))
    if not match:
        return None
    return int(match.group(1))


def _pack_u128_pair(hi: int, lo: int) -> bytes:
    return int(hi).to_bytes(16, "big") + int(lo).to_bytes(16, "big")


def kernel_init_data(owner: str) -> bytes:
    owner_b = bytes.fromhex(Web3.to_checksum_address(owner)[2:])
    root_validator = bytes.fromhex("01" + ECDSA_VALIDATOR[2:])  # bytes21
    return SEL_INIT + encode(
        ["bytes21", "address", "bytes", "bytes", "bytes"],
        [root_validator, "0x" + "00" * 20, owner_b, b"", b""],
    )


def kernel_factory_data(owner: str) -> bytes:
    init = kernel_init_data(owner)
    salt = b"\x00" * 32
    return SEL_CREATE + encode(
        ["address", "bytes", "bytes32"],
        [KERNEL_FACTORY, init, salt],
    )


def encode_execute_single(target: str, data: bytes, value: int = 0) -> bytes:
    inner = encode(
        ["address", "uint256", "bytes"],
        [Web3.to_checksum_address(target), int(value), data],
    )
    mode = (0).to_bytes(32, "big")
    return SEL_EXECUTE + encode(["bytes32", "bytes"], [mode, inner])


def encode_execute_batch(calls: list[tuple[str, bytes, int]]) -> bytes:
    executions = [
        (Web3.to_checksum_address(target), int(value), data) for target, data, value in calls
    ]
    inner = encode(
        ["(address,uint256,bytes)[]"],
        [executions],
    )
    mode = (1).to_bytes(1, "big") + b"\x00" * 31
    return SEL_EXECUTE + encode(["bytes32", "bytes"], [mode, inner])


def _nonce_key() -> int:
    return int(ECDSA_VALIDATOR, 16)


class KernelAccount:
    def __init__(self, client: CheckpointClient, cfg: AppConfig) -> None:
        self.client = client
        self.cfg = cfg
        self.owner = client.address
        self.sender = self._predict_sender()
        self._deployed: bool | None = None
        self._zd = requests.Session()
        self._zd.trust_env = False
        headers = dict(ZERODEV_HEADERS)
        identity = getattr(client, "identity", None)
        if identity is not None and getattr(identity, "user_agent", ""):
            headers["User-Agent"] = identity.user_agent
        self._zd.headers.update(headers)
        if client.proxy:
            self._zd.proxies.update({"http": client.proxy, "https": client.proxy})
        self._dropped_proxy = False

    def _post_rpc(self, method: str, params: list[Any]) -> requests.Response:
        return self._zd.post(
            ZERODEV_RPC,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=self.cfg.request_timeout,
        )

    def _rpc(self, method: str, params: list[Any]) -> Any:
        # Bundler allowlists Origin https://checkpoint.exchange.
        # Extra Client-Hints from the Checkpoint session 400 the bundler;
        # keep a dedicated session. Proxy first, then retry direct (1.6.6 path).
        last_exc: Exception | None = None
        for attempt in range(4):
            resp = self._post_rpc(method, params)
            if resp.status_code >= 400 and self.client.proxy and not self._dropped_proxy:
                self._dropped_proxy = True
                self._zd.proxies.clear()
                resp = self._post_rpc(method, params)
            snippet = _rpc_snippet(resp) if resp.status_code >= 400 else ""
            rate_limited = resp.status_code == 429 or "rate limit" in snippet.lower()
            if rate_limited and attempt < 3:
                time.sleep(1.2 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                if "allowlist" in snippet.lower():
                    raise RuntimeError(f"zerodev HTTP {resp.status_code} allowlist {method}")
                raise RuntimeError(f"zerodev HTTP {resp.status_code} {method}: {snippet}"[:240])
            body = resp.json()
            if body.get("error"):
                err = body["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                text = scrub_secrets(str(msg))
                if "rate limit" in text.lower() and attempt < 3:
                    time.sleep(1.2 * (attempt + 1))
                    last_exc = RuntimeError(f"zerodev {method}: {text}"[:240])
                    continue
                raise RuntimeError(f"zerodev {method}: {text}"[:240])
            return body.get("result")
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"zerodev {method}: rate limited")

    def _eth_call(self, to: str, data: bytes) -> bytes:
        raw = self.client.w3.eth.call({"to": Web3.to_checksum_address(to), "data": data})
        return bytes(raw) if not isinstance(raw, bytes) else raw

    def _predict_sender(self) -> str:
        init = kernel_init_data(self.owner)
        data = SEL_GET_ADDRESS + encode(["bytes", "bytes32"], [init, b"\x00" * 32])
        out = self._eth_call(KERNEL_FACTORY, data)
        addr = "0x" + out[-20:].hex()
        return Web3.to_checksum_address(addr)

    def is_deployed(self) -> bool:
        if self._deployed is None:
            code = self.client.w3.eth.get_code(self.sender)
            self._deployed = bool(code and code not in (b"", b"\x00"))
        return self._deployed

    def nonce(self) -> int:
        data = SEL_GET_NONCE + encode(
            ["address", "uint192"],
            [self.sender, _nonce_key()],
        )
        try:
            out = self._eth_call(ENTRY_POINT, data)
            seq = int.from_bytes(out[-8:], "big") if out else 0
        except Exception:
            seq = 0
        return (_nonce_key() << 64) | seq

    def send_calls(self, calls: list[tuple[str, bytes, int]]) -> str:
        if len(calls) == 1:
            call_data = encode_execute_single(calls[0][0], calls[0][1], calls[0][2])
        else:
            call_data = encode_execute_batch(calls)
        factory = None
        factory_data = None
        if not self.is_deployed():
            factory = META_FACTORY
            factory_data = "0x" + kernel_factory_data(self.owner).hex()

        prices = self._rpc("zd_getUserOperationGasPrice", []) or {}
        user_op = self._unsigned_user_op(call_data, factory, factory_data, prices)
        sponsored = False
        try:
            stub = self._rpc(
                "pm_getPaymasterStubData",
                [user_op, ENTRY_POINT, hex(self.cfg.chain_id), None],
            ) or {}
            if isinstance(stub, dict):
                user_op.update(
                    {k: v for k, v in stub.items() if v is not None and k != "paymasterAndData"}
                )
            sponsored = bool(user_op.get("paymaster"))
        except Exception as exc:
            if not _paymaster_refused(exc):
                raise
            sponsored = False

        # selfFunded bundler: keep native on the Kernel even if a stub paymaster
        # appeared. Empty EOA is only fatal when there is no sponsor either.
        try:
            self._fund_sender_if_needed()
        except LowGasError:
            if not sponsored:
                raise

        if not sponsored:
            user_op = self._unsigned_user_op(call_data, factory, factory_data, prices)

        try:
            return self._estimate_sign_send(user_op, sponsored=sponsored)
        except Exception as exc:
            if not sponsored or not _should_retry_self_funded(exc):
                raise
            self._fund_sender_if_needed()
            user_op = self._unsigned_user_op(call_data, factory, factory_data, prices)
            return self._estimate_sign_send(user_op, sponsored=False)

    def _estimate_sign_send(self, user_op: dict[str, Any], *, sponsored: bool) -> str:
        gas = self._rpc("eth_estimateUserOperationGas", [user_op, ENTRY_POINT]) or {}
        if isinstance(gas, dict):
            for k in (
                "callGasLimit",
                "verificationGasLimit",
                "preVerificationGas",
                "paymasterVerificationGasLimit",
                "paymasterPostOpGasLimit",
            ):
                if gas.get(k):
                    user_op[k] = gas[k]
        clamp_user_op_gas(user_op)

        if sponsored:
            pm = self._rpc(
                "pm_getPaymasterData",
                [user_op, ENTRY_POINT, hex(self.cfg.chain_id), None],
            ) or {}
            if isinstance(pm, dict):
                user_op.update({k: v for k, v in pm.items() if v is not None})
            clamp_user_op_gas(user_op)

        user_op["signature"] = self._sign(user_op)
        try:
            op_hash = self._rpc("eth_sendUserOperation", [user_op, ENTRY_POINT])
        except RuntimeError as exc:
            floor = _verification_gas_floor_from_error(exc)
            if floor is None:
                raise
            clamp_user_op_gas(user_op, floor=floor)
            user_op["signature"] = self._sign(user_op)
            op_hash = self._rpc("eth_sendUserOperation", [user_op, ENTRY_POINT])
        if not op_hash:
            raise RuntimeError("zerodev sendUserOperation empty")
        return self._wait_receipt(str(op_hash))

    def _unsigned_user_op(
        self,
        call_data: bytes,
        factory: str | None,
        factory_data: str | None,
        prices: dict[str, Any],
    ) -> dict[str, Any]:
        max_fee = _int(prices.get("maxFeePerGas") or prices.get("fast", {}).get("maxFeePerGas") or "0x1800000")
        max_prio = _int(
            prices.get("maxPriorityFeePerGas")
            or prices.get("fast", {}).get("maxPriorityFeePerGas")
            or "0x1e000"
        )
        user_op: dict[str, Any] = {
            "sender": self.sender,
            "nonce": _hex(self.nonce()),
            "callData": "0x" + call_data.hex(),
            "callGasLimit": "0x0",
            "verificationGasLimit": "0x0",
            "preVerificationGas": "0x0",
            "maxFeePerGas": _hex(max_fee),
            "maxPriorityFeePerGas": _hex(max_prio),
            "signature": "0x" + DUMMY_SIG.hex(),
        }
        if factory:
            user_op["factory"] = factory
            user_op["factoryData"] = factory_data
        return user_op

    def _fund_sender_if_needed(self) -> None:
        w3 = self.client.w3
        bal = int(w3.eth.get_balance(self.sender))
        need = int(w3.to_wei("0.00008", "ether"))
        if bal >= need:
            return
        eoa = int(w3.eth.get_balance(self.owner))
        if eoa <= 0:
            raise LowGasError(
                "Мало ETH на газ",
                eth="0",
                need=str(Web3.from_wei(need, "ether")),
            )
        send_amt = need if eoa > need else eoa
        try:
            self.client.send_tx({"to": self.sender, "value": int(send_amt)})
        except Exception as exc:
            raise LowGasError(
                "Мало ETH на газ",
                eth=str(Web3.from_wei(eoa, "ether")),
                need=str(Web3.from_wei(need, "ether")),
            ) from exc

    def _sign(self, user_op: dict[str, Any]) -> str:
        packed = _to_packed(user_op)
        data = SEL_GET_USER_OP_HASH + encode(
            ["(address,uint256,bytes,bytes,bytes32,uint256,bytes32,bytes,bytes)"],
            [packed],
        )
        digest = self._eth_call(ENTRY_POINT, data)
        if len(digest) >= 32:
            digest = digest[:32]
        signed = self.client.account.sign_message(encode_defunct(primitive=digest))
        sig = signed.signature
        if isinstance(sig, str):
            return sig if sig.startswith("0x") else "0x" + sig
        hx = sig.hex()
        return hx if hx.startswith("0x") else "0x" + hx

    def _wait_receipt(self, op_hash: str, timeout: int = 180) -> str:
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            rec = self._rpc("eth_getUserOperationReceipt", [op_hash])
            last = rec
            if rec and rec.get("receipt"):
                if rec.get("success") is False:
                    raise RuntimeError("kernel userOp failed")
                txh = rec["receipt"].get("transactionHash") or rec.get("receipt", {}).get("transactionHash")
                if txh:
                    self._deployed = True
                    return str(txh)
            time.sleep(1.2)
        raise RuntimeError(f"kernel userOp timeout: {last}")


def _to_packed(user_op: dict[str, Any]) -> tuple:
    sender = Web3.to_checksum_address(user_op["sender"])
    nonce = _int(user_op["nonce"])
    factory = user_op.get("factory")
    factory_data = user_op.get("factoryData") or "0x"
    if factory:
        init_code = bytes.fromhex(factory[2:]) + bytes.fromhex(factory_data.replace("0x", ""))
    else:
        init_code = b""
    call_data = bytes.fromhex(user_op["callData"].replace("0x", ""))
    account_gas = _pack_u128_pair(
        _int(user_op.get("verificationGasLimit") or 0),
        _int(user_op.get("callGasLimit") or 0),
    )
    pre_ver = _int(user_op.get("preVerificationGas") or 0)
    gas_fees = _pack_u128_pair(
        _int(user_op.get("maxPriorityFeePerGas") or 0),
        _int(user_op.get("maxFeePerGas") or 0),
    )
    paymaster = user_op.get("paymaster")
    if paymaster:
        pm_data = bytes.fromhex((user_op.get("paymasterData") or "0x").replace("0x", ""))
        paymaster_and_data = (
            bytes.fromhex(paymaster[2:])
            + _int(user_op.get("paymasterVerificationGasLimit") or 0).to_bytes(16, "big")
            + _int(user_op.get("paymasterPostOpGasLimit") or 0).to_bytes(16, "big")
            + pm_data
        )
    else:
        paymaster_and_data = b""
    signature = bytes.fromhex((user_op.get("signature") or "0x").replace("0x", ""))
    return (
        sender,
        nonce,
        init_code,
        call_data,
        account_gas,
        pre_ver,
        gas_fees,
        paymaster_and_data,
        signature,
    )
