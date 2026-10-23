import pytest

from content_agents.security.url_guard import UnsafeURLError, ensure_public_url

DNS = {
    "example.com": ["93.184.216.34"],
    "internal.example.com": ["10.0.0.7"],
    "mixed.example.com": ["93.184.216.34", "192.168.1.20"],
    "v6.example.com": ["2606:2800:220:1:248:1893:25c8:1946"],
}


async def fake_resolve(host: str) -> list[str]:
    if host not in DNS:
        raise OSError(f"cannot resolve {host}")
    return DNS[host]


async def test_public_https_url_is_allowed() -> None:
    await ensure_public_url("https://example.com/post", resolve=fake_resolve)


async def test_public_ipv6_host_is_allowed() -> None:
    await ensure_public_url("https://v6.example.com/", resolve=fake_resolve)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https:///no-host",
        "https://user:pass@example.com/",
    ],
)
async def test_non_web_or_malformed_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        await ensure_public_url(url, resolve=fake_resolve)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://224.0.0.1/",
    ],
)
async def test_private_and_special_ip_literals_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        await ensure_public_url(url, resolve=fake_resolve)


async def test_hostname_resolving_to_private_ip_is_rejected() -> None:
    with pytest.raises(UnsafeURLError, match=r"10\.0\.0\.7"):
        await ensure_public_url("https://internal.example.com/", resolve=fake_resolve)


async def test_hostname_with_any_private_address_is_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        await ensure_public_url("https://mixed.example.com/", resolve=fake_resolve)


async def test_unresolvable_host_is_rejected() -> None:
    with pytest.raises(UnsafeURLError, match="resolve"):
        await ensure_public_url("https://nowhere.invalid/", resolve=fake_resolve)
