from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Any


try:
    from loguru import logger as _logger
except Exception:  # pragma: no cover - fallback for minimal environments
    _logger = None


_CONFIGURED = False


def setup_console_logger() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if _logger is None:
        return

    Path("logs").mkdir(parents=True, exist_ok=True)
    _logger.remove()
    _logger.configure(extra={"wallet": "SYSTEM"})
    _logger.add(
        sys.stdout,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[wallet]}</cyan> | "
            "<level>{message}</level>"
        ),
        level="INFO",
        colorize=True,
    )
    _logger.add(
        "logs/checkpoint_console_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[wallet]} | {message}",
        level="DEBUG",
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
    )


def get_logger(wallet: str = "SYSTEM") -> Any:
    setup_console_logger()
    wallet_label = _wallet_label(wallet)
    if _logger is not None:
        return _logger.bind(wallet=wallet_label)
    return _FallbackLogger(wallet_label)


def _wallet_label(wallet: str) -> str:
    if wallet.startswith("0x") and len(wallet) > 12:
        return f"{wallet[:6]}...{wallet[-4:]}"
    return wallet or "SYSTEM"


class _FallbackLogger:
    COLORS = {
        "INFO": "\033[36m",
        "SUCCESS": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "DEBUG": "\033[90m",
    }
    RESET = "\033[0m"

    def __init__(self, wallet: str) -> None:
        self.wallet = wallet

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def success(self, message: str) -> None:
        self._write("SUCCESS", message)

    def warning(self, message: str) -> None:
        self._write("WARNING", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)

    def debug(self, message: str) -> None:
        self._write("DEBUG", message)

    def _write(self, level: str, message: str) -> None:
        color = self.COLORS.get(level, "")
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\033[32m{now}\033[0m | {color}{level:<8}{self.RESET} | \033[36m{self.wallet}\033[0m | {color}{message}{self.RESET}")
