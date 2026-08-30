from __future__ import annotations

from typing import Any

import requests
from eth_account import Account
from web3 import Web3

from .accounts import AccountConfig
from .config import AppConfig

CHECKPOINT_ORIGIN = "https://checkpoint.exchange"
PORTFOLIO_ADDRESSES_URL = f"{CHECKPOINT_ORIGIN}/api/v1/portfolio/public/addresses"
REWARDS_API = f"{CHECKPOINT_ORIGIN}/api/rewards"


def address_from_account(account: AccountConfig) -> str:
    return Web3.to_checksum_address(Account.from_key(account.private_key).address)


def make_session(proxy: str | None, headers: dict[str, str] | None = None) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Origin": CHECKPOINT_ORIGIN,
            "Referer": f"{CHECKPOINT_ORIGIN}/",
        }
    )
    if headers:
        session.headers.update(headers)
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def register_portfolio_address(
    session: requests.Session,
    cfg: AppConfig,
    address: str,
) -> dict[str, Any]:
    """Index wallet in Checkpoint portfolio (lightweight onboarding)."""
    resp = session.post(
        PORTFOLIO_ADDRESSES_URL,
        json={"evm": [checksum_address(address)]},
        headers={
            "Content-Type": "application/json",
            "Referer": f"{CHECKPOINT_ORIGIN}/",
        },
        timeout=cfg.request_timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"portfolio register HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        return {"success": resp.ok}
    return data if isinstance(data, dict) else {"success": resp.ok}


def submit_referral(
    session: requests.Session,
    cfg: AppConfig,
    *,
    referee: str,
    referrer: str,
) -> dict[str, Any]:
    """
    Link child wallet to parent as Deposit Referral.

    Live HAR:
      POST https://checkpoint.exchange/api/rewards/referrals
      {"referee":"0xChild","referrer":"0xParent"}
      → {"created":true,"status":"pending",...}
    """
    url = f"{cfg.rewards_api.rstrip('/')}/referrals"
    payload = {
        "referee": checksum_address(referee),
        "referrer": checksum_address(referrer),
    }
    resp = session.post(
        url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Referer": f"{CHECKPOINT_ORIGIN}/leaderboard",
        },
        timeout=cfg.request_timeout,
    )
    text = (resp.text or "")[:400]
    if resp.status_code >= 400:
        raise RuntimeError(f"referral HTTP {resp.status_code}: {text}")
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"referral bad json: {text}") from exc
    return data if isinstance(data, dict) else {"raw": data}


def fetch_referral_status(
    session: requests.Session,
    cfg: AppConfig,
    address: str,
) -> dict[str, Any]:
    url = f"{cfg.rewards_api.rstrip('/')}/users/{checksum_address(address)}"
    resp = session.get(
        url,
        headers={"Referer": f"{CHECKPOINT_ORIGIN}/"},
        timeout=cfg.request_timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"rewards HTTP {resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict):
        return {}
    referral = data.get("referral") or {}
    return referral if isinstance(referral, dict) else {}


def checksum_address(address: str) -> str:
    value = (address or "").strip()
    if not value:
        raise ValueError("empty address")
    try:
        return Web3.to_checksum_address(value)
    except Exception:
        return value
