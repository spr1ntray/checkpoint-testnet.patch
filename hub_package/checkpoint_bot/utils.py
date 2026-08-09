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


_PRIVATE_KEY_RE = re.compile(r"(?i)\b(?:0x)?[a-f0-9]{64}\b")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?")
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{16,}")
_PROXY_CREDS_RE = re.compile(r"(//)([^/\s@:\"]+):([^/\s@\"]+)@")
_PROXY_CREDS_BARE_RE = re.compile(
    r"(?i)\b([a-z0-9._%+\-]{2,}):([^@\s/:]{2,})@([a-z0-9.\-]+):(\d{2,5})\b"
)


def scrub_secrets(text: str, extra: Iterable[str | None] = ()) -> str:
    """Best-effort scrub of keys, proxy creds, JWTs from free-form error/log text."""
    result = str(text)
    for secret in extra:
        if not secret:
            continue
        s = str(secret)
        if len(s) >= 6:
            result = result.replace(s, "[REDACTED]")
        # Also scrub common proxy forms derived from the same credential.
        bare = s.replace("http://", "").replace("https://", "")
        if bare != s and len(bare) >= 6:
            result = result.replace(bare, "[REDACTED]")
        if "@" in bare:
            userinfo = bare.split("@", 1)[0]
            if ":" in userinfo and len(userinfo) >= 4:
                result = result.replace(userinfo, "***:***")
    result = _PRIVATE_KEY_RE.sub("[REDACTED_KEY]", result)
    result = _JWT_RE.sub("[REDACTED_JWT]", result)
    result = _BEARER_RE.sub(r"\1[REDACTED]", result)
    result = _PROXY_CREDS_RE.sub(r"\1***:***@", result)
    result = _PROXY_CREDS_BARE_RE.sub(r"***:***@\3:\4", result)
    return result


def redact_text(text: str, secrets: Iterable[str | None]) -> str:
    return scrub_secrets(text, secrets)


def encode_erc7930_account(evm_address: str) -> bytes:
    """ERC-7930 EVM account: 0x0001 || 0x0014 || address20."""
    addr = evm_address.lower().removeprefix("0x")
    if len(addr) != 40:
        raise ValueError(f"bad address: {evm_address}")
    return bytes.fromhex("000100000014" + addr)


def encode_erc7930_account_hex(evm_address: str) -> str:
    return "0x" + encode_erc7930_account(evm_address).hex()
