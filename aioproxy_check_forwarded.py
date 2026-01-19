"""Check a forwarded proxy by dispatching multiple async requests."""

import asyncio

from utils import get_starship


async def main():
    """Run proxy checks and print aggregate results."""
    tasks = []
    proxy_list = []
    oks = 0
    bads = 0

    forwarded_proxy = 'http://login:password@host:port'

    retries_count = 500
    counter = 0

    while counter < retries_count:
        proxy_list.append(forwarded_proxy)
        counter += 1

    for proxy in proxy_list:
        tasks.append(get_starship(proxy))
        counter += 1

    results = await asyncio.gather(*tasks)
    for result in results:
        if result['status']:
            message = result.get('message')
            if isinstance(message, dict) and 'ip' in message:
                print(f"OK: {result['proxy']}: {message}")
                oks += 1
            else:
                print(f"BAD: {result['proxy']}: {message}")
                bads += 1
        else:
            print(f"BAD: {result['proxy']}: {result['message']}")
            bads += 1

    oks_percent = round(oks / len(proxy_list) * 100)
    bads_percent = round(bads / len(proxy_list) * 100)
    print(f"OKS: {oks}({oks_percent}%) / BADS: {bads}({bads_percent}%)")


# Not used in Python 3.10 or later
# asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

asyncio.run(main())
