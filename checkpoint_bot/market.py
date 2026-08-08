from __future__ import annotations

from eth_abi import encode
from web3 import Web3

from .abis import SEL_CREATE_OFFER, SEL_FILL_FULL, SEL_FILL_PARTIAL
from .client import CheckpointClient
from .config import AppConfig
from .utils import encode_erc7930_account


def _market_call(client: CheckpointClient, cfg: AppConfig, data: bytes) -> str:
    tx = {
        "to": Web3.to_checksum_address(cfg.market),
        "data": data,
        "value": 0,
    }
    return client.send_tx(tx)


def fill_offer_full(client: CheckpointClient, cfg: AppConfig, offer_id: int) -> str:
    data = SEL_FILL_FULL + encode(["uint256"], [int(offer_id)])
    return _market_call(client, cfg, data)


def fill_offer_partial(client: CheckpointClient, cfg: AppConfig, offer_id: int, usdc_amount_raw: int) -> str:
    data = SEL_FILL_PARTIAL + encode(["uint256", "uint256"], [int(offer_id), int(usdc_amount_raw)])
    return _market_call(client, cfg, data)


def create_offer(
    client: CheckpointClient,
    cfg: AppConfig,
    *,
    points_id: int,
    points_amount_raw: int,
    price_raw: int,
    collateral_raw: int = 0,
) -> str:
    account = encode_erc7930_account(client.address)
    # Observed HAR encoding: pointsId, pointsAmount, price, collateral, bytes account
    data = SEL_CREATE_OFFER + encode(
        ["uint256", "uint256", "uint256", "uint256", "bytes"],
        [int(points_id), int(points_amount_raw), int(price_raw), int(collateral_raw), account],
    )
    return _market_call(client, cfg, data)
