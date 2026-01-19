"""Async helpers for proxy IP checks."""

import asyncio
import ssl

import aiohttp
import certifi


ssl_ctx = ssl.create_default_context(cafile=certifi.where())


async def _get_starship(proxy: str):
    """Fetch the caller's IP via the proxy using a default SSL context."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                    url='https://api.ipify.org?format=json',
                    proxy=proxy,
                    timeout=5
            ) as response:
                return {
                    "status": True,
                    "message": await response.json(),
                    "proxy": proxy,
                }
        except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            return {"status": False, "message": e, "proxy": proxy}


async def get_starship(proxy: str):
    """Fetch the caller's IP via the proxy using the shared SSL context."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                    url='https://api.ipify.org?format=json',
                    proxy=proxy,
                    ssl=ssl_ctx,
                    timeout=5
            ) as response:
                return {
                    "status": True,
                    "message": await response.json(),
                    "proxy": proxy,
                }
        except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            return {"status": False, "message": e, "proxy": proxy}
