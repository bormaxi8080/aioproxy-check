"""Asynchronously check proxy lists and record results."""

import argparse
import asyncio
import ipaddress
import json
import logging
import re
import ssl
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import aiohttp
import certifi
from colorama import Fore, Style, init as colorama_init
from tqdm import tqdm

# ------------------- CONFIGURATION -------------------
PROXY_DIR = Path("proxy")                       # Folder with proxy lists
PROXY_TYPES = ("http", "socks4", "socks5")
CHECK_SERVICE_URL = "https://api.myip.com"
GEOLOOKUP_URL = "https://ipwho.is/{ip}"
OK_PROXIES_WITH_IP_FILE = "ok_proxies_with_ip.txt"  # Working proxies with all observed IPs
OK_PROXIES_FILE = "ok_proxies.txt"             # Working proxies only (no IPs)
BAD_PROXIES_FILE = "bad_proxies.txt"           # Proxies that never returned IP
LOG_FILE = "actions.log"                       # Log file name
TIMEOUT = 5                                    # Request timeout in seconds
MAX_CONCURRENCY = 200                          # Max simultaneous proxy checks
RETRIES = 1                                    # Additional retries per proxy check
RETRY_BACKOFF = 0.2                            # Base retry delay in seconds
# ------------------------------------------------------

# Initialize colorama for colored console output
colorama_init(autoreset=True)

# Create SSL context using certifi CA bundle
ssl_ctx = ssl.create_default_context(cafile=certifi.where())

# Configure logger
logger = logging.getLogger("ProxyChecker")
logger.setLevel(logging.DEBUG)

# File logging (detailed)
file_handler = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

# Console logging (colored)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)


class ColorFormatter(logging.Formatter):
    """Custom formatter to add colors to console log levels."""
    COLORS = {
        logging.DEBUG: Style.DIM,
        logging.INFO: Fore.CYAN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{super().format(record)}{Style.RESET_ALL}"


console_formatter = ColorFormatter("%(message)s")
console_handler.setFormatter(console_formatter)

# Attach handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)


def parse_args():
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        description="Asynchronously check proxy list from proxy/<file_name>.",
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
        help="Resolve country code for each proxy IP and write it to output (default: true)",
    )
    parser.add_argument(
        "--check-url",
        default=CHECK_SERVICE_URL,
        help=(
            "URL used to detect proxy egress IP "
            "(default: https://api.myip.com)"
        ),
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=MAX_CONCURRENCY,
        help="Maximum simultaneous proxy checks (default: 200)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=RETRIES,
        help="Additional retries per proxy on transient errors (default: 1)",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=RETRY_BACKOFF,
        help="Base retry delay in seconds (default: 0.2)",
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

    # Ignore common header/comment lines in sourced lists.
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


def extract_ip_from_response(response_body: str):
    """
    Extract an IP address from response text.

    Supported response formats:
      - JSON object with key "ip" / "origin" / "query"
      - JSON string with raw IP
      - Plain text IP
    """
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
        # "origin" may contain a comma-separated IP list.
        first_part = candidate.split(",")[0].strip()
        try:
            ipaddress.ip_address(first_part)
            return first_part
        except ValueError:
            continue
    return None


def format_error(error: BaseException) -> str:
    """Convert exception to a non-empty, readable error message."""
    message = str(error).strip()
    return message if message else error.__class__.__name__


async def check_proxy(
    proxy: str,
    check_url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    retries: int,
    retry_backoff: float,
):
    """Try connecting to the check URL through the provided proxy."""
    for attempt in range(retries + 1):
        try:
            async with semaphore:
                async with session.get(
                    url=check_url,
                    proxy=proxy,
                    ssl=ssl_ctx,
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
            return {"status": True, "message": {"ip": ip}, "proxy": proxy}
        except aiohttp.InvalidURL:
            return {"status": False, "message": f"Invalid proxy URL: {proxy}", "proxy": proxy}
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ssl.SSLError,
        ) as error:
            if attempt < retries:
                await asyncio.sleep(retry_backoff * (attempt + 1))
                continue
            return {"status": False, "message": format_error(error), "proxy": proxy}


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
    ) as error:
        logger.debug("Location lookup failed for %s: %s", ip, error)
        return "N/A"

    country = data.get("country_code")
    if isinstance(country, str) and country:
        return country.upper()
    return "N/A"


async def resolve_locations(ips, resolve_location: bool, ip_location_cache):
    """Resolve locations for IPs and update cache."""
    if not resolve_location:
        return

    unresolved_ips = [ip for ip in ips if ip not in ip_location_cache]
    if not unresolved_ips:
        return

    async with aiohttp.ClientSession() as session:
        tasks = {ip: asyncio.create_task(resolve_country_for_ip(ip, session)) for ip in unresolved_ips}
        for ip, task in tasks.items():
            ip_location_cache[ip] = await task


async def run_iteration(
    proxy_list,
    iteration_num,
    all_ok_proxies,
    bad_proxy_stats,
    check_url,
    max_concurrency,
    retries,
    retry_backoff,
    resolve_location,
    ip_location_cache,
):
    """
    Run a single iteration of proxy checks.
    Side effects:
      - Updates all_ok_proxies: dict {proxy: {ip1: country1, ip2: country2}}
      - Updates bad_proxy_stats: dict {
          proxy: {"fail_count": int, "last_error": str, "last_check": str}
        }
    """
    oks = 0
    bads = 0
    results = []
    semaphore = asyncio.Semaphore(max_concurrency)
    connector = aiohttp.TCPConnector(
        limit=max_concurrency,
        limit_per_host=max_concurrency,
    )
    client_timeout = aiohttp.ClientTimeout(total=TIMEOUT)

    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
        # Keep a bounded number of in-memory tasks while still streaming progress.
        batch_size = max(max_concurrency * 10, max_concurrency)
        with tqdm(total=len(proxy_list), desc=f"Iteration {iteration_num}") as progress:
            for start in range(0, len(proxy_list), batch_size):
                batch = proxy_list[start:start + batch_size]
                tasks = [
                    asyncio.create_task(
                        check_proxy(
                            proxy,
                            check_url,
                            session,
                            semaphore,
                            retries,
                            retry_backoff,
                        )
                    )
                    for proxy in batch
                ]
                for task in asyncio.as_completed(tasks):
                    result = await task
                    results.append(result)
                    progress.update(1)

    iteration_ips = [
        result["message"]["ip"]
        for result in results
        if result["status"] and isinstance(result["message"], dict) and "ip" in result["message"]
    ]
    await resolve_locations(iteration_ips, resolve_location, ip_location_cache)

    for result in results:
        proxy = result["proxy"]
        if result["status"] and isinstance(result["message"], dict) and "ip" in result["message"]:
            ip = result["message"]["ip"]
            country_code = ip_location_cache.get(ip, "N/A") if resolve_location else ""
            if resolve_location:
                logger.info("OK: %s -> IP: %s (%s)", proxy, ip, country_code)
            else:
                logger.info("OK: %s -> IP: %s", proxy, ip)
            oks += 1
            # Store all observed IPs with country code (if enabled)
            if proxy not in all_ok_proxies:
                all_ok_proxies[proxy] = {}
            if ip not in all_ok_proxies[proxy]:
                all_ok_proxies[proxy][ip] = country_code
        else:
            err = result["message"]
            logger.warning("BAD: %s -> %s", proxy, err)
            bads += 1
            if proxy not in bad_proxy_stats:
                bad_proxy_stats[proxy] = {"fail_count": 0, "last_error": "", "last_check": ""}
            bad_proxy_stats[proxy]["fail_count"] += 1
            bad_proxy_stats[proxy]["last_error"] = str(err)
            bad_proxy_stats[proxy]["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    oks_percent = round(oks / len(proxy_list) * 100)
    bads_percent = round(bads / len(proxy_list) * 100)
    logger.info(
        "Iteration %s summary: OK: %s (%s%%) / BAD: %s (%s%%)",
        iteration_num,
        oks,
        oks_percent,
        bads,
        bads_percent,
    )
    return oks, bads


async def main():
    """Load proxies, run checks, and write summary output files."""
    args = parse_args()
    if args.iterations < 1:
        logger.error("Iterations must be >= 1.")
        return
    if args.max_concurrency < 1:
        logger.error("max-concurrency must be >= 1.")
        return
    if args.retries < 0:
        logger.error("retries must be >= 0.")
        return
    if args.retry_backoff < 0:
        logger.error("retry-backoff must be >= 0.")
        return

    proxy_file = PROXY_DIR / Path(args.proxy_file_name).name

    # Load proxies from file
    try:
        with open(proxy_file, "r", encoding="utf-8") as f:
            proxy_list = []
            skipped_lines = 0
            for line in f:
                normalized = normalize_proxy(line, args.proxy_type)
                if normalized:
                    proxy_list.append(normalized)
                else:
                    skipped_lines += 1
    except FileNotFoundError:
        logger.error("Proxy file '%s' not found.", proxy_file)
        return

    if not proxy_list:
        logger.error("Proxy list is empty.")
        return

    logger.info(
        "Loaded %s proxies from %s (proxy_type=%s)",
        len(proxy_list),
        proxy_file,
        args.proxy_type,
    )
    logger.info("Check URL: %s", args.check_url)
    logger.info(
        "Concurrency=%s, retries=%s, retry_backoff=%ss",
        args.max_concurrency,
        args.retries,
        args.retry_backoff,
    )
    if skipped_lines:
        logger.info("Skipped %s non-proxy lines (headers/comments/empty).", skipped_lines)

    total_oks = 0
    total_bads = 0
    all_ok_proxies = {}   # {proxy: {ip1: country1, ip2: country2}}
    bad_proxy_stats = {}  # {proxy: {"fail_count": int, "last_error": str, "last_check": str}}
    ip_location_cache = {}  # {ip: country_code}

    # Multiple iterations
    for i in range(1, args.iterations + 1):
        oks, bads = await run_iteration(
            proxy_list,
            i,
            all_ok_proxies,
            bad_proxy_stats,
            args.check_url,
            args.max_concurrency,
            args.retries,
            args.retry_backoff,
            args.resolve_location,
            ip_location_cache,
        )
        total_oks += oks
        total_bads += bads

    # ---- Write GOOD proxies to two files ----
    # 1) Proxies WITH all observed IPs
    if args.resolve_location:
        ok_with_ip_lines = [
            "{} -> {}".format(
                proxy,
                ", ".join(
                    f"{ip} ({country})" for ip, country in sorted(all_ok_proxies[proxy].items())
                ),
            )
            for proxy in sorted(all_ok_proxies.keys())
        ]
    else:
        ok_with_ip_lines = [
            f"{proxy} -> {sorted(all_ok_proxies[proxy].keys())}"
            for proxy in sorted(all_ok_proxies.keys())
        ]
    Path(OK_PROXIES_WITH_IP_FILE).write_text("\n".join(ok_with_ip_lines), encoding="utf-8")

    # 2) Proxies ONLY (no IPs)
    Path(OK_PROXIES_FILE).write_text(
        "\n".join(proxy for proxy in sorted(all_ok_proxies.keys())),
        encoding="utf-8",
    )

    # ---- Write NEVER-SUCCESSFUL proxies (sorted by fails desc) ----
    never_ok = {
        proxy: stats
        for proxy, stats in bad_proxy_stats.items()
        if proxy not in all_ok_proxies
    }
    with open(BAD_PROXIES_FILE, "w", encoding="utf-8") as f:
        for proxy, stats in sorted(
            never_ok.items(),
            key=lambda x: x[1]["fail_count"],
            reverse=True,
        ):
            f.write(
                f"{proxy} | Fails: {stats['fail_count']} | "
                f"Last error: {stats['last_error']} | Last check: {stats['last_check']}\n"
            )

    # Final summary
    total_checks = total_oks + total_bads
    success_rate = round(total_oks / total_checks * 100) if total_checks else 0

    logger.info("%s", "=" * 50)
    logger.info("FINAL SUMMARY for %s iterations:", args.iterations)
    logger.info("Total checks: %s", total_checks)
    logger.info("Total OK: %s", total_oks)
    logger.info("Total BAD: %s", total_bads)
    logger.info("Success rate: %s%%", success_rate)
    logger.info("Working proxies with IPs saved to %s", OK_PROXIES_WITH_IP_FILE)
    logger.info("Working proxies only saved to %s", OK_PROXIES_FILE)
    logger.info("Never-successful proxies saved to %s", BAD_PROXIES_FILE)
    logger.info("%s", "=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
