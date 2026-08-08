from __future__ import annotations

from getpass import getpass

from checkpoint_bot.accounts import accounts_from_keys
from checkpoint_bot.config import load_config
from checkpoint_bot.interactive import choose, confirm
from checkpoint_bot.runner import run_accounts
from checkpoint_bot.secure_store import (
    CAPSOLVER_KEY_FILE,
    PRIVATE_KEYS_FILE,
    PROXIES_FILE,
    clear_plaintext_inputs,
    database_exists,
    ensure_input_files,
    read_input_lines,
    save_database,
    unlock_database,
)

BANNER = r"""
        _.---.._             _.---...__
     .-'   /\   \          .'  /\     /
     `.   (  )   \        /   (  )   /
       `.  \/   .'\      /`.   \/  .'
         ``---''   )    (   ``---''
                 .';.--.;`.
               .' /_...._\ `.
             .'   `.a  a.'   `.
            (        \/        )
             `.___..-'`-..___.`
                \          /
 BY SPRINTRAY    `-.____.-'    CHECKPOINT XP
"""


def print_banner() -> None:
    print(BANNER)


def main() -> int:
    print_banner()
    ensure_input_files()
    selected = show_menu()

    if selected.get("action") == "create_db":
        create_database()
        return 0

    if not database_exists():
        print("  База не найдена. Сначала выбери 'Создать/обновить зашифрованную базу'.")
        return 1

    cfg = load_config()
    mode = selected.get("mode", "full")

    secrets = unlock_database()
    accounts = accounts_from_keys(secrets.private_keys, secrets.proxies)
    if not accounts:
        print("  В базе нет валидных кошельков.")
        return 1

    print(f"  Режим: {mode}")
    print(f"  Кошельков: {len(accounts)} | потоков: {cfg.max_workers} | market pointsId={cfg.points_id}")
    if mode != "parse":
        print(f"  Trades/day: {cfg.trades_per_day} | USDC band: {cfg.trade_usdc_min}-{cfg.trade_usdc_max}")
        print(f"  SIWE captcha: {'ON' if cfg.siwe_captcha else 'OFF'} | Capsolver: {'есть' if secrets.capsolver_api_key else 'НЕТ'}")
        print(f"  RPC via proxy: {cfg.rpc_via_proxy}")
    return run_accounts(
        cfg,
        accounts,
        mode=mode,
        capsolver_api_key=secrets.capsolver_api_key,
    )


def show_menu() -> dict:
    return choose(
        "CHECKPOINT XP FARMER",
        [
            ("Полный цикл (SIWE + deposit + daily trades)", {"mode": "full"}),
            ("Daily farm (5 trades)", {"mode": "daily"}),
            ("Deposit only", {"mode": "deposit"}),
            ("Create sell offers", {"mode": "sell"}),
            ("Parse XP / balances", {"mode": "parse"}),
            ("Создать/обновить зашифрованную базу", {"action": "create_db"}),
        ],
    )


def create_database() -> None:
    keys = read_input_lines(PRIVATE_KEYS_FILE)
    proxies = read_input_lines(PROXIES_FILE)
    cap_keys = read_input_lines(CAPSOLVER_KEY_FILE)
    capsolver = cap_keys[0] if cap_keys else ""

    print("=" * 52)
    print("  СОЗДАНИЕ ЗАШИФРОВАННОЙ БАЗЫ")
    print("=" * 52)
    print(f"  Найдено ключей: {len(keys)}")
    print(f"  Найдено прокси: {len(proxies)}")
    print(f"  Capsolver: {'найден' if capsolver else 'нет'}")

    if not keys and database_exists():
        print("  private_keys.txt пустой, беру ключи из текущей базы.")
        existing = unlock_database()
        keys = existing.private_keys
        if not proxies:
            proxies = existing.proxies
        if not capsolver:
            capsolver = existing.capsolver_api_key

    if not keys:
        print(f"  Добавь приватные ключи в {PRIVATE_KEYS_FILE} и повтори.")
        return

    if len(proxies) and len(proxies) < len(keys):
        print(
            f"  Внимание: прокси ({len(proxies)}) < ключей ({len(keys)}). "
            "Прокси будут зациклены по модулю."
        )

    if database_exists() and not confirm("База уже существует. Перезаписать?", default=False):
        print("  Отмена.")
        return

    while True:
        password = getpass("  Придумай пароль: ")
        password2 = getpass("  Повтори пароль:  ")
        if not password:
            print("  Пароль не может быть пустым.")
            continue
        if password != password2:
            print("  Пароли не совпадают.")
            continue
        break

    capsolver = (capsolver or "").strip().strip('"').strip("'")
    if capsolver:
        print(f"  Capsolver key fingerprint: len={len(capsolver)} {capsolver[:4]}…{capsolver[-4:]}")
        try:
            from checkpoint_bot.capsolver import get_balance

            bal = get_balance(capsolver)
            print(f"  Capsolver balance check: OK ({bal})")
        except Exception as exc:
            print(f"  ⚠ Capsolver balance check FAILED: {exc}")
            print("  Базу всё равно сохраню — но SIWE не заработает, пока key неверный.")
            if not confirm("Сохранить базу с этим Capsolver key?", default=False):
                print("  Отмена.")
                return

    save_database(password, keys, proxies, capsolver)
    clear_plaintext_inputs()
    print("  База создана: input/database.enc")
    print("  Открытые файлы с ключами/прокси очищены.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Прервано пользователем.")
