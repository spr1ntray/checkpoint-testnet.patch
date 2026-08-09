# Checkpoint Testnet

Софт для фарма **Checkpoint XP** на **Arbitrum Sepolia** (Soft Hub 0.6.8+).

## Soft Hub

```text
dist/checkpoint-testnet-1.5.0.softhub.zip
```

Repo: https://github.com/spr1ntray/checkpoint-testnet.patch  
Latest: https://github.com/spr1ntray/checkpoint-testnet.patch/releases/latest

### Действия

| Действие | Риск | Назначение |
|----------|------|------------|
| **Работа** | testnet_write | Auto-register по реф-цепи Hub + farm fills |
| **Парсинг** | read | ETH / USDC / XP |

Новые аккаунты регистрируются **внутри Работы** (parent-first).  
Чужой referrer — лог + ignore. Manual invite code не нужен.

### Сборка

```bash
python3 /Users/sprintray/codex_soft/soft-hub/scripts/build_plugin.py \
  hub_package \
  dist/checkpoint-testnet-1.5.0.softhub.zip
```

## CLI (legacy)

```bash
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```
