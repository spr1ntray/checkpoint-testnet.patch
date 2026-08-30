"""HTTP identity from an AdsPower profile — no browser start.

Checkpoint first-party JS does not run FingerprintJS. Dynamic + hCaptcha +
Cloudflare see User-Agent, Client Hints, Accept-Language and the proxy IP.
We copy those from AdsPower Local API (`fingerprint_config`) and send every
Checkpoint / Dynamic / ZeroDev call through the Hub account proxy (same IP
as the Ads profile).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
CHECKPOINT_ORIGIN = "https://checkpoint.exchange"
_CHROME_RE = re.compile(r"(?:Chrome|CriOS)/(\d+)")
_EDGE_RE = re.compile(r"Edg(?:e|A|iOS)?/(\d+)")
_SECRET_FIELD = ("pass", "user", "cookie", "proxy", "fakey", "secret", "token", "auth")
_FP_KEYS = (
    "fingerprint_config",
    "fingerprintConfig",
    "finger_print_config",
    "fp_config",
    "browser_fingerprint",
)


@dataclass(frozen=True)
class BrowserIdentity:
    user_agent: str
    accept_language: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    chrome_major: int
    platform: str
    timezone: str
    screen: str
    hardware_concurrency: str
    fallback: bool
    profile_fields: tuple[str, ...] = ()
    source: str = "ads"

    def headers(self) -> dict[str, str]:
        out = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": self.accept_language,
            "Origin": CHECKPOINT_ORIGIN,
            "Referer": f"{CHECKPOINT_ORIGIN}/",
        }
        if self.sec_ch_ua:
            out["sec-ch-ua"] = self.sec_ch_ua
            out["sec-ch-ua-mobile"] = self.sec_ch_ua_mobile or "?0"
            out["sec-ch-ua-platform"] = self.sec_ch_ua_platform or '"Windows"'
        return {key: value for key, value in out.items() if value}

    def summary(self) -> dict[str, Any]:
        return {
            "chrome": self.chrome_major,
            "platform": self.platform,
            "source": self.source,
            "fallback": self.fallback,
            "language": self.accept_language.split(",", 1)[0],
            "profile_fields": list(self.profile_fields[:24]),
        }


def identity_from_profile(row: dict[str, Any] | None) -> BrowserIdentity:
    """Map AdsPower profile JSON → browser headers. Never copies proxy creds."""
    data = row if isinstance(row, dict) else {}
    fp = _fingerprint_dict(data)

    ua = _first_str(
        fp.get("ua"),
        fp.get("user_agent"),
        data.get("ua"),
        data.get("user_agent"),
        data.get("useragent"),
    )
    kernel = fp.get("browser_kernel_config") if isinstance(fp.get("browser_kernel_config"), dict) else {}
    kernel_version = str(kernel.get("version") or "").strip()
    if not ua and kernel_version.isdigit():
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{kernel_version}.0.0.0 Safari/537.36"
        )
    fallback = not bool(ua)
    if not ua:
        ua = DEFAULT_UA

    chrome_major, brand = _chrome_brand(ua, kernel_version)
    platform, mobile = _platform(ua)
    language = _accept_language(fp.get("language") or data.get("language"))
    timezone = _first_str(fp.get("timezone"), data.get("timezone"))
    screen = _screen(fp.get("screen_resolution") or fp.get("screen") or data.get("screen_resolution"))
    cores = _first_str(fp.get("hardware_concurrency"), data.get("hardware_concurrency"))

    return BrowserIdentity(
        user_agent=ua,
        accept_language=language,
        sec_ch_ua=_sec_ch_ua(chrome_major, brand),
        sec_ch_ua_mobile=mobile,
        sec_ch_ua_platform=f'"{platform}"',
        chrome_major=chrome_major,
        platform=platform,
        timezone=timezone,
        screen=screen,
        hardware_concurrency=cores,
        fallback=fallback,
        profile_fields=_public_fields(data),
        source="ads",
    )


def identity_from_pool(seed: str) -> BrowserIdentity:
    from .fingerprint_pool import pick_profile

    ident = identity_from_profile({"fingerprint_config": pick_profile(seed)})
    return replace(ident, source="pool", fallback=False, profile_fields=("pool",))


def resolve_identity(seed: str, ads_row: dict[str, Any] | None = None) -> BrowserIdentity:
    """Ads fingerprint if it has a real UA; otherwise a stable row from the local pool."""
    if ads_row:
        ident = identity_from_profile(ads_row)
        if not ident.fallback and ident.user_agent:
            return replace(ident, source="ads")
    return identity_from_pool(seed)


def _fingerprint_dict(row: dict[str, Any]) -> dict[str, Any]:
    for key in _FP_KEYS:
        value = row.get(key)
        if isinstance(value, dict) and value:
            return value
        if isinstance(value, str) and value.startswith("{"):
            try:
                import json

                parsed = json.loads(value)
            except Exception:
                parsed = None
            if isinstance(parsed, dict) and parsed:
                return parsed
    return {}


def _public_fields(row: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for key in row.keys():
        lower = str(key).lower()
        if any(token in lower for token in _SECRET_FIELD):
            continue
        names.append(str(key))
    return tuple(sorted(names)[:24])


def default_identity() -> BrowserIdentity:
    return identity_from_pool("checkpoint-default")


def _first_str(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() not in {"none", "null", "n/a", "ua_auto"}:
            return text
    return ""


def _chrome_brand(ua: str, kernel_version: str) -> tuple[int, str]:
    edge = _EDGE_RE.search(ua)
    chrome = _CHROME_RE.search(ua)
    major = 143
    if chrome:
        major = int(chrome.group(1))
    elif kernel_version.isdigit():
        major = int(kernel_version)
    brand = "Microsoft Edge" if edge and not chrome else "Google Chrome"
    if edge and (not chrome or int(edge.group(1)) >= (chrome and int(chrome.group(1)) or 0)):
        brand = "Microsoft Edge"
        major = int(edge.group(1))
    return major, brand


def _platform(ua: str) -> tuple[str, str]:
    if "Android" in ua:
        return "Android", "?1"
    if "iPhone" in ua or "iPad" in ua:
        return "iOS", "?1"
    if "Mac OS X" in ua or "Macintosh" in ua:
        return "macOS", "?0"
    if "Linux" in ua:
        return "Linux", "?0"
    return "Windows", "?0"


def _sec_ch_ua(major: int, brand: str) -> str:
    return f'"Not)A;Brand";v="8", "Chromium";v="{major}", "{brand}";v="{major}"'


def _accept_language(language: Any) -> str:
    parts: list[str] = []
    if isinstance(language, str) and language.strip():
        parts = [item.strip() for item in language.split(",") if item.strip()]
    elif isinstance(language, (list, tuple)):
        parts = [str(item).strip() for item in language if str(item).strip()]
    cleaned: list[str] = []
    for item in parts:
        token = item.split(";", 1)[0].strip()
        if token and token not in cleaned:
            cleaned.append(token)
    if not cleaned:
        cleaned = ["en-US", "en"]
    out = [cleaned[0]]
    quality = 0.9
    for item in cleaned[1:6]:
        out.append(f"{item};q={quality:.1f}")
        quality = max(0.1, quality - 0.1)
    return ",".join(out)


def _screen(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or text in {"none", "random"}:
        return ""
    return text.replace("_", "x")
