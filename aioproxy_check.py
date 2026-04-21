"""Check a list of proxies by dispatching async requests."""

import argparse
import asyncio
import json
import re
import ssl
from pathlib import Path
from urllib.parse import quote

import aiohttp
import certifi

from utils import DEFAULT_CHECK_URL, get_starship

PROXY_DIR = Path("proxy")
PROXY_TYPES = ("http", "socks4", "socks5")
GEOLOOKUP_URL = "https://ipwho.is/{ip}"
TIMEOUT = 5
ssl_ctx = ssl.create_default_context(cafile=certifi.where())


def parse_args():
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        description="Check proxies from proxy/<file_name>.",
    )
    parser.add_argument("proxy_file_name", help="Proxy list file name inside proxy/ folder")
    parser.add_argument(
        "--proxy-type",
        "-t",
        choices=PROXY_TYPES,
        default="http",
        help="Proxy type for entries without scheme (default: http)",
    )
    parser.add_argument(
        "--iterations",
        "-i",
        type=int,
        default=1,
        help="Number of check iterations (default: 1)",
    )
    parser.add_argument(
        "--resolve-location",
        dest="resolve_location",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resolve country code for each proxy IP in output (default: true)",
    )
    parser.add_argument(
        "--check-url",
        default=DEFAULT_CHECK_URL,
        help=(
            "URL used to detect proxy egress IP "
            "(default: https://api.myip.com)"
        ),
    )
    return parser.parse_args()


def normalize_proxy(proxy: str, proxy_type: str) -> str:
    """
    Normalize proxy line to a URL accepted by aiohttp.

    Supported input formats:
      - scheme://host:port
      - scheme://user:pass@host:port
      - host:port
      - host:port:login:pass (converted to scheme://login:pass@host:port)
    """
    stripped = proxy.strip()
    if not stripped:
        return ""

    lowered = stripped.lower()
    if stripped.startswith("#"):
        return ""
    if lowered.startswith("host:port") and "login" in lowered and "pass" in lowered:
        return ""

    if "://" in stripped:
        return stripped
    auth_match = re.fullmatch(
        r"(?P<host>[^:\s]+):(?P<port>\d{1,5}):(?P<login>[^:\s]+):(?P<password>[^:\s]+)",
        stripped,
    )
    if auth_match:
        port = int(auth_match.group("port"))
        if 1 <= port <= 65535:
            login = quote(auth_match.group("login"), safe="")
            password = quote(auth_match.group("password"), safe="")
            return (
                f"{proxy_type}://{login}:{password}"
                f"@{auth_match.group('host')}:{port}"
            )
    return f"{proxy_type}://{stripped}"


async def main():
    """Run proxy checks from a file and print aggregate results."""
    args = parse_args()
    if args.iterations < 1:
        print("Iterations must be >= 1")
        return

    tasks = []
    proxy_list = []
    total_oks = 0
    total_bads = 0
    ip_location_cache = {}

    proxy_file_path = PROXY_DIR / Path(args.proxy_file_name).name

    skipped_lines = 0
    with open(proxy_file_path, "r", encoding="utf-8") as proxy_file:
        for line in proxy_file:
            normalized = normalize_proxy(line, args.proxy_type)
            if normalized:
                proxy_list.append(normalized)
            else:
                skipped_lines += 1

    print(f"Loaded {len(proxy_list)} proxies from {proxy_file_path} (proxy_type={args.proxy_type})")
    print(f"Check URL: {args.check_url}")
    if skipped_lines:
        print(f"Skipped {skipped_lines} non-proxy lines (headers/comments/empty).")

    for iteration in range(1, args.iterations + 1):
        tasks = [get_starship(proxy, check_url=args.check_url) for proxy in proxy_list]
        oks = 0
        bads = 0
        print(f"Iteration {iteration}/{args.iterations}")
        results = await asyncio.gather(*tasks)

        if args.resolve_location:
            iteration_ips = [
                result['message']['ip']
                for result in results
                if result['status']
                and isinstance(result.get('message'), dict)
                and 'ip' in result['message']
                and result['message']['ip'] not in ip_location_cache
            ]
            if iteration_ips:
                async with aiohttp.ClientSession() as session:
                    location_tasks = {
                        ip: asyncio.create_task(resolve_country_for_ip(ip, session))
                        for ip in iteration_ips
                    }
                    for ip, location_task in location_tasks.items():
                        ip_location_cache[ip] = await location_task

        for result in results:
            if result['status']:
                message = result.get('message')
                if isinstance(message, dict) and 'ip' in message:
                    if args.resolve_location:
                        country_code = ip_location_cache.get(message['ip'], "N/A")
                        print(f"OK: {result['proxy']}: {message} ({country_code})")
                    else:
                        print(f"OK: {result['proxy']}: {message}")
                    oks += 1
                else:
                    print(f"BAD: {result['proxy']}: {message}")
                    bads += 1
            else:
                print(f"BAD: {result['proxy']}: {result['message']}")
                bads += 1

        total_oks += oks
        total_bads += bads
        oks_percent = round(oks / len(proxy_list) * 100)
        bads_percent = round(bads / len(proxy_list) * 100)
        print(f"Iteration {iteration} result: OKS: {oks}({oks_percent}%) / BADS: {bads}({bads_percent}%)")

    total_checks = total_oks + total_bads
    total_oks_percent = round(total_oks / total_checks * 100) if total_checks else 0
    total_bads_percent = round(total_bads / total_checks * 100) if total_checks else 0
    print(
        f"FINAL ({args.iterations} iterations): "
        f"OKS: {total_oks}({total_oks_percent}%) / BADS: {total_bads}({total_bads_percent}%)"
    )


async def resolve_country_for_ip(ip: str, session: aiohttp.ClientSession) -> str:
    """Resolve IP country code via public API."""
    try:
        async with session.get(
            url=GEOLOOKUP_URL.format(ip=ip),
            ssl=ssl_ctx,
            timeout=TIMEOUT,
        ) as response:
            data = await response.json()
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        json.JSONDecodeError,
        ssl.SSLError,
    ):
        return "N/A"

    country = data.get("country_code")
    if isinstance(country, str) and country:
        return country.upper()
    return "N/A"


# Not used in Python 3.10 or later
# asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

asyncio.run(main())
