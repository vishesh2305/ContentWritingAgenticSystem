"""Source hygiene: canonical URLs, near-duplicate detection, credibility scoring."""

from collections.abc import Sequence
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "msclkid", "dclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src"}
)
DEFAULT_PORTS = {"http": 80, "https": 443}

# Institutional and reference sites; .gov/.edu and docs.* hosts are matched by pattern.
TRUSTED_DOMAINS = frozenset(
    {
        "wikipedia.org",
        "github.com",
        "arxiv.org",
        "nature.com",
        "acm.org",
        "ieee.org",
        "who.int",
        "w3.org",
        "mozilla.org",
        "python.org",
        "postgresql.org",
    }
)
LOW_QUALITY_DOMAINS = frozenset(
    {"pinterest.com", "quora.com", "answers.com", "ehow.com", "scribd.com"}
)

SHINGLE_WORDS = 5
NEAR_DUPLICATE_JACCARD = 0.8
RECENT_DAYS = 2 * 365
STALE_DAYS = 5 * 365
THIN_PAGE_WORDS = 400


def canonical_url(url: str) -> str:
    """Normalise a URL so the same page found via different links gets one key."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().removeprefix("www.")
    if ":" in host:
        host = f"[{host}]"
    if parts.port and parts.port != DEFAULT_PORTS.get(scheme):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
        )
    )
    return urlunsplit((scheme, host, path, query, ""))


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = text.lower().split()
    if len(words) < SHINGLE_WORDS:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def _jaccard(a: set[tuple[str, ...]], b: set[tuple[str, ...]]) -> float:
    return len(a & b) / len(a | b)


def find_near_duplicates(texts: Sequence[str]) -> set[int]:
    """Indexes of texts that near-duplicate an earlier one; the first copy is kept."""
    kept: list[set[tuple[str, ...]]] = []
    duplicates: set[int] = set()
    for index, text in enumerate(texts):
        shingles = _shingles(text)
        if not shingles:
            continue
        if any(_jaccard(shingles, other) >= NEAR_DUPLICATE_JACCARD for other in kept):
            duplicates.add(index)
        else:
            kept.append(shingles)
    return duplicates


def _matches(host: str, domains: frozenset[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def credibility(
    url: str,
    *,
    author: str | None,
    published: date | None,
    word_count: int,
    today: date,
) -> float:
    """Heuristic 0-1 trust score used to rank non-competitor sources."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    score = 0.5
    institutional = host.endswith((".gov", ".edu")) or ".gov." in host or ".edu." in host
    if institutional or _matches(host, TRUSTED_DOMAINS) or host.startswith("docs."):
        score += 0.25
    if _matches(host, LOW_QUALITY_DOMAINS):
        score -= 0.3
    if parts.scheme == "https":
        score += 0.05
    if author:
        score += 0.1
    if published is not None:
        age_days = (today - published).days
        if age_days <= RECENT_DAYS:
            score += 0.1
        elif age_days > STALE_DAYS:
            score -= 0.1
    if word_count < THIN_PAGE_WORDS:
        score -= 0.2
    return round(min(1.0, max(0.0, score)), 3)
