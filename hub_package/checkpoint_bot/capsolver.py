from __future__ import annotations

import time
from typing import Any
from urllib.parse import unquote, urlparse

import requests


class CapsolverError(RuntimeError):
    pass


def _post(url: str, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    """POST to Capsolver; never hide error body behind bare HTTPError."""
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise CapsolverError(f"network error: {exc}") from exc

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text[:400], "httpStatus": resp.status_code}

    # Capsolver often returns HTTP 400 with JSON {errorId, errorCode, errorDescription}
    if isinstance(data, dict) and data.get("errorId"):
        code = data.get("errorCode") or ""
        desc = data.get("errorDescription") or data.get("error") or data
        raise CapsolverError(f"{code}: {desc}".strip(": "))

    if resp.status_code >= 400:
        raise CapsolverError(f"HTTP {resp.status_code}: {str(data)[:300]}")

    return data if isinstance(data, dict) else {"raw": data}


def normalize_api_key(api_key: str) -> str:
    key = (api_key or "").strip().strip('"').strip("'")
    # common paste artifacts
    key = key.replace("\u200b", "").replace("\ufeff", "")
    return key


def get_balance(api_key: str) -> float:
    key = normalize_api_key(api_key)
    if not key:
        raise CapsolverError("Capsolver API key пустой")
    data = _post(
        "https://api.capsolver.com/getBalance",
        {"clientKey": key},
        timeout=30,
    )
    bal = data.get("balance")
    if bal is None:
        raise CapsolverError(f"getBalance unexpected: {data}")
    return float(bal)


def proxy_task_fields(proxy: str | None) -> dict[str, Any]:
    """Capsolver proxy fields. Hub stores `http://user:pass@host:port` — keep that URL."""
    raw = (proxy or "").strip()
    if not raw:
        return {}
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    port = parsed.port
    if not host or not port:
        raise CapsolverError("Capsolver: не разобрали proxy")
    scheme = (parsed.scheme or "http").lower()
    if scheme in {"socks5", "socks5h"}:
        ptype = "socks5"
    elif scheme in {"socks4", "socks4a"}:
        ptype = "socks4"
    else:
        ptype = "http"
    fields: dict[str, Any] = {
        "proxyType": ptype,
        "proxyAddress": host,
        "proxyPort": int(port),
        "proxy": raw,
    }
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    if user:
        fields["proxyLogin"] = user
        fields["proxyPassword"] = password
    return fields


def _unsupported_hcaptcha(exc: Exception) -> bool:
    text = str(exc).lower()
    return "not supported" in text or "unsupport" in text or "deprecated" in text


def solve_hcaptcha(
    api_key: str,
    *,
    site_key: str,
    page_url: str,
    proxy: str | None = None,
    is_enterprise: bool = True,
    user_agent: str = "",
    timeout_seconds: int = 180,
) -> str:
    """Solve hCaptcha via Capsolver. Returns token string for Dynamic captchaToken."""
    key = normalize_api_key(api_key)
    if not key:
        raise CapsolverError("Capsolver API key пустой")

    try:
        bal = get_balance(key)
    except CapsolverError as exc:
        raise CapsolverError(f"Capsolver key не принят ({exc})") from exc

    if bal <= 0:
        raise CapsolverError(f"Capsolver balance = {bal}. Пополни баланс на capsolver.com")

    # Official token catalog no longer lists hCaptcha. HCaptchaTask may still
    # work on older keys. Do not send the retired enterprise task type.
    type_candidates = ["HCaptchaTask"] if proxy else ["HCaptchaTaskProxyLess", "HCaptchaTaskProxyless"]
    proxy_fields = proxy_task_fields(proxy) if proxy else {}

    last_err: Exception | None = None
    for task_type in type_candidates:
        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        if is_enterprise:
            task["isEnterprise"] = True
        if user_agent:
            task["userAgent"] = user_agent
        if proxy and "ProxyLess" not in task_type and "Proxyless" not in task_type:
            task["proxy"] = str(proxy_fields.get("proxy") or proxy)

        try:
            created = _post(
                "https://api.capsolver.com/createTask",
                {"clientKey": key, "task": task},
            )
            task_id = created.get("taskId")
            if not task_id:
                last_err = CapsolverError(f"no taskId for {task_type}: {created}")
                continue
            token = _poll_result(key, task_id, timeout_seconds=timeout_seconds)
            return token
        except CapsolverError as exc:
            last_err = exc
            msg = str(exc).lower()
            if "invalid" in msg and "key" in msg:
                raise
            if "balance" in msg:
                raise
            if _unsupported_hcaptcha(exc):
                raise CapsolverError("Capsolver не решает hCaptcha") from exc
            continue

    raise CapsolverError(f"hCaptcha не решилась: {last_err}")


def _poll_result(api_key: str, task_id: str, *, timeout_seconds: int) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(3)
        data = _post(
            "https://api.capsolver.com/getTaskResult",
            {"clientKey": api_key, "taskId": task_id},
        )
        status = data.get("status")
        if status == "ready":
            solution = data.get("solution") or {}
            token = (
                solution.get("gRecaptchaResponse")
                or solution.get("token")
                or solution.get("respKey")
                or solution.get("userAgent")  # never use UA; just avoid KeyError path
            )
            # Prefer captcha response fields only
            token = solution.get("gRecaptchaResponse") or solution.get("token")
            if not token:
                raise CapsolverError(f"empty captcha solution: {data}")
            return str(token)
        if status and status not in {"idle", "processing"}:
            raise CapsolverError(f"unexpected status: {data}")
    raise CapsolverError("timeout waiting for hCaptcha solution")
