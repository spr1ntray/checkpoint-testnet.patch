"""Local browser-identity pool when AdsPower is missing or has no UA.

Built from real desktop Chrome UA shapes (Windows / macOS / Linux), common
Accept-Language packs and screens. One account always maps to the same row.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

# Desktop Chrome majors seen in the wild through 2025–2026.
_CHROME = (
    120, 122, 124, 125, 126, 127, 128, 129, 130, 131, 132,
    133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143,
)

_OS = (
    ("Windows", "Windows NT 10.0; Win64; x64"),
    ("Windows", "Windows NT 10.0; Win64; x64"),
    ("macOS", "Macintosh; Intel Mac OS X 10_15_7"),
    ("macOS", "Macintosh; Intel Mac OS X 13_6_7"),
    ("macOS", "Macintosh; Intel Mac OS X 14_5"),
    ("macOS", "Macintosh; Intel Mac OS X 15_2"),
    ("Linux", "X11; Linux x86_64"),
)

# (languages, timezone, screens, cores) — locale-consistent, not random soup.
_LOCALES = (
    (("en-US", "en"), "America/New_York", ("1920x1080", "1366x768", "2560x1440"), ("8", "12", "16")),
    (("en-US", "en"), "America/Chicago", ("1920x1080", "1536x864", "1440x900"), ("4", "8", "16")),
    (("en-US", "en"), "America/Los_Angeles", ("1920x1080", "2560x1440", "1680x1050"), ("8", "12", "16")),
    (("en-GB", "en"), "Europe/London", ("1920x1080", "1366x768", "2560x1440"), ("8", "12")),
    (("en-GB", "en-US", "en"), "Europe/London", ("1440x900", "1920x1080"), ("4", "8")),
    (("de-DE", "de", "en"), "Europe/Berlin", ("1920x1080", "2560x1440", "1366x768"), ("8", "16")),
    (("de-AT", "de", "en"), "Europe/Vienna", ("1920x1080", "1440x900"), ("8", "12")),
    (("fr-FR", "fr", "en"), "Europe/Paris", ("1920x1080", "1366x768", "2560x1440"), ("4", "8", "12")),
    (("es-ES", "es", "en"), "Europe/Madrid", ("1920x1080", "1366x768"), ("4", "8")),
    (("es-MX", "es", "en"), "America/Mexico_City", ("1366x768", "1920x1080"), ("4", "8")),
    (("pt-BR", "pt", "en"), "America/Sao_Paulo", ("1366x768", "1920x1080", "1536x864"), ("4", "8", "12")),
    (("pt-PT", "pt", "en"), "Europe/Lisbon", ("1920x1080", "1440x900"), ("8", "16")),
    (("it-IT", "it", "en"), "Europe/Rome", ("1920x1080", "1366x768"), ("4", "8", "12")),
    (("nl-NL", "nl", "en"), "Europe/Amsterdam", ("1920x1080", "2560x1440"), ("8", "16")),
    (("pl-PL", "pl", "en"), "Europe/Warsaw", ("1920x1080", "1366x768", "1600x900"), ("4", "8")),
    (("cs-CZ", "cs", "en"), "Europe/Prague", ("1920x1080", "1440x900"), ("8", "12")),
    (("ro-RO", "ro", "en"), "Europe/Bucharest", ("1920x1080", "1366x768"), ("4", "8")),
    (("hu-HU", "hu", "en"), "Europe/Budapest", ("1920x1080", "1536x864"), ("8", "16")),
    (("sv-SE", "sv", "en"), "Europe/Stockholm", ("1920x1200", "1920x1080", "2560x1440"), ("8", "12")),
    (("da-DK", "da", "en"), "Europe/Copenhagen", ("1920x1080", "1440x900"), ("8", "16")),
    (("nb-NO", "nb", "en"), "Europe/Oslo", ("1920x1080", "2560x1440"), ("8", "12")),
    (("fi-FI", "fi", "en"), "Europe/Helsinki", ("1920x1080", "1680x1050"), ("8", "16")),
    (("el-GR", "el", "en"), "Europe/Athens", ("1920x1080", "1366x768"), ("4", "8")),
    (("tr-TR", "tr", "en"), "Europe/Istanbul", ("1920x1080", "1366x768", "1536x864"), ("4", "8", "12")),
    (("uk-UA", "uk", "en"), "Europe/Kyiv", ("1920x1080", "1366x768"), ("4", "8")),
    (("ru-RU", "ru", "en"), "Europe/Moscow", ("1920x1080", "1366x768", "1600x900"), ("4", "8", "16")),
    (("ja-JP", "ja", "en"), "Asia/Tokyo", ("1440x900", "1680x1050", "2560x1600", "2880x1800"), ("8", "12", "16")),
    (("ko-KR", "ko", "en"), "Asia/Seoul", ("1920x1080", "2560x1440", "1440x900"), ("8", "16")),
    (("zh-TW", "zh", "en"), "Asia/Taipei", ("1920x1080", "1366x768"), ("4", "8", "12")),
    (("zh-CN", "zh", "en"), "Asia/Shanghai", ("1920x1080", "1536x864", "2560x1440"), ("8", "12")),
    (("th-TH", "th", "en"), "Asia/Bangkok", ("1366x768", "1920x1080"), ("4", "8")),
    (("vi-VN", "vi", "en"), "Asia/Ho_Chi_Minh", ("1366x768", "1920x1080"), ("4", "8")),
    (("id-ID", "id", "en"), "Asia/Jakarta", ("1366x768", "1920x1080"), ("4", "8")),
    (("hi-IN", "en-IN", "en"), "Asia/Kolkata", ("1366x768", "1920x1080", "1536x864"), ("4", "8", "12")),
    (("en-AU", "en"), "Australia/Sydney", ("1920x1080", "2560x1440", "1440x900"), ("8", "16")),
    (("en-CA", "en"), "America/Toronto", ("1920x1080", "1366x768", "2560x1440"), ("8", "12")),
)


def _ua(os_token: str, chrome: int) -> str:
    return (
        f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome}.0.0.0 Safari/537.36"
    )


@lru_cache(maxsize=1)
def all_profiles() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for loc_index, (languages, timezone, screens, cores) in enumerate(_LOCALES):
        for os_index, (_platform, os_token) in enumerate(_OS):
            for chrome in _CHROME:
                mix = loc_index + os_index + chrome
                screen = screens[mix % len(screens)]
                cpu = cores[mix % len(cores)]
                rows.append(
                    {
                        "ua": _ua(os_token, chrome),
                        "language": list(languages),
                        "timezone": timezone,
                        "screen_resolution": screen.replace("x", "_"),
                        "hardware_concurrency": cpu,
                    }
                )
    return tuple(rows)


def pool_size() -> int:
    return len(all_profiles())


def pick_profile(seed: str) -> dict[str, Any]:
    rows = all_profiles()
    digest = hashlib.sha256((seed or "checkpoint").encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(rows)
    return dict(rows[index])
