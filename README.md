# Checkpoint Testnet

Софт для фарма **Checkpoint XP** на **Arbitrum Sepolia** (Soft Hub 0.6.22+).

## Soft Hub

Актуальный пакет: `dist/checkpoint-testnet.softhub.zip` (сейчас **1.7.24**).  
Старые zip в `dist/` не копим — версия живёт в git (ветки + теги `v1.7.24`).

Repo: https://github.com/spr1ntray/checkpoint-testnet.patch

### Действия

| Действие | Риск | Назначение |
|----------|------|------------|
| **Работа** | testnet_write | Кран QuickNode в Ads при нехватке газа (если нет истории txs на Ethereum — маленький свап, ≥ 0.001 ETH остаётся), auto-register по реф-цепи Hub, Kernel fills. Потолок 12 действий/аккаунт/день. |
| **Парсинг** | read | ETH / USDC / XP |

Новые аккаунты регистрируются **внутри Работы** (parent-first).  
Чужой referrer — лог + ignore. Manual invite code не нужен.

Mint test USDC — в том же Kernel UserOp, что и сделка. Если USDC кончились — доминт со следующим fill.

### Сборка

```bash
./scripts/build.sh
```
