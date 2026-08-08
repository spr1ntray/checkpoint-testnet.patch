# Checkpoint XP Farmer Implementation Plan

> **For agentic workers:** Inline execution in this session.

**Goal:** Secure multi-wallet Python CLI that farms Checkpoint testnet XP via SIWE + USDC mint + fillOffer trades.

**Architecture:** EOA-direct on Arbitrum Sepolia. Encrypted secrets (sekai pattern). ThreadPool multi-wallet runner with 1:1 proxies.

**Tech Stack:** Python 3.11+, web3.py, eth-account, cryptography, loguru, requests

## Global Constraints
- Keys only in encrypted DB after import
- parameters.py is the only user knob file
- Smoke-first: 2 wallets, EOA gas
- Max 5 trades/day/wallet for XP

## Tasks
1. Scaffold package + secure store + menu
2. Web3 client, USDC, market API, fill
3. SIWE + rewards + runner actions
4. Deposit + sell modes
5. README + import check
