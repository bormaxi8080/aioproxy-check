"""Asynchronously check proxy lists and record results."""

import argparse
import asyncio
import json
import logging
import ssl
from datetime import datetime
from pathlib import Path

import aiohttp
import certifi
from colorama import Fore, Style, init as colorama_init
from tqdm import tqdm

# ------------------- CONFIGURATION -------------------
PROXY_DIR = Path("proxy")                       # Folder with proxy lists
PROXY_TYPES = ("http", "socks4", "socks5")
GEOLOOKUP_URL = "https://ipwho.is/{ip}"
OK_PROXIES_WITH_IP_FILE = "ok_proxies_with_ip.txt"  # Working proxies with all observed IPs
OK_PROXIES_FILE = "ok_proxies.txt"             # Working proxies only (no IPs)
BAD_PROXIES_FILE = "bad_proxies.txt"           # Proxies that never returned IP
LOG_FILE = "actions.log"                       # Log file name
TIMEOUT = 5                                    # Request timeout in seconds
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
    return parser.parse_args()


def normalize_proxy(proxy: str, proxy_type: str) -> str:
    """Normalize proxy URL by prepending proxy type if scheme is absent."""
    stripped = proxy.strip()
    if "://" in stripped:
        return stripped
    return f"{proxy_type}://{stripped}"


async def check_proxy(proxy: str):
    """
    Try connecting to https://api.ipify.org through the provided proxy.
    Returns:
        dict: {status: bool, message: dict|str, proxy: str}
              - On success: message is a dict with key "ip"
              - On failure: message is an error string
    """
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                url="https://api.ipify.org?format=json",
                proxy=proxy,
                ssl=ssl_ctx,
                timeout=TIMEOUT,
            ) as response:
                return {"status": True, "message": await response.json(), "proxy": proxy}
        except aiohttp.InvalidURL:
            return {"status": False, "message": f"Invalid proxy URL: {proxy}", "proxy": proxy}
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
            ssl.SSLError,
        ) as e:
            return {"status": False, "message": str(e), "proxy": proxy}


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
    tasks = [check_proxy(proxy) for proxy in proxy_list]
    results = []

    # Progress over concurrently completed tasks
    for coro in tqdm(
        asyncio.as_completed(tasks),
        total=len(tasks),
        desc=f"Iteration {iteration_num}",
    ):
        result = await coro
        results.append(result)

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

    proxy_file = PROXY_DIR / Path(args.proxy_file_name).name

    # Load proxies from file
    try:
        with open(proxy_file, "r", encoding="utf-8") as f:
            proxy_list = [normalize_proxy(line, args.proxy_type) for line in f if line.strip()]
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
