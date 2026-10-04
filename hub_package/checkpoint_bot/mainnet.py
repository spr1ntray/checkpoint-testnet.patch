"""Ethereum L1 snapshot + tiny Uniswap swap for QuickNode's tx-history gate.

QuickNode rejects virgin EOAs even when they hold ≥ 0.001 ETH:

    "We require wallets to have a more established transaction history"

A $2-ish Uniswap V2 ETH→USDC swap is enough. The swap MUST leave ≥ 0.001 ETH
on the address, otherwise the next banner is "Invalid ETH mainnet balance".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from eth_account.signers.local import LocalAccount
from web3 import Web3

from checkpoint_bot.timing import sleep_jitter

MAINNET_CHAIN_ID = 1
MAINNET_NEED_WEI = 10**15  # 0.001 ETH — QuickNode still needs this after the swap
TARGET_SWAP_WEI = 7 * 10**14  # ~0.0007 ETH ≈ $2 at ~$2800
MIN_SWAP_WEI = 3 * 10**13  # 0.00003 ETH — still a real swap, not dust spam
WRAP_WEI = 2 * 10**13  # 0.00002 ETH WETH deposit fallback
SWAP_GAS_LIMIT = 220_000
WRAP_GAS_LIMIT = 60_000
SELF_GAS_LIMIT = 21_000

UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

MAINNET_RPCS = (
    "https://ethereum.publicnode.com",
    "https://cloudflare-eth.com",
    "https://eth.llamarpc.com",
)

UNISWAP_V2_ABI = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
    {
        "name": "swapExactETHForTokens",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
]

WETH_ABI = [
    {
        "name": "deposit",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [],
        "outputs": [],
    }
]


@dataclass(frozen=True)
class MainnetSnapshot:
    wei: int
    nonce: int


def needs_mainnet_history(nonce: int) -> bool:
    return int(nonce) <= 0


def swap_budget_wei(
    balance: int,
    gas_cost: int,
    *,
    target: int = TARGET_SWAP_WEI,
    reserve: int = MAINNET_NEED_WEI,
) -> int:
    """ETH we can sell without dropping below QuickNode's 0.001 gate."""
    spendable = int(balance) - int(reserve) - max(0, int(gas_cost))
    if spendable < MIN_SWAP_WEI:
        return 0
    return min(int(target), spendable)


def wrap_budget_wei(
    balance: int,
    gas_cost: int,
    *,
    reserve: int = MAINNET_NEED_WEI,
) -> int:
    spendable = int(balance) - int(reserve) - max(0, int(gas_cost))
    if spendable <= 0:
        return 0
    return min(WRAP_WEI, spendable)


def can_self_tx(
    balance: int,
    gas_cost: int,
    *,
    reserve: int = MAINNET_NEED_WEI,
) -> bool:
    """0-value self-tx still needs gas while leaving QuickNode's 0.001 gate."""
    return int(balance) - max(0, int(gas_cost)) >= int(reserve)


def mainnet_snapshot(address: str, *, timeout: float = 12.0) -> MainnetSnapshot:
    """eth_getBalance + eth_getTransactionCount. Direct session, no account proxy."""
    import requests

    session = requests.Session()
    session.trust_env = False
    checksum = Web3.to_checksum_address(address)
    last: Exception | None = None
    for url in MAINNET_RPCS:
        try:
            wei = _rpc_int(
                session,
                url,
                "eth_getBalance",
                [checksum, "latest"],
                timeout=timeout,
            )
            nonce = _rpc_int(
                session,
                url,
                "eth_getTransactionCount",
                [checksum, "latest"],
                timeout=timeout,
            )
            return MainnetSnapshot(wei=wei, nonce=nonce)
        except Exception as exc:
            last = exc
            continue
    raise RuntimeError(f"Не удалось прочитать Ethereum mainnet: {last}")


def activate_mainnet_history(
    account: LocalAccount,
    *,
    log: Callable[[str], None],
    cancel: Callable[[], None],
) -> MainnetSnapshot:
    """Create L1 tx history if nonce is 0. No-op when the wallet already traded."""
    address = Web3.to_checksum_address(account.address)
    snap = mainnet_snapshot(address)
    if not needs_mainnet_history(snap.nonce):
        log("На Ethereum уже есть транзакции — свап для крана не нужен")
        return snap

    cancel()
    w3 = _connect_l1()
    fee = _max_fee_per_gas(w3)
    swap_gas_cost = SWAP_GAS_LIMIT * fee
    amount = swap_budget_wei(snap.wei, swap_gas_cost)
    if amount > 0:
        try:
            hx = _uniswap_eth_to_usdc(w3, account, amount=amount, log=log, cancel=cancel)
            log(f"Свап ETH→USDC на Ethereum прошёл: {hx}")
            return _wait_history(address, prev_nonce=snap.nonce, cancel=cancel)
        except Exception as exc:
            log(f"Свап ETH→USDC не вышел ({exc}) — пробую wrap WETH")

    wrap_gas_cost = WRAP_GAS_LIMIT * fee
    wrap = wrap_budget_wei(snap.wei, wrap_gas_cost)
    if wrap > 0:
        try:
            hx = _wrap_eth(w3, account, amount=wrap, log=log, cancel=cancel)
            log(f"Wrap ETH→WETH на Ethereum прошёл: {hx}")
            return _wait_history(address, prev_nonce=snap.nonce, cancel=cancel)
        except Exception as exc:
            log(f"Wrap не вышел ({exc}) — пробую пустой self-tx")

    self_gas_cost = SELF_GAS_LIMIT * fee
    if can_self_tx(snap.wei, self_gas_cost):
        hx = _self_tx(w3, account, log=log, cancel=cancel)
        log(f"Self-tx на Ethereum прошёл: {hx}")
        return _wait_history(address, prev_nonce=snap.nonce, cancel=cancel)

    log(
        "На Ethereum слишком мало ETH, чтобы сделать свап и оставить ≥ 0.001. "
        "Кран всё равно попробуем."
    )
    return snap


def _rpc_int(
    session: Any,
    url: str,
    method: str,
    params: list[Any],
    *,
    timeout: float,
) -> int:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = session.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    result = (resp.json() or {}).get("result")
    if result in (None, ""):
        raise RuntimeError(f"empty {method}")
    return int(str(result), 16)


def _connect_l1() -> Web3:
    import requests

    session = requests.Session()
    session.trust_env = False
    last: Exception | None = None
    for url in MAINNET_RPCS:
        try:
            w3 = Web3(
                Web3.HTTPProvider(
                    url,
                    request_kwargs={"timeout": 12},
                    session=session,
                )
            )
            if int(w3.eth.chain_id) != MAINNET_CHAIN_ID:
                raise RuntimeError(f"unexpected chain_id {w3.eth.chain_id}")
            return w3
        except Exception as exc:
            last = exc
            continue
    raise RuntimeError(f"Ethereum mainnet RPC недоступен: {last}")


def _max_fee_per_gas(w3: Web3) -> int:
    """Worst-case wei/gas we may pay (EIP-1559 maxFee). Budget with this, not spot gasPrice."""
    try:
        block = w3.eth.get_block("latest")
        base = int(block.get("baseFeePerGas") or 0)
        if base > 0:
            tip = int(w3.to_wei(0.05, "gwei"))
            return base * 2 + tip
    except Exception:
        pass
    return max(1, int(w3.eth.gas_price)) * 2


def _wait_history(
    address: str,
    *,
    prev_nonce: int,
    cancel: Callable[[], None],
) -> MainnetSnapshot:
    """Receipt is not enough — QuickNode's indexer can lag a few seconds."""
    last = mainnet_snapshot(address)
    for _ in range(8):
        if last.nonce > int(prev_nonce):
            return last
        cancel()
        sleep_jitter(1.2, 2.0, cancel_check=cancel)
        last = mainnet_snapshot(address)
    return last


def _uniswap_eth_to_usdc(
    w3: Web3,
    account: LocalAccount,
    *,
    amount: int,
    log: Callable[[str], None],
    cancel: Callable[[], None],
) -> str:
    router = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V2_ROUTER),
        abi=UNISWAP_V2_ABI,
    )
    path = [
        Web3.to_checksum_address(WETH),
        Web3.to_checksum_address(USDC),
    ]
    amounts = router.functions.getAmountsOut(int(amount), path).call()
    out_min = int(amounts[-1]) * 90 // 100
    deadline = int(time.time()) + 600
    sender = Web3.to_checksum_address(account.address)
    log(
        f"Свап на Ethereum: {Web3.from_wei(amount, 'ether')} ETH → USDC "
        f"(оставляем ≥ 0.001 ETH для крана)"
    )
    cancel()
    fn = router.functions.swapExactETHForTokens(out_min, path, sender, deadline)
    return _send_l1(w3, account, fn, value=int(amount), gas=SWAP_GAS_LIMIT)


def _wrap_eth(
    w3: Web3,
    account: LocalAccount,
    *,
    amount: int,
    log: Callable[[str], None],
    cancel: Callable[[], None],
) -> str:
    weth = w3.eth.contract(
        address=Web3.to_checksum_address(WETH),
        abi=WETH_ABI,
    )
    log(f"Wrap на Ethereum: {Web3.from_wei(amount, 'ether')} ETH → WETH")
    cancel()
    return _send_l1(w3, account, weth.functions.deposit(), value=int(amount), gas=WRAP_GAS_LIMIT)


def _self_tx(
    w3: Web3,
    account: LocalAccount,
    *,
    log: Callable[[str], None],
    cancel: Callable[[], None],
) -> str:
    sender = Web3.to_checksum_address(account.address)
    log("Пустой self-tx на Ethereum — только чтобы появилась история")
    cancel()
    nonce = int(w3.eth.get_transaction_count(sender))
    tx: dict[str, Any] = {
        "from": sender,
        "to": sender,
        "value": 0,
        "nonce": nonce,
        "chainId": MAINNET_CHAIN_ID,
        "gas": SELF_GAS_LIMIT,
    }
    _apply_fees(w3, tx, gas=SELF_GAS_LIMIT)
    return _broadcast(w3, account, tx)


def _send_l1(
    w3: Web3,
    account: LocalAccount,
    fn: Any,
    *,
    value: int,
    gas: int,
) -> str:
    sender = Web3.to_checksum_address(account.address)
    nonce = int(w3.eth.get_transaction_count(sender))
    tx = fn.build_transaction(
        {
            "from": sender,
            "value": int(value),
            "nonce": nonce,
            "chainId": MAINNET_CHAIN_ID,
            "gas": int(gas),
        }
    )
    _apply_fees(w3, tx, gas=int(gas))
    try:
        estimated = int(w3.eth.estimate_gas(tx))
        if estimated > int(gas):
            raise RuntimeError(f"L1 gas estimate {estimated} выше лимита {gas}")
        tx["gas"] = min(int(gas), int(estimated * 1.25))
    except RuntimeError:
        raise
    except Exception:
        tx["gas"] = int(gas)
    return _broadcast(w3, account, tx)


def _apply_fees(w3: Web3, tx: dict[str, Any], *, gas: int) -> None:
    tx["gas"] = int(gas)
    try:
        fee = _max_fee_per_gas(w3)
        tip = int(w3.to_wei(0.05, "gwei"))
        tx["maxPriorityFeePerGas"] = min(tip, fee)
        tx["maxFeePerGas"] = fee
        tx["type"] = 2
        tx.pop("gasPrice", None)
    except Exception:
        tx["gasPrice"] = int(w3.eth.gas_price)
        tx.pop("maxFeePerGas", None)
        tx.pop("maxPriorityFeePerGas", None)
        tx.pop("type", None)


def _broadcast(w3: Web3, account: LocalAccount, tx: dict[str, Any]) -> str:
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    hx = tx_hash.hex() if hasattr(tx_hash, "hex") else w3.to_hex(tx_hash)
    if not str(hx).startswith("0x"):
        hx = "0x" + str(hx)
    status = int(getattr(receipt, "status", None) or receipt.get("status") or 0)
    if status != 1:
        raise RuntimeError(f"L1 tx reverted: {hx}")
    return hx
