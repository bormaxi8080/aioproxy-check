import asyncio
from utils import get_starship


async def main():
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
            try:
                if 'ip' in result['message'].keys():
                    print("{}: {}: {}".format("OK", result['proxy'], result['message']))
                    oks += 1
                else:
                    print("{}: {}: {}".format("BAD", result['proxy'], result['message']))
                    bads += 1
            except Exception:
                print("{}: {}: {}".format("BAD", result['proxy'], result['message']))
                bads += 1
        else:
            print("{}: {}: {}".format("BAD", result['proxy'], result['message']))
            bads += 1

    print("OKS: {}({}%) / BADS: {}({}%)".format(oks, round(oks / len(proxy_list) * 100), bads,
                                                round(bads / len(proxy_list) * 100)))


# Not used in Python 3.10 or later
# asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

asyncio.run(main())
