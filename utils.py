"""Async helpers for proxy IP checks."""

import asyncio
import ipaddress
import json
import ssl

import aiohttp
import certifi


ssl_ctx = ssl.create_default_context(cafile=certifi.where())
DEFAULT_CHECK_URL = "https://api.myip.com"
DEFAULT_TIMEOUT = 5


def extract_ip_from_response(response_body: str):
    """Extract IP from JSON/plain-text check service responses."""
    stripped = response_body.strip()
    if not stripped:
        return None

    parsed = stripped
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = stripped

    candidates = []
    if isinstance(parsed, dict):
        for key in ("ip", "origin", "query"):
            value = parsed.get(key)
            if isinstance(value, str):
                candidates.append(value)
    elif isinstance(parsed, str):
        candidates.append(parsed)

    for candidate in candidates:
        first_part = candidate.split(",")[0].strip()
        try:
            ipaddress.ip_address(first_part)
            return first_part
        except ValueError:
            continue
    return None


async def _get_starship(
    proxy: str,
    check_url: str = DEFAULT_CHECK_URL,
    timeout: int = DEFAULT_TIMEOUT,
):
    """Fetch the caller's IP via the proxy using a default SSL context."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                    url=check_url,
                    proxy=proxy,
                    timeout=timeout
            ) as response:
                response.raise_for_status()
                body = await response.text()
                ip = extract_ip_from_response(body)
                if ip is None:
                    return {
                        "status": False,
                        "message": f"IP not found in response from {check_url}",
                        "proxy": proxy,
                    }
                return {
                    "status": True,
                    "message": {"ip": ip},
                    "proxy": proxy,
                }
        except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            return {"status": False, "message": str(e), "proxy": proxy}


async def get_starship(
    proxy: str,
    check_url: str = DEFAULT_CHECK_URL,
    timeout: int = DEFAULT_TIMEOUT,
):
    """Fetch the caller's IP via the proxy using the shared SSL context."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                    url=check_url,
                    proxy=proxy,
                    ssl=ssl_ctx,
                    timeout=timeout
            ) as response:
                response.raise_for_status()
                body = await response.text()
                ip = extract_ip_from_response(body)
                if ip is None:
                    return {
                        "status": False,
                        "message": f"IP not found in response from {check_url}",
                        "proxy": proxy,
                    }
                return {
                    "status": True,
                    "message": {"ip": ip},
                    "proxy": proxy,
                }
        except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            return {"status": False, "message": str(e), "proxy": proxy}
