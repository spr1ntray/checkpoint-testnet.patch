# Checkpoint XP Farmer — Design Spec

**Date:** 2026-07-31  
**Status:** Approved approach (smoke on 2 wallets, EOA-direct)  
**Owner:** Checkpoint_testnet product  
**Goal:** Maximize Checkpoint testnet XP per wallet with a secure multi-account Python bot.

---

## 1. Problem

Checkpoint Exchange testnet rewards real participation with XP. Manual multi-wallet farming does not scale. We need a CLI bot that:

1. Safely stores private keys and proxies
2. Onboards wallets (SIWE / connect attribution)
3. Deposits points when available
4. Executes daily trades up to the XP daily cap
5. Reports XP deltas per wallet
6. Scales from 2 wallets (smoke) to N wallets with 1:1 proxies

Reference UX/security: `sekai_testnet_clean/`.

---

## 2. XP Economy (source of truth)

| Action | XP | Cap | Notes |
|--------|-----|-----|--------|
| Connect wallet (SIWE) | +10 | once | Dynamic verify / first session |
| Deposit points | +100 | once per program path (leaderboard shows stacked deposits up to 500) | Oracle claim + on-chain deposit |
| Trade (buy or sell fill) | +5 | **max 5 trades / day** | Primary recurring farm = **+25 XP/day** |
| Referral commission | variable | ongoing | Optional later (cross-wallet refs) |
| X / Farcaster / milestones | variable | gated | Out of scope v1 |

**Primary optimization target v1:** reliably hit **5 minimal trades/day/wallet**, plus one-time connect + deposit.

Live check API:

```http
GET https://checkpoint.exchange/api/rewards/users/{evmAddress}
```

Response fields used: `totalPoints`, `pointsBreakdown` (`depositPoints`, `buyPoints`, `sellPoints`, …), `rank`.

---

## 3. Environment & Contracts

| Item | Value |
|------|--------|
| Chain | Arbitrum Sepolia |
| Chain ID | `421614` |
| Market API | `https://checkpoint-data-market-api.dorimebest.workers.dev` |
| Oracle API | `https://oracle.checkpoint.exchange` |
| Rewards API | `https://checkpoint.exchange/api/rewards` |
| Dynamic env | `ad89a660-574d-46d0-9a19-3d8834d535c7` |
| Market | `0xf2869aCE6170F7Ab1aba1C55a3483eD7E2f8AaAE` |
| Registry | `0xC9b2b7138ECF35f980036BbDB466d8e6437B4d9F` |
| Oracle | `0x6973c1506a81707F431f8E93D8465a9840e8B458` |
| Test USDC (mintable) | `0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773` |
| Default RPC | public Arbitrum Sepolia RPC (overridable in parameters) |

**Points program IDs (from oracle config):**

| ID | Adapter |
|----|---------|
| 1 | jumperexchange |
| 2 | debridge |
| 3 | debridge |
| 4 | galxe |
| 5 | **checkpoint** (default farm market) |
| 6 | blockscout |
| 7 | cap |

---

## 4. Architecture Decision: EOA-direct (v1)

### Decision

Use **EOA private keys** for:

1. SIWE signature (Dynamic connect/verify) so XP attributes to the EOA
2. On-chain txs: mint USDC → approve → `fillOffer` / `createOffer` / deposit

### Why not ZeroDev AA in v1

UI uses Kernel smart accounts + paymaster (gasless). That path depends on Dynamic WaaS/MPC and is harder to automate safely with only private keys. Gas on Arbitrum Sepolia is negligible; operator will fund ~0.001–0.01 ETH per wallet.

### Risk & mitigation

| Risk | Mitigation |
|------|------------|
| XP only counts smart-wallet `msg.sender` | Smoke on 2 wallets; if XP does not move after confirmed fills, implement Kernel AA (v1.1) |
| SIWE required for trade XP | Always run SIWE before first trade session; persist JWT only in memory |
| Offer already filled / race | Retry next cheapest offer; never hard-fail the wallet cycle |
| Public RPC rate limits | Per-wallet proxy for HTTP APIs; optional private RPC URL |

---

## 5. Product Surface

### Interactive menu (Sekai-style)

```
CHECKPOINT XP FARMER
  > Полный цикл (SIWE + deposit if needed + daily trades)
    Daily farm (5 trades)
    Deposit only
    Create sell offers
    Parse XP / balances
    Создать/обновить зашифрованную базу
```

Arrow keys + Enter; numeric fallback.

### User knobs — `parameters.py` only

```python
MAX_WORKERS = 2
SHUFFLE_WALLETS = True
DELAY_MIN = 8
DELAY_MAX = 25

# Network
RPC_URL = "https://arbitrum-sepolia-rpc.publicnode.com"
CHAIN_ID = 421614
GAS_MULTIPLIER = 1.2
MIN_ETH_BALANCE = "0.0005"

# Farm
POINTS_ID = 5                    # Checkpoint XP market
TRADES_PER_DAY = 5
TRADE_USDC_MIN = "0.05"          # prefer cheapest notional >= this if needed
TRADE_USDC_MAX = "1.0"           # skip expensive fills
MINT_USDC_AMOUNT = "50"          # mint if balance low
USDC_RESERVE = "5"               # keep some USDC spare

# Deposit
DEPOSIT_ENABLED = True
DEPOSIT_POINTS_IDS = [5]         # try Checkpoint first

# Sell (optional path)
SELL_ENABLED = False
SELL_POINTS_AMOUNT = ("0.01", "0.1")
SELL_PRICE_USD = ("50", "120")   # total price band for small listings
SELL_COLLATERAL = "0"

# Auth
SIWE_ENABLED = True
DYNAMIC_ENV_ID = "ad89a660-574d-46d0-9a19-3d8834d535c7"
```

---

## 6. Components

```
checkpoint_farmer/                 # project root package name: checkpoint_bot
  main.py                          # banner, menu, create_db, dispatch modes
  parameters.py                    # user knobs only
  requirements.txt
  README.md
  input/
    private_keys.txt
    proxies.txt
    capsolver_api_key.txt          # reserved; optional
    README.txt
    database.enc / database.salt   # created at runtime
  logs/
  checkpoint_bot/
    __init__.py
    secure_store.py                # PBKDF2 + Fernet (from sekai pattern)
    accounts.py                    # key/proxy pairing, proxy normalize
    interactive.py                 # arrow menu
    console.py                     # loguru colored console
    logger.py                      # jsonl per run
    config.py                      # dataclasses from parameters.py
    client.py                      # web3 + requests session via proxy
    auth_siwe.py                   # Dynamic nonce → SIWE → verify JWT
    rewards.py                     # rewards API parse + XP delta
    market_api.py                  # list offers, activity
    usdc.py                        # mint / balance / approve
    market.py                      # fillOffer, createOffer, cancel (if needed)
    deposit.py                     # oracle auth + claim + on-chain deposit
    actions.py                     # per-wallet action plan
    runner.py                      # ThreadPoolExecutor multi-wallet
    abis.py                        # minimal ABIs / selectors
    utils.py                       # short address, delays, redact
```

### Data flow (daily farm)

```
unlock DB → accounts (key+proxy)
     ↓
for each wallet (parallel workers):
  SIWE if needed
  read rewards (baseline XP)
  ensure ETH min
  ensure USDC (mint if low)
  loop up to TRADES_PER_DAY:
    fetch offers for POINTS_ID
    pick cheapest eligible notional
    approve + fill
    delay random
  read rewards (delta) → log
```

---

## 7. On-chain Call Specs (from HAR reverse-engineering)

### ERC-7930 account encoding

```
bytes account = 0x000100000014 || <20-byte EVM address>
# prefix 0x0001 + length 0x000014 + address
```

Used in createOffer / deposit claim payloads.

### USDC

```solidity
function mint(address to, uint256 amount);   // selector 0x40c10f19
function approve(address spender, uint256 amount);
function balanceOf(address) view returns (uint256);
// decimals: treat as 6 (standard USDC); confirm on smoke
```

### Market fills (observed)

```
// Partial fill by USDC amount (6-decimal units matching approve)
// selector 0x076db91f
fillOffer(uint256 offerId, uint256 usdcAmount)

// Full remaining fill
// selector 0x85dba861
fillOffer(uint256 offerId)
```

Exact ABI names may differ; selectors are authoritative. During implementation, wrap with eth_abi encoding and verify one successful fill on Arbiscan Sepolia.

### Create offer (observed)

```
// selector 0xecbc43e6
// ABI shape (decoded from UserOp):
createOffer(
  uint256 pointsId,
  uint256 pointsAmount,   // 18 decimals style (1e18 = 1 point)
  uint256 price,          // total USDC for full offer, 6 decimals
  uint256 collateralAmount,
  bytes account           // ERC-7930
)
```

### Deposit (Oracle API + on-chain)

1. `POST https://oracle.checkpoint.exchange/claim/deposit/authorization`  
   body: `{ pointsId, account, operator }`  
2. Sign EIP-712 typed data from response with EOA  
3. `POST https://oracle.checkpoint.exchange/claim/deposit` with signature  
4. Call Deposit contract with claim + oracle signature  

Deposit contract address resolved from Registry via `pointsId` at runtime (do not hardcode only one deposit address). Registry: `0xC9b2b713…`.

If deposit path is blocked (0 points available / already deposited), log skip and continue to trades.

---

## 8. Off-chain APIs

### Market offers

```http
GET /market/{pointsId}/offers?limit=500
```

Filter: `status == 0` (open), not fully filled, `price` within `TRADE_USDC_MAX`, prefer lowest `price` (notional).

Price units in API: integer string (e.g. `"120000"` ≈ $0.12 at 6 decimals). Cross-check with on-chain fill amounts during smoke.

### Dynamic SIWE

1. `GET .../nonce`
2. Build SIWE message for `checkpoint.exchange` (same statement as frontend)
3. `personal_sign` with EOA
4. `POST .../verify` → JWT  
5. JWT kept **in memory only** for the run (not written to disk)

### Rewards

```http
GET https://checkpoint.exchange/api/rewards/users/{address}
```

No auth required for read (observed). Use for parse mode and post-run XP delta.

---

## 9. Security Model

Copied and adapted from sekai `secure_store.py`:

1. User fills `input/private_keys.txt` + `input/proxies.txt` (1:1 order)
2. Menu → **Create encrypted DB** → password (PBKDF2-HMAC-SHA256, 480_000 iterations, random salt) → Fernet encrypt JSON bundle
3. Wipe plaintext key/proxy files to templates
4. File modes `0600` / dir `0700`
5. Runtime: password prompt → decrypt to memory → never log private keys or proxy credentials
6. Redact secrets in exception strings
7. `.gitignore`: `input/database.*`, `input/*.txt` except README, `logs/*`, `.env*`

Capsolver key file is reserved for future captcha needs; not required for v1 core path.

---

## 10. Logging

Console (loguru):

```
HH:mm:ss | INFO     | wallet-1 0xA8b0…E949 | Fill offer #133488 for 0.12 USDC
HH:mm:ss | SUCCESS  | wallet-1 0xA8b0…E949 | tx 0xabc… | XP 215 → 220 (+5)
```

JSONL per run: `logs/checkpoint_{run_id}.jsonl` with fields:

```json
{
  "ts": "...",
  "label": "wallet-1",
  "address": "0x...",
  "action": "fill|mint_usdc|siwe|deposit|xp_report",
  "status": "ok|skipped|failed",
  "tx_hash": "0x..." | null,
  "details": {}
}
```

---

## 11. Modes

| Mode | Behavior |
|------|----------|
| `full` | SIWE → deposit attempt → daily trades → XP report |
| `daily` | SIWE if needed → trades only → XP report |
| `deposit` | SIWE → deposit only |
| `sell` | SIWE → create small sell offers (params) |
| `parse` | no txs; balances + rewards for all wallets |
| `create_db` | encrypt secrets |

Smoke checklist (2 wallets):

1. Create DB from 2 keys + 2 proxies  
2. Parse mode shows addresses + current XP  
3. Full cycle on wallet-1: mint USDC, 1 fill, XP +5  
4. Daily mode completes 5 fills (or fewer if market thin)  
5. No secrets in logs or leftover plaintext files  

---

## 12. Explicit Non-Goals (v1)

- ZeroDev / Dynamic WaaS smart accounts  
- Galxe / Discord automation  
- X verification & milestones  
- Referral graph automation (document as v1.1+)  
- Mainnet / real USDC  
- AdsPower browser assist  
- Capsolver unless captcha appears on SIWE/oracle  

---

## 13. Implementation Order

1. Scaffold project (main, parameters, secure_store, interactive, console, accounts)  
2. Client + USDC mint/approve/balance  
3. Market API + fillOffer encoding  
4. SIWE auth  
5. Rewards parse + XP delta  
6. Runner daily loop  
7. Deposit oracle path  
8. Create offer (sell mode)  
9. Smoke on 2 wallets; fix selectors/decimals if needed  
10. README  

---

## 14. Success Criteria

1. **Smoke:** 2 wallets complete at least 1 confirmed fill each; rewards API shows XP increase  
2. **Daily cap:** bot stops at `TRADES_PER_DAY` and reports XP  
3. **Security:** after DB create, plaintext keys gone; wrong password fails closed  
4. **Isolation:** each account uses its assigned proxy for HTTP; RPC may use same proxy if configured  
5. **Operability:** all day-to-day knobs only in `parameters.py`  

---

## 15. Open Items Resolved at Smoke Time

These are intentionally left as “confirm on first live run” rather than blocking design:

- USDC `decimals()` value (assume 6)  
- Exact human-readable names for selectors `076db91f` / `85dba861` / `ecbc43e6`  
- Whether buy XP requires SIWE JWT server-side or only on-chain fill attribution  
- Deposit contract address per `pointsId` via Registry  

If smoke shows fills succeed but XP does not increase, escalate to **v1.1 Kernel AA** without changing the menu/security shell.

---

## 16. Rollout Plan

| Phase | Scope |
|-------|--------|
| Phase 0 | Spec + plan (this doc) |
| Phase 1 | Build + smoke 2 wallets |
| Phase 2 | Stabilize daily farm; optional sell |
| Phase 3 | Scale workers / wallet count; optional referrals |

Operator provides: EVM private keys, HTTP proxies (`ip:port:user:pass`), optional Capsolver key later.
