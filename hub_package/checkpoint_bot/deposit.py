from __future__ import annotations

from typing import Any

from eth_abi import encode
from web3 import Web3  # noqa: F401 — used below

from .client import CheckpointClient
from .config import AppConfig
from .utils import encode_erc7930_account_hex


def _normalize_typed_data(typed: dict[str, Any], auth: dict[str, Any]) -> dict[str, Any]:
    """Ensure message ints for EIP-712 signing."""
    data = {
        "types": typed.get("types") or {},
        "primaryType": typed.get("primaryType") or "DepositAuth",
        "domain": dict(typed.get("domain") or {}),
        "message": dict(typed.get("message") or {}),
    }
    msg = data["message"]
    for key in ("pointsId", "nonce", "expiry"):
        if key in msg:
            msg[key] = int(msg[key])
        elif key in auth:
            msg[key] = int(auth[key])
    if "account" not in msg and auth.get("account"):
        msg["account"] = auth["account"]
    if "operator" not in msg and auth.get("operator"):
        msg["operator"] = auth["operator"]
    types = data["types"]
    domain = data["domain"]
    if "chainId" in domain:
        domain["chainId"] = int(domain["chainId"])
    if "EIP712Domain" not in types:
        eip_fields = []
        if "name" in domain:
            eip_fields.append({"name": "name", "type": "string"})
        if "version" in domain:
            eip_fields.append({"name": "version", "type": "string"})
        if "chainId" in domain:
            eip_fields.append({"name": "chainId", "type": "uint256"})
        if "verifyingContract" in domain:
            eip_fields.append({"name": "verifyingContract", "type": "address"})
        types = dict(types)
        types["EIP712Domain"] = eip_fields
        data["types"] = types
    return data


def _sign_typed_data(account: Any, full: dict[str, Any]) -> str:
    """Sign EIP-712 across eth-account API variants."""
    try:
        from eth_account.messages import encode_typed_data

        signable = encode_typed_data(full_message=full)
        signed = account.sign_message(signable)
    except TypeError:
        from eth_account.messages import encode_typed_data

        signable = encode_typed_data(
            domain_data=full["domain"],
            message_types={k: v for k, v in full["types"].items() if k != "EIP712Domain"},
            message_data=full["message"],
        )
        signed = account.sign_message(signable)
    except Exception:
        # last resort: sign_typed_data on Account
        signed = account.sign_typed_data(
            full["domain"],
            {k: v for k, v in full["types"].items() if k != "EIP712Domain"},
            full["message"],
        )
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    return signature


def request_deposit_authorization(
    client: CheckpointClient,
    cfg: AppConfig,
    points_id: int,
) -> dict[str, Any]:
    account = encode_erc7930_account_hex(client.address)
    body = {
        "pointsId": str(points_id),
        "account": account,
        "operator": client.address,
    }
    resp = client.http_post(
        f"{cfg.oracle_api}/claim/deposit/authorization",
        json=body,
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"deposit auth failed: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    if not data.get("success", True):
        raise RuntimeError(f"deposit auth error: {data}")
    return data.get("authorization") or data


def request_deposit_claim(
    client: CheckpointClient,
    cfg: AppConfig,
    points_id: int,
    authorization: dict[str, Any],
    signature: str,
) -> dict[str, Any]:
    account = authorization.get("account") or encode_erc7930_account_hex(client.address)
    body = {
        "pointsId": str(points_id),
        "account": account,
        "operator": authorization.get("operator") or client.address,
        "authorization": {
            "nonce": str(authorization.get("nonce")),
            "expiry": str(authorization.get("expiry")),
            "signature": signature,
        },
    }
    resp = client.http_post(
        f"{cfg.oracle_api}/claim/deposit",
        json=body,
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"deposit claim failed: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    if not data.get("success", True):
        raise RuntimeError(f"deposit claim error: {data}")
    return data


def resolve_deposit_contract(client: CheckpointClient, cfg: AppConfig, points_id: int) -> str | None:
    """Best-effort registry lookup for deposit contract."""
    registry = Web3.to_checksum_address(cfg.registry)
    # try common getter signatures via eth_call raw
    candidates = [
        # depositOf(uint256)
        ("0x" + Web3.keccak(text="depositOf(uint256)")[:4].hex(), True),
        ("0x" + Web3.keccak(text="getDeposit(uint256)")[:4].hex(), True),
        ("0x" + Web3.keccak(text="programs(uint256)")[:4].hex(), False),
        ("0x" + Web3.keccak(text="getProgram(uint256)")[:4].hex(), False),
    ]
    for sel_hex, single_addr in candidates:
        try:
            data = bytes.fromhex(sel_hex[2:]) + encode(["uint256"], [points_id])
            result = client.w3.eth.call({"to": registry, "data": data})
            if not result or len(result) < 32:
                continue
            if single_addr:
                addr = Web3.to_checksum_address("0x" + result[-20:].hex())
            else:
                addr = Web3.to_checksum_address("0x" + result[12:32].hex())
            if int(addr, 16) != 0:
                return addr
        except Exception:
            continue
    return None


def onchain_deposit(
    client: CheckpointClient,
    cfg: AppConfig,
    claim: dict[str, Any],
    oracle_signature: str,
    deposit_contract: str,
) -> str:
    """Encode Deposit.deposit(claim, signature) with flexible struct layout."""
    # claim fields from oracle API
    chain_id = int(claim.get("chainId") or cfg.chain_id)
    points_id = int(claim.get("pointsId"))
    account = claim.get("account")
    if isinstance(account, str) and account.startswith("0x"):
        account_bytes = bytes.fromhex(account[2:])
    else:
        account_bytes = bytes(account)
    operator = Web3.to_checksum_address(claim.get("operator") or client.address)
    amount = int(claim.get("amount"))
    expiry = int(claim.get("expiry"))
    nonce = int(claim.get("nonce"))

    sig = oracle_signature
    if isinstance(sig, str):
        sig_bytes = bytes.fromhex(sig[2:] if sig.startswith("0x") else sig)
    else:
        sig_bytes = bytes(sig)

    # Try struct (uint256,uint256,bytes,address,uint256,uint256,uint256) + bytes
    # matching docs: chainId, pointsId, account, operator, amount, expiry, nonce
    sel = Web3.keccak(text="deposit((uint256,uint256,bytes,address,uint256,uint256,uint256),bytes)")[:4]
    try:
        body = encode(
            ["(uint256,uint256,bytes,address,uint256,uint256,uint256)", "bytes"],
            [(chain_id, points_id, account_bytes, operator, amount, expiry, nonce), sig_bytes],
        )
        data = sel + body
        return client.send_tx(
            {
                "to": Web3.to_checksum_address(deposit_contract),
                "data": data,
                "value": 0,
            }
        )
    except Exception as first:
        # Alternate: deposit(bytes,address,uint256,uint256,uint256,uint256,bytes)
        sel2 = Web3.keccak(
            text="deposit(bytes,address,uint256,uint256,uint256,uint256,bytes)"
        )[:4]
        body2 = encode(
            ["bytes", "address", "uint256", "uint256", "uint256", "uint256", "bytes"],
            [account_bytes, operator, points_id, amount, expiry, nonce, sig_bytes],
        )
        try:
            return client.send_tx(
                {
                    "to": Web3.to_checksum_address(deposit_contract),
                    "data": sel2 + body2,
                    "value": 0,
                }
            )
        except Exception as second:
            raise RuntimeError(
                f"deposit on-chain failed: {first}; alt: {second}"
            ) from second


def try_deposit(client: CheckpointClient, cfg: AppConfig, points_id: int) -> dict[str, Any]:
    """Full deposit flow. Returns event dict details."""
    auth = request_deposit_authorization(client, cfg, points_id)
    typed = auth.get("typedData")
    if not typed:
        raise RuntimeError("no typedData in deposit authorization")

    full = _normalize_typed_data(typed, auth)
    signature = _sign_typed_data(client.account, full)

    claim_resp = request_deposit_claim(client, cfg, points_id, auth, signature)
    claim = claim_resp.get("claim") or {}
    oracle_sig = claim_resp.get("signature")
    if not claim or not oracle_sig:
        raise RuntimeError(f"incomplete claim response: {str(claim_resp)[:300]}")

    deposit_addr = resolve_deposit_contract(client, cfg, points_id)
    if not deposit_addr:
        raise RuntimeError("could not resolve deposit contract from registry")

    tx_hash = onchain_deposit(client, cfg, claim, oracle_sig, deposit_addr)
    return {
        "points_id": points_id,
        "amount": claim.get("amount"),
        "deposit_contract": deposit_addr,
        "tx_hash": tx_hash,
    }
