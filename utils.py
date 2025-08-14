import aiohttp
import certifi
import ssl


ssl_ctx = ssl.create_default_context(cafile=certifi.where())


async def _get_starship(proxy: str):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url=f'https://api.ipify.org?format=json', proxy=proxy, timeout=5) as response:
                return {"status": True, "message": await response.json(), "proxy": proxy}
        except Exception as e:
            return {"status": False, "message": e, "proxy": proxy}


async def get_starship(proxy: str):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                    url='https://api.ipify.org?format=json',
                    proxy=proxy,
                    ssl=ssl_ctx,
                    timeout=5
            ) as response:
                return {"status": True, "message": await response.json(), "proxy": proxy}
        except Exception as e:
            return {"status": False, "message": e, "proxy": proxy}
