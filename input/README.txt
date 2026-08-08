Заполни готовые файлы (не создавай новые):

  private_keys.txt       — приватные ключи EVM, 1 на строку
  proxies.txt            — прокси http, 1 на строку: ip:port:user:pass
  capsolver_api_key.txt  — Capsolver API key (НУЖЕН для SIWE/hCaptcha)

Порядок: 1-й ключ ↔ 1-й прокси, 2-й ↔ 2-й, ...

Потом: python main.py → «Создать/обновить зашифрованную базу»

После импорта plaintext-файлы очищаются. Нужен пароль базы при каждом запуске.

Перед фармом:
  - немного ETH на Arbitrum Sepolia на каждый кошелёк (газ)
  - Capsolver key в базе (иначе SIWE failed → XP может не капать)

