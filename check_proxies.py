"""Asynchronously check proxy lists and record results."""

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
PROXY_FILE = "proxies_all.txt"                     # File with a proxy list
OK_PROXIES_WITH_IP_FILE = "ok_proxies_with_ip.txt"  # Working proxies with all observed IPs
OK_PROXIES_FILE = "ok_proxies.txt"             # Working proxies only (no IPs)
BAD_PROXIES_FILE = "bad_proxies.txt"           # Proxies that never returned IP
LOG_FILE = "actions.log"                       # Log file name
ITERATIONS = 10                                # Number of proxy check iterations
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
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
            ssl.SSLError,
        ) as e:
            return {"status": False, "message": str(e), "proxy": proxy}


async def run_iteration(proxy_list, iteration_num, all_ok_proxies, bad_proxy_stats):
    """
    Run a single iteration of proxy checks.
    Side effects:
      - Updates all_ok_proxies: dict {proxy: [ip1, ip2, ...]} (all observed IPs)
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

    for result in results:
        proxy = result["proxy"]
        if result["status"] and isinstance(result["message"], dict) and "ip" in result["message"]:
            ip = result["message"]["ip"]
            logger.info("OK: %s -> IP: %s", proxy, ip)
            oks += 1
            # Store all observed IPs in a list without duplicates
            if proxy not in all_ok_proxies:
                all_ok_proxies[proxy] = []
            if ip not in all_ok_proxies[proxy]:
                all_ok_proxies[proxy].append(ip)
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
    # Load proxies from file
    try:
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            proxy_list = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.error("Proxy file '%s' not found.", PROXY_FILE)
        return

    if not proxy_list:
        logger.error("Proxy list is empty.")
        return

    logger.info("Loaded %s proxies from %s", len(proxy_list), PROXY_FILE)

    total_oks = 0
    total_bads = 0
    all_ok_proxies = {}   # {proxy: [ip1, ip2, ...]}
    bad_proxy_stats = {}  # {proxy: {"fail_count": int, "last_error": str, "last_check": str}}

    # Multiple iterations
    for i in range(1, ITERATIONS + 1):
        oks, bads = await run_iteration(proxy_list, i, all_ok_proxies, bad_proxy_stats)
        total_oks += oks
        total_bads += bads

    # ---- Write GOOD proxies to two files ----
    # 1) Proxies WITH all observed IPs
    Path(OK_PROXIES_WITH_IP_FILE).write_text(
        "\n".join(f"{proxy} -> {all_ok_proxies[proxy]}" for proxy in sorted(all_ok_proxies.keys())),
        encoding="utf-8",
    )

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
    logger.info("FINAL SUMMARY for %s iterations:", ITERATIONS)
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
