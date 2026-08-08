from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from importlib import import_module


@dataclass(frozen=True)
class AppConfig:
    max_workers: int
    shuffle_wallets: bool
    delay_min: float
    delay_max: float
    gas_multiplier: float
    receipt_timeout: int
    min_eth_balance: Decimal

    rpc_url: str
    rpc_fallbacks: list[str]
    chain_id: int
    rpc_via_proxy: bool

    market: str
    registry: str
    oracle: str
    usdc: str

    market_api: str
    oracle_api: str
    rewards_api: str
    dynamic_env_id: str
    dynamic_api: str
    request_timeout: int

    points_id: int
    trades_per_day: int
    trade_usdc_min: Decimal
    trade_usdc_max: Decimal
    mint_usdc_if_below: Decimal
    mint_usdc_amount: Decimal
    prefer_full_fill: bool

    deposit_enabled: bool
    deposit_points_ids: list[int]

    sell_enabled: bool
    sell_points_amount_min: Decimal
    sell_points_amount_max: Decimal
    sell_price_per_point_min: Decimal
    sell_price_per_point_max: Decimal
    sell_collateral: Decimal
    sell_repeats: int

    siwe_enabled: bool
    siwe_captcha: bool
    hcaptcha_sitekey: str


def load_config() -> AppConfig:
    p = import_module("parameters")
    fallbacks = list(getattr(p, "RPC_FALLBACKS", []) or [])
    return AppConfig(
        max_workers=int(p.MAX_WORKERS),
        shuffle_wallets=bool(p.SHUFFLE_WALLETS),
        delay_min=float(p.DELAY_MIN),
        delay_max=float(p.DELAY_MAX),
        gas_multiplier=float(p.GAS_MULTIPLIER),
        receipt_timeout=int(p.RECEIPT_TIMEOUT),
        min_eth_balance=Decimal(str(p.MIN_ETH_BALANCE)),
        rpc_url=str(p.RPC_URL),
        rpc_fallbacks=[str(x) for x in fallbacks],
        chain_id=int(p.CHAIN_ID),
        rpc_via_proxy=bool(p.RPC_VIA_PROXY),
        market=str(p.MARKET),
        registry=str(p.REGISTRY),
        oracle=str(p.ORACLE),
        usdc=str(p.USDC),
        market_api=str(p.MARKET_API).rstrip("/"),
        oracle_api=str(p.ORACLE_API).rstrip("/"),
        rewards_api=str(p.REWARDS_API).rstrip("/"),
        dynamic_env_id=str(p.DYNAMIC_ENV_ID),
        dynamic_api=str(p.DYNAMIC_API).rstrip("/"),
        request_timeout=int(p.REQUEST_TIMEOUT),
        points_id=int(p.POINTS_ID),
        trades_per_day=int(p.TRADES_PER_DAY),
        trade_usdc_min=Decimal(str(p.TRADE_USDC_MIN)),
        trade_usdc_max=Decimal(str(p.TRADE_USDC_MAX)),
        mint_usdc_if_below=Decimal(str(p.MINT_USDC_IF_BELOW)),
        mint_usdc_amount=Decimal(str(p.MINT_USDC_AMOUNT)),
        prefer_full_fill=bool(p.PREFER_FULL_FILL),
        deposit_enabled=bool(p.DEPOSIT_ENABLED),
        deposit_points_ids=[int(x) for x in p.DEPOSIT_POINTS_IDS],
        sell_enabled=bool(p.SELL_ENABLED),
        sell_points_amount_min=Decimal(str(p.SELL_POINTS_AMOUNT_MIN)),
        sell_points_amount_max=Decimal(str(p.SELL_POINTS_AMOUNT_MAX)),
        sell_price_per_point_min=Decimal(str(p.SELL_PRICE_PER_POINT_MIN)),
        sell_price_per_point_max=Decimal(str(p.SELL_PRICE_PER_POINT_MAX)),
        sell_collateral=Decimal(str(p.SELL_COLLATERAL)),
        sell_repeats=int(p.SELL_REPEATS),
        siwe_enabled=bool(p.SIWE_ENABLED),
        siwe_captcha=bool(getattr(p, "SIWE_CAPTCHA", True)),
        hcaptcha_sitekey=str(getattr(p, "HCAPTCHA_SITEKEY", "14c486da-cd2e-4648-8446-0f469696acee")),
    )
