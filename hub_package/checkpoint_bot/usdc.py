from __future__ import annotations

from decimal import Decimal

from web3 import Web3

from .client import CheckpointClient
from .config import AppConfig


def ensure_usdc(client: CheckpointClient, cfg: AppConfig) -> str | None:
    """Mint USDC if below threshold. Returns tx hash or None if skipped."""
    bal = client.usdc_balance()
    if bal >= cfg.mint_usdc_if_below:
        return None
    amount = client.to_usdc_units(cfg.mint_usdc_amount)
    fn = client.usdc.functions.mint(client.address, amount)
    tx = client.build_contract_tx(fn)
    return client.send_tx(tx)


def approve_market(client: CheckpointClient, cfg: AppConfig, amount_raw: int) -> str | None:
    spender = Web3.to_checksum_address(cfg.market)
    current = client.usdc.functions.allowance(client.address, spender).call()
    if current >= amount_raw:
        return None
    # Approve max-ish to reduce future txs, but not unbounded forever
    approve_amount = max(amount_raw, client.to_usdc_units(cfg.mint_usdc_amount))
    fn = client.usdc.functions.approve(spender, approve_amount)
    tx = client.build_contract_tx(fn)
    return client.send_tx(tx)
