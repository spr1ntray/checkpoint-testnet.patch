# Checkpoint Testnet — Soft Hub plugin 1.5.0

Пакет для **Soft Hub 0.6.8+** (`SH-SOFTWARE-0.6/3`).

## Установка

Patch Radar → `spr1ntray/checkpoint-testnet.patch` → **1.5.0**,  
либо Local package: `dist/checkpoint-testnet-1.5.0.softhub.zip`

## Перед запуском

1. Аккаунты: `private_key` + `proxy`
2. Топология рефералов Hub (child → parent)
3. Для **Работы** — ETH Arbitrum Sepolia на газ

## Действия

| Action | Risk | Что делает |
|--------|------|------------|
| **Работа** | testnet_write | Auto-register новых по реф-цепи (parent-first) + mint USDC + fills |
| **Парсинг** | read | ETH / USDC / XP в таблицу |

### Работа и рефералы

- Отдельного режима «Регистрация» **нет**.
- При **Работе** софт сам:
  1. индексирует portfolio,
  2. если parent в топологии и реферала ещё нет — сажает на EVM-адрес parent,
  3. фармит fills.
- Уровни `referral_levels` — родители раньше детей.
- Уже на **чужом** referrer → warning в лог, фарм **продолжается**.
- Manual invite code не нужен.

## Сборка

```bash
python3 /path/to/soft-hub/scripts/build_plugin.py \
  hub_package \
  dist/checkpoint-testnet-1.5.0.softhub.zip
```

## Визуал

Icon/image — официальные ассеты Checkpoint (`@CheckpointEX` / checkpoint.exchange).
