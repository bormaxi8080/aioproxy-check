import aiohttp
import asyncio
import ssl
import certifi
import logging

from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init
from pathlib import Path

# ------------------- CONFIGURATION -------------------
PROXY_FILE = "proxies.txt"            # File with a proxy list
OK_PROXIES_FILE = "ok_proxies.txt"    # File to store successful proxies with IP
BAD_PROXIES_FILE = "bad_proxies.txt"  # File to store proxies that never returned an IP
LOG_FILE = "actions.log"              # Log file name
ITERATIONS = 5                        # Number of proxy check iterations
TIMEOUT = 5                           # Request timeout in seconds
# ------------------------------------------------------

# Initialize colorama for colored output
colorama_init(autoreset=True)

# Create SSL context using certifi CA bundle
ssl_ctx = ssl.create_default_context(cafile=certifi.where())

# Configure logger
logger = logging.getLogger("ProxyChecker")
logger.setLevel(logging.DEBUG)

# Log to file (detailed)
file_handler = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

# Log to console (colored)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)


class ColorFormatter(logging.Formatter):
    """Custom formatter to add colors to log levels in console output."""
    COLORS = {
        logging.DEBUG: Style.DIM,
        logging.INFO: Fore.CYAN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{super().format(record)}{Style.RESET_ALL}"


console_formatter = ColorFormatter("%(message)s")
console_handler.setFormatter(console_formatter)

# Attach handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)


async def check_proxy(proxy: str):
    """
    Attempt to connect to https://api.ipify.org through the given proxy.
    Returns a dict with status, message and proxy string.
    """
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                url="https://api.ipify.org?format=json",
                proxy=proxy,
                ssl=ssl_ctx,
                timeout=TIMEOUT
            ) as response:
                return {"status": True, "message": await response.json(), "proxy": proxy}
        except Exception as e:
            return {"status": False, "message": str(e), "proxy": proxy}


async def run_iteration(proxy_list, iteration_num, all_ok_proxies, bad_stats):
    """
    Run a single iteration of proxy checking.
    Logs results to both console and file.
    Updates:
      - all_ok_proxies: dict {proxy: ip} for successful ones
      - bad_stats: dict {proxy: {"fails": int, "last_error": str}}
    """
    oks = 0
    bads = 0
    tasks = [check_proxy(proxy) for proxy in proxy_list]

    results = []
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=f"Iteration {iteration_num}"):
        result = await coro
        results.append(result)

    for result in results:
        proxy = result["proxy"]
        if result["status"] and isinstance(result["message"], dict) and "ip" in result["message"]:
            ip = result["message"]["ip"]
            logger.info(f"OK: {proxy} -> IP: {ip}")
            oks += 1
            all_ok_proxies[proxy] = ip  # store proxy-IP pair
        else:
            logger.warning(f"BAD: {proxy} -> {result['message']}")
            bads += 1
            # Increment fail counter and store the last error
            if proxy not in bad_stats:
                bad_stats[proxy] = {"fails": 0, "last_error": ""}
            bad_stats[proxy]["fails"] += 1
            bad_stats[proxy]["last_error"] = result["message"]

    logger.info(
        f"Iteration {iteration_num} summary: OKS: {oks}({round(oks / len(proxy_list) * 100)}%) / "
        f"BADS: {bads}({round(bads / len(proxy_list) * 100)}%)"
    )

    return oks, bads


async def main():
    # Load the proxy list from a file
    try:
        with open(PROXY_FILE, "r") as f:
            proxy_list = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.error(f"Proxy file '{PROXY_FILE}' not found.")
        return

    if not proxy_list:
        logger.error("Proxy list is empty.")
        return

    logger.info(f"Loaded {len(proxy_list)} proxies from {PROXY_FILE}")

    total_oks = 0
    total_bads = 0
    all_ok_proxies = {}  # {proxy: ip}
    bad_stats = {}       # {proxy: {"fails": int, "last_error": str}}

    # Run multiple iterations
    for i in range(1, ITERATIONS + 1):
        oks, bads = await run_iteration(proxy_list, i, all_ok_proxies, bad_stats)
        total_oks += oks
        total_bads += bads

    # Save unique successful proxies to a file
    Path(OK_PROXIES_FILE).write_text(
        "\n".join(f"{proxy} -> {ip}" for proxy, ip in sorted(all_ok_proxies.items())),
        encoding="utf-8"
    )

    # Save proxies that NEVER returned an IP
    never_ok = {
        proxy: data
        for proxy, data in bad_stats.items()
        if proxy not in all_ok_proxies
    }
    Path(BAD_PROXIES_FILE).write_text(
        "\n".join(
            f"{proxy} | fails: {data['fails']} | last_error: {data['last_error']}"
            for proxy, data in sorted(never_ok.items())
        ),
        encoding="utf-8"
    )

    # Log final summary
    total_checks = total_oks + total_bads
    success_rate = round(total_oks / total_checks * 100) if total_checks else 0

    logger.info("=" * 50)
    logger.info(f"FINAL SUMMARY for {ITERATIONS} iterations:")
    logger.info(f"Total checks: {total_checks}")
    logger.info(f"Total OK: {total_oks}")
    logger.info(f"Total BAD: {total_bads}")
    logger.info(f"Success rate: {success_rate}%")
    logger.info(f"Unique successful proxies saved to {OK_PROXIES_FILE}")
    logger.info(f"Never-successful proxies saved to {BAD_PROXIES_FILE}")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
