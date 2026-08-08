from __future__ import annotations

import sys


def read_key() -> str:
    if sys.platform == "win32":
        import msvcrt

        char = msvcrt.getch()
        if char in (b"\xe0", b"\x00"):
            second = msvcrt.getch()
            if second == b"H":
                return "up"
            if second == b"P":
                return "down"
            return "esc"
        if char in (b"\r", b"\n"):
            return "enter"
        if char == b"\x03":
            raise KeyboardInterrupt
        if char == b"q":
            return "q"
        return char.decode("utf-8", errors="ignore")

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.buffer.read(1)
        if char == b"\x1b":
            second = sys.stdin.buffer.read(1)
            third = sys.stdin.buffer.read(1)
            if second == b"[":
                if third == b"A":
                    return "up"
                if third == b"B":
                    return "down"
            return "esc"
        if char in (b"\r", b"\n"):
            return "enter"
        if char == b"\x03":
            raise KeyboardInterrupt
        if char == b"q":
            return "q"
        return char.decode("utf-8", errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def choose(title: str, options: list[tuple[str, dict]]) -> dict:
    labels = [label for label, _ in options]
    selected = 0

    print()
    print("=" * 52)
    print(f"  {title}")
    print("=" * 52)
    print("  Стрелки вверх/вниз, Enter для выбора, q для выхода")
    print()

    for index, label in enumerate(labels):
        print(_line(label, index == selected))
    print()

    def render() -> None:
        print(f"\033[{len(labels) + 1}A", end="")
        for index, label in enumerate(labels):
            print(_line(label, index == selected) + "\033[K")
        print()

    try:
        while True:
            key = read_key()
            if key == "up":
                selected = (selected - 1) % len(labels)
                render()
            elif key == "down":
                selected = (selected + 1) % len(labels)
                render()
            elif key == "enter":
                print(f"\n  Выбрано: {labels[selected]}\n")
                return options[selected][1]
            elif key == "q":
                raise SystemExit("  Выход.")
    except Exception:
        for index, label in enumerate(labels, start=1):
            print(f"  {index}. {label}")
        raw = input("  Номер: ").strip()
        index = int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= len(labels) else 0
        return options[index][1]


def confirm(question: str, *, default: bool = False) -> bool:
    options = [("Да", {"value": True}), ("Нет", {"value": False})]
    if not default:
        options.reverse()
    try:
        return bool(choose(question, options)["value"])
    except Exception:
        suffix = "Y/n" if default else "y/N"
        answer = input(f"{question} ({suffix}): ").strip().lower()
        if not answer:
            return default
        return answer in {"y", "yes", "д", "да"}


def _line(label: str, active: bool) -> str:
    if active:
        return f"\033[36m  >  {label}\033[0m"
    return f"     {label}"
