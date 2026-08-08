from __future__ import annotations

import time
from typing import Any

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


def solve_hcaptcha(
    api_key: str,
    *,
    site_key: str,
    page_url: str,
    proxy: str | None = None,
    is_enterprise: bool = True,
    timeout_seconds: int = 180,
) -> str:
    """Solve hCaptcha via Capsolver. Returns token string for Dynamic captchaToken."""
    key = normalize_api_key(api_key)
    if not key:
        raise CapsolverError(
            "Capsolver API key пустой — положи в input/capsolver_api_key.txt и пересоздай базу"
        )

    # Validate key early with clear message
    try:
        bal = get_balance(key)
    except CapsolverError as exc:
        raise CapsolverError(
            f"Capsolver key не принят ({exc}). "
            "Проверь ключ в dashboard capsolver.com → API Key, "
            "перезапиши input/capsolver_api_key.txt (одна строка, без кавычек) и пересоздай базу."
        ) from exc

    if bal <= 0:
        raise CapsolverError(f"Capsolver balance = {bal}. Пополни баланс на capsolver.com")

    # Capsolver historically used both spellings; try preferred first.
    type_candidates: list[str]
    if proxy:
        type_candidates = ["HCaptchaTask", "HCaptchaEnterpriseTask"]
    else:
        type_candidates = [
            "HCaptchaTaskProxyLess",
            "HCaptchaTaskProxyless",
            "HCaptchaEnterpriseTaskProxyLess",
            "HCaptchaEnterpriseTaskProxyless",
        ]

    last_err: Exception | None = None
    for task_type in type_candidates:
        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        if is_enterprise and "Enterprise" not in task_type:
            task["isEnterprise"] = True
        if proxy and "ProxyLess" not in task_type and "Proxyless" not in task_type:
            # Capsolver proxy string formats: "ip:port:user:pass" or full URL
            task["proxy"] = proxy.replace("http://", "").replace("https://", "")

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
            # Invalid key / balance — no point trying other types
            msg = str(exc).lower()
            if "invalid" in msg and "key" in msg:
                raise
            if "balance" in msg:
                raise
            continue

    raise CapsolverError(f"все task types failed: {last_err}")


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
