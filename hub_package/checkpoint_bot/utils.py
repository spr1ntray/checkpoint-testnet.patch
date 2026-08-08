from __future__ import annotations

import random
import re
import time
from typing import Iterable


def short_address(value: str | None) -> str:
    if not value:
        return "-"
    text = str(value)
    if text.startswith("0x") and len(text) > 12:
        return f"{text[:6]}...{text[-4:]}"
    return text


def short_hash(value: str | None) -> str:
    if not value:
        return "-"
    text = str(value)
    if text.startswith("0x") and len(text) > 14:
        return f"{text[:10]}...{text[-6:]}"
    return text


def sleep_range(min_s: float, max_s: float) -> float:
    delay = random.uniform(float(min_s), float(max_s))
    time.sleep(delay)
    return delay


def redact_text(text: str, secrets: Iterable[str | None]) -> str:
    result = str(text)
    for secret in secrets:
        if not secret:
            continue
        s = str(secret)
        if len(s) >= 8:
            result = result.replace(s, "***")
        # also redact user:pass inside proxy urls
        result = re.sub(r"(//)([^/@:]+):([^/@]+)@", r"\1***:***@", result)
    return result


def encode_erc7930_account(evm_address: str) -> bytes:
    """ERC-7930 EVM account: 0x0001 || 0x0014 || address20."""
    addr = evm_address.lower().removeprefix("0x")
    if len(addr) != 40:
        raise ValueError(f"bad address: {evm_address}")
    return bytes.fromhex("000100000014" + addr)


def encode_erc7930_account_hex(evm_address: str) -> str:
    return "0x" + encode_erc7930_account(evm_address).hex()
