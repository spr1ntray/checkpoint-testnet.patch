from __future__ import annotations

from datetime import datetime, timezone
import secrets

from eth_account.messages import encode_defunct

from .capsolver import normalize_api_key, solve_hcaptcha
from .client import CheckpointClient
from .config import AppConfig

# Exact statement from live HAR (must match Dynamic verify)
SIWE_STATEMENT = (
    "By signing this message, you confirm that you control this wallet and agree to the "
    "Checkpoint Exchange Terms of Use and Privacy Policy. This signature is used only for "
    "authentication and does not authorize any transaction or transfer of assets."
)

HCAPTCHA_SITEKEY = "14c486da-cd2e-4648-8446-0f469696acee"
PAGE_URL = "https://checkpoint.exchange/"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_siwe_message(
    *,
    address: str,
    nonce: str,
    chain_id: int,
    env_id: str,
    domain: str = "checkpoint.exchange",
) -> str:
    # Live frontend includes trailing slash on URI + Request ID = Dynamic env id
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        f"{SIWE_STATEMENT}\n\n"
        f"URI: https://{domain}/\n"
        f"Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {_iso_now()}\n"
        f"Request ID: {env_id}"
    )


def siwe_login(client: CheckpointClient, cfg: AppConfig, capsolver_api_key: str = "") -> str:
    """Connect + hCaptcha + verify via Dynamic. Returns JWT. Stores on client.jwt."""
    base = f"{cfg.dynamic_api}/{cfg.dynamic_env_id}"
    headers = {
        "Origin": "https://checkpoint.exchange",
        "Referer": "https://checkpoint.exchange/",
        "Content-Type": "application/json",
    }

    client.http_post(
        f"{base}/connect",
        json={
            "address": client.address,
            "chain": "EVM",
            "provider": "browserExtension",
            "walletName": "rabby",
            "authMode": "connect-and-sign",
        },
        headers=headers,
    )

    nonce_resp = client.http_get(f"{base}/nonce", headers=headers)
    nonce_resp.raise_for_status()
    nonce = (nonce_resp.json() or {}).get("nonce") or secrets.token_hex(16)

    message = build_siwe_message(
        address=client.address,
        nonce=nonce,
        chain_id=cfg.chain_id,
        env_id=cfg.dynamic_env_id,
    )
    signed = client.account.sign_message(encode_defunct(text=message))
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature

    captcha_token = ""
    if cfg.siwe_captcha:
        captcha_token = solve_hcaptcha(
            normalize_api_key(capsolver_api_key),
            site_key=cfg.hcaptcha_sitekey or HCAPTCHA_SITEKEY,
            page_url=PAGE_URL,
            proxy=None,
            is_enterprise=True,
        )

    body = {
        "signedMessage": signature,
        "messageToSign": message,
        "publicWalletAddress": client.address,
        "chain": "EVM",
        "walletName": "rabby",
        "walletProvider": "browserExtension",
        "captchaToken": captcha_token,
    }
    verify = client.http_post(f"{base}/verify", json=body, headers=headers)
    if verify.status_code >= 400:
        detail = verify.text[:400]
        raise RuntimeError(f"SIWE verify {verify.status_code}: {detail}")
    data = verify.json()
    jwt = data.get("jwt") or data.get("token") or ""
    if not jwt:
        raise RuntimeError(f"SIWE verify ok but no jwt: {str(data)[:200]}")
    client.jwt = jwt
    return jwt
