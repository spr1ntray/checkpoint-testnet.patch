from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from eth_account import Account


@dataclass(frozen=True)
class AccountConfig:
    label: str
    private_key: str
    proxy: str | None = None


def normalize_proxy(proxy: str | None) -> str | None:
    if not proxy:
        return None
    value = proxy.strip()
    if not value:
        return None
    scheme = "http"
    if "://" in value:
        scheme, value = value.split("://", 1)
    if "@" not in value:
        parts = value.split(":")
        if len(parts) == 4 and parts[1].isdigit():
            host, port, username, password = parts
            username = quote(username, safe="")
            password = quote(password, safe="")
            value = f"{username}:{password}@{host}:{port}"
    if "://" not in value:
        value = f"{scheme}://{value}"
    return value


def normalize_private_key(private_key: str) -> str:
    key = private_key.strip()
    if not key:
        raise ValueError("empty private key")
    if not key.startswith("0x"):
        key = f"0x{key}"
    Account.from_key(key)
    return key


def accounts_from_keys(
    private_keys: list[str],
    proxies: list[str] | None = None,
    *,
    limit: int | None = None,
) -> list[AccountConfig]:
    normalized_proxies = [p for p in (normalize_proxy(proxy) for proxy in (proxies or [])) if p]
    accounts: list[AccountConfig] = []
    for index, private_key in enumerate(private_keys, start=1):
        if limit is not None and len(accounts) >= limit:
            break
        try:
            key = normalize_private_key(private_key)
        except Exception:
            continue
        proxy = normalized_proxies[(index - 1) % len(normalized_proxies)] if normalized_proxies else None
        accounts.append(AccountConfig(label=f"wallet-{index}", private_key=key, proxy=proxy))
    return accounts


def load_accounts(path: str | Path, limit: int | None = None) -> list[AccountConfig]:
    path = Path(path)
    accounts: list[AccountConfig] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"private_key"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("accounts CSV must include a private_key column")

        for index, row in enumerate(reader, start=1):
            if limit is not None and len(accounts) >= limit:
                break
            key = (row.get("private_key") or "").strip()
            if not key or key == "0xYOUR_TESTNET_PRIVATE_KEY":
                continue
            key = normalize_private_key(key)
            label = (row.get("label") or f"wallet-{index}").strip()
            proxy = normalize_proxy(row.get("proxy"))
            accounts.append(AccountConfig(label=label, private_key=key, proxy=proxy))
    return accounts
