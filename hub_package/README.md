# Checkpoint Testnet — Soft Hub plugin 1.4.0

Пакет для **Soft Hub 0.6.8+** (`SH-SOFTWARE-0.6/3`).

## Установка

Patch Radar → репозиторий `spr1ntray/checkpoint-testnet.patch` → release **1.4.0**,  
либо Local package:

```text
dist/checkpoint-testnet-1.4.0.softhub.zip
```

Если `needs_setup` → **Подготовить**.

## Перед запуском

1. В **Аккаунтах** импортируй связки `private_key,proxy` (email/twitter не нужны).
2. Настрой **реферальную топологию** Hub: child → direct parent.  
   Project code (EVM-адрес parent) софт берёт сам — manual invite code не вводится.
3. Для **Фарм** — немного **ETH Arbitrum Sepolia** на газ.

## Действия

| Action | Risk | Что делает |
|--------|------|------------|
| **Регистрация** | external_write | Portfolio index + сажает на parent по топологии Hub (уровни parent-first) |
| **Парсинг** | read | ETH / USDC / XP в таблицу |
| **Фарм** | testnet_write | mint test USDC + до 5 market fills / день |

### Регистрация и рефералы

- Код Checkpoint = **EVM-адрес parent** из топологии Soft Hub.
- `context.referral_levels` → сначала roots/уровень 0, потом дети.
- Внутри уровня — `map_accounts` с `account_concurrency`.
- Сразу после получения адреса parent → `protect_secret` (код не в логах/results).
- Roots (`parent_required: false`) только индексируются в portfolio.
- Уже привязанный к правильному parent — success / already_linked.
- Чужой referrer — `blocked` (API не даёт пересадить).

## Сборка

```bash
python3 /path/to/soft-hub/scripts/build_plugin.py \
  hub_package \
  dist/checkpoint-testnet-1.4.0.softhub.zip
```

## Заметки

- XP от fills индексируется с задержкой; смотри **Парсинг** позже.
- SIWE/hCaptcha не блокирует сценарии 1.4.0.
- Force stop после external write → `needs_attention`, сверь Checkpoint UI.
