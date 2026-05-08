# aioproxy-check

Async proxy list checker for HTTP, SOCKS4, and SOCKS5 proxies.

Current package version: `0.1.0`.

The main checker is `check_proxies.py`. It normalizes mixed proxy list formats, checks proxy egress IPs asynchronously, optionally resolves country codes, stores reusable geolocation cache, and can run extra domain availability checks through working proxies.

## Install

Create a Python 3.12 virtual environment and install dependencies with `uv`:

```bash
uv venv --python 3.12
uv sync
uv run python --version
```

To recreate the local virtual environment:

```bash
uv venv --clear --python 3.12
uv sync
```

Alternatively, install from `requirements.txt` in an existing Python 3.12 environment:

```bash
python -m pip install -r requirements.txt
```

## Usage

Proxy list files are loaded from the `proxy/` directory. For example, this command reads `proxy/my_proxies.txt`:

```bash
uv run python check_proxies.py my_proxies.txt
```

Use `--proxy-type` only as a fallback for entries without an explicit scheme. Lines that already start with `http://`, `https://`, `socks4://`, or `socks5://` keep their own scheme.

```bash
uv run python check_proxies.py my_proxies.txt --proxy-type socks5
uv run python check_proxies.py proxy.enabled.socks5.kuvalda.msk.txt
```

Run several check rounds:

```bash
uv run python check_proxies.py my_proxies.txt --iterations 3
```

Use a custom IP detection endpoint. The response may be plain text IP or JSON with `ip`, `origin`, or `query`.

```bash
uv run python check_proxies.py my_proxies.txt --check-url https://api.myip.com
uv run python check_proxies.py my_proxies.txt --check-url "https://api.ipify.org?format=json"
```

Tune concurrency and transient error retries:

```bash
uv run python check_proxies.py my_proxies.txt --max-concurrency 200 --retries 1 --retry-backoff 0.2
```

Geolocation is enabled by default. Disable it when country codes are not needed:

```bash
uv run python check_proxies.py my_proxies.txt --no-resolve-location
```

Tune geolocation lookups and cache:

```bash
uv run python check_proxies.py my_proxies.txt \
  --resolve-location \
  --geo-max-concurrency 20 \
  --geo-retries 3 \
  --geo-retry-backoff 1 \
  --geo-rps 5 \
  --geo-cache-file geo_ip_cache.json
```

Run extra checks for working proxies against domains from `domains.json`:

```bash
uv run python check_proxies.py my_proxies.txt --check-domains 10
uv run python check_proxies.py my_proxies.txt --check-domains all
uv run python check_proxies.py my_proxies.txt \
  --check-domains 20 \
  --domains-file domains.json \
  --domain-results-file domain_check_results.jsonl
```

## Options

- `--proxy-type`, `-t`: fallback proxy type for scheme-less lines: `http`, `socks4`, or `socks5`. Default: `http`.
- `--iterations`, `-i`: number of check rounds. Default: `1`.
- `--resolve-location` / `--no-resolve-location`: write country code near each detected IP. Default: enabled.
- `--check-url`: endpoint used to detect proxy egress IP. Default: `https://api.myip.com`.
- `--max-concurrency`: maximum simultaneous proxy checks. Default: `200`.
- `--retries`: additional retry attempts per proxy. Default: `1`.
- `--retry-backoff`: base retry delay in seconds. Default: `0.2`.
- `--geo-max-concurrency`: maximum simultaneous geolocation lookups. Default: `20`.
- `--geo-retries`: additional retry attempts for geolocation requests. Default: `3`.
- `--geo-retry-backoff`: base geolocation retry delay in seconds. Default: `1.0`.
- `--geo-rps`: geolocation requests per second; `0` disables throttling. Default: `5.0`.
- `--geo-cache-file`: append-only geolocation cache file. Default: `geo_ip_cache.json`.
- `--check-domains`: after the main proxy check, test working proxies against `N` random domains or `all` domains from the domains file.
- `--domains-file`: JSON file for domain checks. Default: `domains.json`.
- `--domain-results-file`: JSON Lines output for domain check results. Default: `domain_check_results.jsonl`.

## Proxy Line Formats

Supported by `check_proxies.py` and `aioproxy_check.py`:

- `http://host:port`
- `http://login:pass@host:port`
- `socks4://host:port`
- `socks5://host:port`
- `host:port`
- `host:port:login:pass`
- `login:pass:host:port`
- `login:pass@host:port`
- `host:port@login:pass`
- `socks5://host:port:login:pass`
- `socks4://host:port:login:pass`

Scheme-less entries use `--proxy-type`. Credentials are URL-encoded during normalization. Empty lines, comments, and common header rows are skipped.

## Output Files

`check_proxies.py` writes these files in the project root:

- `ok_proxies_with_ip.txt`: working proxies with all observed egress IPs and country codes when geolocation is enabled.
- `ok_proxies.txt`: working proxies only.
- `bad_proxies.txt`: proxies that never succeeded, with fail count, last error, and last check time.
- `actions.log`: detailed run log.
- `geo_ip_cache.json`: append-only IP-to-country cache when geolocation is enabled.
- `domain_check_results.jsonl`: one JSON object per proxy/domain check when `--check-domains` is used.

## Result

Example console output:

```text
Loaded 4 proxies from proxy/my_proxies.txt (default_type_for_scheme_less=http)
Check URL: https://api.myip.com
Concurrency=200, retries=1, retry_backoff=0.2s
Geo settings: concurrency=20, retries=3, retry_backoff=1.0s, rps=5.0, cache=geo_ip_cache.json
Skipped 1 non-proxy lines (headers/comments/empty).
Loaded 0 geo cache entries.
Iteration 1: 100%|##########| 4/4 [00:02<00:00,  1.80it/s]
Resolving geolocation for 2 new IPs (rate=5.00 req/s, est ~0.0 min)
Geo lookup: 100%|##########| 2/2 [00:01<00:00,  1.53it/s]
OK: http://user:pass@proxy-a.example:8080 -> IP: 203.0.113.10 (US)
OK: socks5://proxy-b.example:1080 -> IP: 198.51.100.25 (DE)
BAD: http://proxy-c.example:3128 -> TimeoutError
BAD: http://proxy-d.example:8080 -> 403, message='Forbidden', url='https://api.myip.com'
Iteration 1 summary: OK: 2 (50%) / BAD: 2 (50%)
Appended 2 geo cache entries to geo_ip_cache.json
==================================================
FINAL SUMMARY for 1 iterations:
Total checks: 4
Total OK: 2
Total BAD: 2
Success rate: 50%
Working proxies with IPs saved to ok_proxies_with_ip.txt
Working proxies only saved to ok_proxies.txt
Never-successful proxies saved to bad_proxies.txt
==================================================
```

Example `ok_proxies_with_ip.txt`:

```text
http://user:pass@proxy-a.example:8080 -> 203.0.113.10 (US)
socks5://proxy-b.example:1080 -> 198.51.100.25 (DE)
```

Example `bad_proxies.txt`:

```text
http://proxy-c.example:3128 | Fails: 1 | Last error: TimeoutError | Last check: 2026-05-08 23:15:00
http://proxy-d.example:8080 | Fails: 1 | Last error: 403, message='Forbidden', url='https://api.myip.com' | Last check: 2026-05-08 23:15:01
```

Example `domain_check_results.jsonl`:

```json
{"domain": "example.com", "message": "OK", "proxy": "http://user:pass@proxy-a.example:8080", "status": true, "status_code": 200, "url": "https://example.com"}
{"domain": "example.org", "message": "TimeoutError", "proxy": "socks5://proxy-b.example:1080", "status": false, "status_code": null, "url": "http://example.org"}
```

## Legacy Scripts

`aioproxy_check.py` is a simpler checker that prints aggregate results and supports `--proxy-type`, `--iterations`, `--resolve-location` / `--no-resolve-location`, and `--check-url`.

```bash
uv run python aioproxy_check.py my_proxies.txt
uv run python aioproxy_check.py my_proxies.txt --proxy-type socks4
uv run python aioproxy_check.py my_proxies.txt --iterations 3 --no-resolve-location
```

`aioproxy_check_forwarded.py` is a small manual script for repeatedly checking one forwarded proxy configured inside the file.

```bash
uv run python aioproxy_check_forwarded.py
```

## Tests

```bash
uv run python -m unittest discover -s tests
```

## Donates

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/osintech)
