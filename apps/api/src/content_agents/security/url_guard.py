"""SSRF guard: the crawler may only fetch public http(s) addresses (Plan.md §11 Step 3)."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], Awaitable[list[str]]]


class UnsafeURLError(ValueError):
    pass


async def system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


async def ensure_public_url(url: str, *, resolve: Resolver = system_resolve) -> None:
    """Raise UnsafeURLError unless every address the URL's host maps to is public.

    Check again on every redirect hop. DNS can still change between this check and
    the request (rebinding); the per-hop check limits but does not remove that window.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"scheme not allowed: {url}")
    if parts.username or parts.password:
        raise UnsafeURLError(f"credentials in URL are not allowed: {url}")
    host = parts.hostname
    if not host:
        raise UnsafeURLError(f"no host in URL: {url}")

    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await resolve(host)
        except OSError as e:
            raise UnsafeURLError(f"cannot resolve {host}: {e}") from e

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast:
            raise UnsafeURLError(f"{host} maps to non-public address {address}")
