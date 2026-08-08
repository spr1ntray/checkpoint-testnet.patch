# Checkpoint Testnet — Soft Hub plugin 1.1.0

Пакет для **Soft Hub 0.6.4+** (`SH-SOFTWARE-0.6/2`).

## Установка

1. Soft Hub → **Патчи** → Local package  
2. Выбери файл:

```text
dist/checkpoint-testnet-1.1.0.softhub.zip
```

3. Если `needs_setup` → **Подготовить**  
4. **Софты** → Checkpoint Testnet  

## Перед запуском

В **Аккаунтах** импортируй связки `private_key,proxy,email,twitter,adspower_profile`  
(для этого модуля реально нужны **private_key + proxy**).

На каждом кошельке — немного **ETH Arbitrum Sepolia** (газ).

## Действия

| Action | Risk | Что делает |
|--------|------|------------|
| Проверить XP и балансы | read | ETH / USDC / XP |
| Daily farm | testnet_write | mint USDC + до 5 fills |
| Deposit | testnet_write | oracle deposit attempt |
| Полный цикл | testnet_write | optional deposit + fills |
| Создать sell offer | testnet_write | 1 sell listing |

## Сборка заново

```bash
python3 /path/to/soft-hub/scripts/build_plugin.py \
  hub_package \
  dist/checkpoint-testnet-1.1.0.softhub.zip
```

## Заметки

- XP от fills индексируется с задержкой (минуты); смотри inspect позже.  
- SIWE/hCaptcha в 1.1.0 не блокирует preflight (Capsolver не required).  
- Deposit может skip, если registry/points недоступны.  
- Force stop после tx → сверь chain, статус `needs_attention`.
