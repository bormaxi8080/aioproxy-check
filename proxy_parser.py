"""Helpers for parsing proxy lists from mixed source formats."""

import json
import random
import re
from pathlib import Path
from urllib.parse import quote


PROXY_TYPES = ("http", "socks4", "socks5")
PROXY_SCHEMES = ("http", "https", "socks4", "socks5")
HEADER_WORDS = {
    "host",
    "port",
    "login",
    "user",
    "username",
    "pass",
    "password",
    "protocol",
    "type",
}


def _is_port(value: str) -> bool:
    if not value.isdigit():
        return False
    port = int(value)
    return 1 <= port <= 65535


def _looks_like_host(value: str) -> bool:
    return bool(value) and (
        "." in value
        or value.lower() == "localhost"
        or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value) is not None
    )


def _is_header_or_comment(value: str) -> bool:
    lowered = value.lower()
    if lowered.startswith("#"):
        return True
    words = set(re.findall(r"[a-z]+", lowered))
    if {"host", "port"} <= words and words & HEADER_WORDS:
        return True
    return bool(words & HEADER_WORDS) and not re.search(r"\d{1,5}", lowered)


def _build_proxy_url(scheme: str, host: str, port: str, login: str = "", password: str = "") -> str:
    if login or password:
        return f"{scheme}://{quote(login, safe='')}:{quote(password, safe='')}@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def _parse_pair(pair: str):
    parts = pair.split(":", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _parse_at_format(value: str, scheme: str):
    if "@" not in value:
        return None

    left, right = value.rsplit("@", 1)
    left_pair = _parse_pair(left)
    right_pair = _parse_pair(right)
    if not left_pair or not right_pair:
        return None

    if _is_port(right_pair[1]) and _looks_like_host(right_pair[0]):
        login, password = left_pair
        host, port = right_pair
        return _build_proxy_url(scheme, host, port, login, password)

    if _is_port(left_pair[1]) and _looks_like_host(left_pair[0]):
        host, port = left_pair
        login, password = right_pair
        return _build_proxy_url(scheme, host, port, login, password)

    return None


def _parse_colon_format(value: str, scheme: str):
    parts = value.split(":")
    if len(parts) == 2 and _is_port(parts[1]) and _looks_like_host(parts[0]):
        return _build_proxy_url(scheme, parts[0], parts[1])

    if len(parts) != 4:
        return None

    for idx in (1, 3):
        if not _is_port(parts[idx]):
            continue
        host_idx = idx - 1
        if host_idx < 0 or not _looks_like_host(parts[host_idx]):
            continue
        rest = [part for part_idx, part in enumerate(parts) if part_idx not in (host_idx, idx)]
        if len(rest) == 2:
            return _build_proxy_url(scheme, parts[host_idx], parts[idx], rest[0], rest[1])

    return None


def normalize_proxy(proxy: str, proxy_type: str = "http") -> str:
    """
    Normalize a proxy line to a URL accepted by aiohttp.

    Lines without an explicit protocol use ``proxy_type``. Empty lines, comments,
    and common header rows return an empty string.
    """
    stripped = proxy.strip()
    if not stripped or _is_header_or_comment(stripped):
        return ""

    scheme = proxy_type.lower()
    value = stripped
    if "://" in stripped:
        raw_scheme, value = stripped.split("://", 1)
        if raw_scheme.lower() in PROXY_SCHEMES:
            scheme = raw_scheme.lower()
        else:
            return stripped

    parsed = _parse_at_format(value, scheme)
    if parsed:
        return parsed

    parsed = _parse_colon_format(value, scheme)
    if parsed:
        return parsed

    if "://" in stripped:
        return stripped
    return f"{scheme}://{value}"


def load_proxy_file(proxy_file: Path, proxy_type: str = "http"):
    """Load and normalize proxies from a text file."""
    proxies = []
    skipped_lines = 0
    with open(proxy_file, "r", encoding="utf-8") as file_obj:
        for line in file_obj:
            normalized = normalize_proxy(line, proxy_type)
            if normalized:
                proxies.append(normalized)
            else:
                skipped_lines += 1
    return proxies, skipped_lines


def load_domains(domains_file: Path):
    """Load domain names from domains.json-like files."""
    with open(domains_file, "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)

    domains = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                domain = item
            elif isinstance(item, dict):
                domain = item.get("domain") or item.get("host") or item.get("url")
            else:
                continue
            if isinstance(domain, str) and domain.strip():
                domains.append(domain.strip())
    elif isinstance(data, dict):
        for value in data.values():
            if isinstance(value, str) and value.strip():
                domains.append(value.strip())
            elif isinstance(value, dict):
                domain = value.get("domain") or value.get("host") or value.get("url")
                if isinstance(domain, str) and domain.strip():
                    domains.append(domain.strip())

    return domains


def select_domains(domains, domain_checks: str):
    """Return all domains or a random sample based on a CLI value."""
    if domain_checks == "all":
        return list(domains)

    count = int(domain_checks)
    if count < 1:
        raise ValueError("domain check count must be >= 1")
    if count >= len(domains):
        return list(domains)
    return random.sample(list(domains), count)
