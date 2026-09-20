# Checkpoint Testnet — Soft Hub plugin 1.7.20

Пакет для **Soft Hub 0.6.22+** (`SH-SOFTWARE-0.6/5`).

## Установка

Актуальный пакет (единственный на диске): `dist/checkpoint-testnet.softhub.zip`  
Версия внутри манифеста: **1.7.20** (`dist/checkpoint-testnet-1.7.20.softhub.zip` — копия того же файла).

## Перед запуском

1. AdsPower запущен, Local API включён, у каждого аккаунта свой profile ID, API key в настройках Hub
2. Proxy в Hub-аккаунте
3. Топология рефералов Hub (child → parent)
4. На **Работе**: если мало Sepolia ETH — Ads открывает [QuickNode drip](https://faucet.quicknode.com/drip), вставляет адрес, выбирает Arbitrum/Sepolia, Continue, затем **Send to** на 0.05 ETH. Invisible reCAPTCHA уходит сама; картинка — только если Google кинет challenge, тогда решаешь в окне профиля. Chrome после крана закрывается, фарм идёт по HTTP
5. QuickNode: 1 drip / сеть / 12 часов; часто просит ≥ 0.001 ETH в Ethereum mainnet на том же адресе

Buy XP Checkpoint считает только после Kernel UserOps, не после EOA fill.

Ордербук: живые офферы — `status` 0 и 2 (частично залитые). Если на рынке пусто — ждём **5 минут**, затем следующий маркет. Сначала Checkpoint XP (`pointsId` 5), потом самые жирные книги.

Mint test USDC идёт **в том же Kernel UserOp, что и сделка**. Перед фармом софт кидает бюджет **420–500 USDC** и число fills в диапазоне настроек (5–10), затем делит бюджет на сделки. Ставку (USDC на fill) пользователь не задаёт.

Кран QuickNode: если на Ethereum mainnet меньше 0.001 ETH — **ошибка**, Ads/QuickNode не открываем. Иначе ждёт reCAPTCHA и не считает скрытый текст «12 hours» за отказ.

## Действия

| Action | Risk | Что делает |
|--------|------|------------|
| **Работа** | testnet_write | При нехватке газа — кран QuickNode в Ads, затем auto-register по реф-цепи + Kernel fills |
| **Парсинг** | read | ETH / USDC / XP в таблицу |

Кран открывается **только если** на кошельке мало Sepolia ETH. Если ETH уже есть — Ads Chrome не стартует.

Параллельность: слайдер до **20** (потолок Soft Hub). Hub сам зажимает по числу выбранных аккаунтов. 30 сразу нельзя — это лимит Hub, не софта. Реф-уровни по-прежнему parent-first: дети ждут родителей.

### Работа и рефералы

- Отдельного режима «Регистрация» **нет**.
- При **Работе** софт сам:
  1. проверяет Ads-профиль,
  2. если мало газа — кран QuickNode в этом профиле,
  3. индексирует portfolio,
  4. если parent в топологии и реферала ещё нет — сажает на EVM-адрес parent,
  5. фармит Kernel fills через ZeroDev.
- Уровни `referral_levels` — родители раньше детей.
- Уже на **чужом** referrer → warning в лог, фарм **продолжается**.
- Manual invite code не нужен.

0 Sepolia ETH после крана → аккаунт **blocked**, не failed.

## Сборка

История версий — **git** (ветки + теги), не стопка zip. Скрипт пересобирает только актуальную версию и удаляет старые zip в `dist/`:

```bash
./scripts/build.sh
```

Эквивалент вручную:

```bash
python3 /path/to/soft-hub/scripts/build_plugin.py \
  hub_package \
  dist/checkpoint-testnet.softhub.zip
```

### Git (кратко)

| Что | Зачем |
|-----|--------|
| ветка `main` | то, что можно ставить |
| ветка `fix/...` / `feat/...` | одна задача, потом merge в `main` |
| тег `v1.7.20` | номер релиза = версия в `hub.plugin.json` |
| `dist/*.zip` в `.gitignore` | артефакт собирается, в git не копится |

Постоянные ветки «на каждый модуль навсегда» **не нужны**: модули уже файлы (`faucet.py`, `kernel_aa.py`, `actions.py`). Ветка живёт, пока чинишь mint/кран, потом вливается в `main` и удаляется.

## Визуал

Icon/image — официальные ассеты Checkpoint (`@CheckpointEX` / checkpoint.exchange).
