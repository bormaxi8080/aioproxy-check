import aiohttp
import asyncio


async def get_starship(proxy: str):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url=f'https://api.ipify.org?format=json', proxy=proxy, timeout=5) as response:
                return {"status": True, "message": await response.json(), "proxy": proxy}
        except Exception as e:
            return {"status": False, "message": e, "proxy": proxy}


async def main():
    tasks = []
    proxy_list = []
    oks = 0
    bads = 0

    with open("proxy_list.txt", "r") as proxy_file:  # real: proxies.txt
        for line in proxy_file:
            proxy_list.append(line.strip())

    for proxy in proxy_list:
        tasks.append(get_starship(proxy))

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
