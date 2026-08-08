# ============================================================
#  CHECKPOINT XP FARMER — НАСТРОЙКИ
#  Меняй только этот файл. Остальное трогать не нужно.
#  Запуск: python main.py
# ============================================================

# ---------- ЗАПУСК ----------
MAX_WORKERS = 2           # параллельных кошельков
SHUFFLE_WALLETS = True
DELAY_MIN = 8             # пауза между действиями, сек
DELAY_MAX = 25
GAS_MULTIPLIER = 1.25
RECEIPT_TIMEOUT = 180
MIN_ETH_BALANCE = "0.0003"

# ---------- СЕТЬ ----------
RPC_URL = "https://arbitrum-sepolia-rpc.publicnode.com"
# Запасные RPC (если 403 / rate-limit)
RPC_FALLBACKS = [
    "https://sepolia-rollup.arbitrum.io/rpc",
    "https://arbitrum-sepolia.gateway.tenderly.co",
    "https://arbitrum-sepolia.public.blastapi.io",
]
CHAIN_ID = 421614
# Public RPC часто режет датацентровые proxy → по умолчанию RPC напрямую
RPC_VIA_PROXY = False

# ---------- КОНТРАКТЫ (Arbitrum Sepolia) ----------
MARKET = "0xf2869aCE6170F7Ab1aba1C55a3483eD7E2f8AaAE"
REGISTRY = "0xC9b2b7138ECF35f980036BbDB466d8e6437B4d9F"
ORACLE = "0x6973c1506a81707F431f8E93D8465a9840e8B458"
USDC = "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773"

# ---------- API ----------
MARKET_API = "https://checkpoint-data-market-api.dorimebest.workers.dev"
ORACLE_API = "https://oracle.checkpoint.exchange"
REWARDS_API = "https://checkpoint.exchange/api/rewards"
DYNAMIC_ENV_ID = "ad89a660-574d-46d0-9a19-3d8834d535c7"
DYNAMIC_API = "https://app.dynamicauth.com/api/v0/sdk"
REQUEST_TIMEOUT = 45

# ---------- FARM ----------
POINTS_ID = 5
TRADES_PER_DAY = 5
TRADE_USDC_MIN = "0.01"
TRADE_USDC_MAX = "2.0"
MINT_USDC_IF_BELOW = "5"
MINT_USDC_AMOUNT = "50"
PREFER_FULL_FILL = True

# ---------- DEPOSIT ----------
# Пока registry — proxy без публичного getter; deposit не блокирует trades
DEPOSIT_ENABLED = False
DEPOSIT_POINTS_IDS = [5]

# ---------- SELL ----------
SELL_ENABLED = False
SELL_POINTS_AMOUNT_MIN = "0.01"
SELL_POINTS_AMOUNT_MAX = "0.10"
SELL_PRICE_PER_POINT_MIN = "50"
SELL_PRICE_PER_POINT_MAX = "120"
SELL_COLLATERAL = "0"
SELL_REPEATS = 1

# ---------- AUTH (SIWE + hCaptcha) ----------
SIWE_ENABLED = True
SIWE_CAPTCHA = True
HCAPTCHA_SITEKEY = "14c486da-cd2e-4648-8446-0f469696acee"
