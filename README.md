# Checkpoint Testnet

Софт для фарма **Checkpoint XP** на **Arbitrum Sepolia**, упакованный под **Soft Hub 0.6.8+**.

## Soft Hub (основной путь)

Готовый пакет (Patch Radar / GitHub Release):

```text
dist/checkpoint-testnet-1.4.0.softhub.zip
```

Repo: https://github.com/spr1ntray/checkpoint-testnet.patch  
Latest release: https://github.com/spr1ntray/checkpoint-testnet.patch/releases/latest

### Установка

1. Открой **Soft Hub**  
2. **Патчи** → Patch Radar (`spr1ntray/checkpoint-testnet.patch`) или Local package  
3. Если `needs_setup` → **Подготовить**  
4. В **Аккаунтах** — `private_key` + `proxy`  
5. Настрой **реферальную топологию** (child → parent)  
6. Для фарма — ETH Arbitrum Sepolia на газ  
7. **Софты** → **Checkpoint Testnet**

### Действия

| Действие | Риск | Назначение |
|----------|------|------------|
| **Регистрация** | external_write | Portfolio + рефералы по топологии Hub (parent-first) |
| **Парсинг** | read | ETH / test USDC / XP |
| **Фарм** | testnet_write | до 5 fills/день |

Реферальный код Checkpoint = **EVM-адрес parent**. Manual invite code не нужен и запрещён контрактом Soft Hub `/3`.

### Сборка пакета

```bash
python3 /Users/sprintray/codex_soft/soft-hub/scripts/build_plugin.py \
  hub_package \
  dist/checkpoint-testnet-1.4.0.softhub.zip
```

Исходники: `hub_package/`  
Контракт: `docs_hub/SOFTWARE_SPEC_RU.md` (`SH-SOFTWARE-0.6/3`)  
Параллельность: `account_concurrency` + `map_accounts`; для регистрации — ещё `referral_levels`.

## CLI (legacy / отладка)

```bash
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

CLI остаётся для локальной отладки; **production-путь — Soft Hub**.

## Важно

- XP от fills может обновляться с **лагом** → смотри **Парсинг** позже  
- После force-stop external write → `needs_attention`, сверь Checkpoint UI  
- Уже привязанный к **другому** referrer → `blocked` (пересадка невозможна)
