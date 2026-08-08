# Checkpoint Testnet

Софт для фарма **Checkpoint XP** на **Arbitrum Sepolia**, упакованный под **Soft Hub 0.6.4**.

## Soft Hub (основной путь)

Готовый пакет:

```text
dist/checkpoint-testnet-1.1.0.softhub.zip
```

### Установка

1. Открой **Soft Hub**  
2. **Патчи** → Local package → выбери `.softhub.zip`  
3. Если `needs_setup` → **Подготовить**  
4. В **Аккаунтах** должны быть `private_key` + `proxy`  
5. На кошельках — ETH (Arbitrum Sepolia) на газ  
6. **Софты** → **Checkpoint Testnet**

### Действия

| Действие | Риск | Назначение |
|----------|------|------------|
| Проверить XP и балансы | read | ETH / test USDC / XP |
| Daily farm | testnet_write | до 5 fills/день |
| Deposit | testnet_write | oracle deposit |
| Полный цикл | testnet_write | deposit? + fills |
| Создать sell offer | testnet_write | sell listing |

### Сборка пакета

```bash
python3 /Users/sprintray/codex_soft/soft-hub/scripts/build_plugin.py \
  hub_package \
  dist/checkpoint-testnet-1.1.0.softhub.zip
```

Исходники пакета: `hub_package/`  
Контракт: `hub_docs/SOFTWARE_SPEC_RU.md` (`SH-SOFTWARE-0.6/2`)

## CLI (legacy / отладка)

```bash
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

CLI остаётся для локальной отладки; **production-путь — Soft Hub**.

## Важно

- XP от fills может обновляться с **лагом 10–30 мин** → смотри **Проверить XP** позже  
- Deposit может skip (oracle/registry)  
- После force-stop write → сверка в chain explorer  
