from __future__ import annotations

# Minimal ABIs + raw selectors observed from HAR / frontend UserOps.

ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "mint",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
]

# Selectors from live HAR (authoritative if human names differ)
SEL_FILL_PARTIAL = bytes.fromhex("076db91f")  # fillOffer(uint256,uint256)
SEL_FILL_FULL = bytes.fromhex("85dba861")     # fillOffer(uint256)
SEL_CREATE_OFFER = bytes.fromhex("ecbc43e6")  # createOffer(...)

# Common deposit selectors tried at runtime if needed
SEL_DEPOSIT_CANDIDATES = [
    bytes.fromhex("b9f04567"),  # deposit(Claim,bytes) guesses — resolved via registry/probe
]

REGISTRY_ABI = [
    {
        "name": "getProgram",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "pointsId", "type": "uint256"}],
        "outputs": [
            {"name": "deposit", "type": "address"},
            {"name": "escrowToken", "type": "address"},
            {"name": "settlement", "type": "address"},
            {"name": "active", "type": "bool"},
        ],
    },
    {
        "name": "programs",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "pointsId", "type": "uint256"}],
        "outputs": [
            {"name": "deposit", "type": "address"},
            {"name": "escrowToken", "type": "address"},
            {"name": "settlement", "type": "address"},
            {"name": "active", "type": "bool"},
        ],
    },
    {
        "name": "depositOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "pointsId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "address"}],
    },
]

# Deposit claim struct encoding is handled manually from oracle JSON.
