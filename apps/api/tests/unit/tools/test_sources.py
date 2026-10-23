from datetime import date

import pytest

from content_agents.tools.sources import canonical_url, credibility, find_near_duplicates

TODAY = date(2026, 10, 22)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://Example.COM/Post", "https://example.com/Post"),
        ("https://www.example.com/post/", "https://example.com/post"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com:443/post", "https://example.com/post"),
        ("https://example.com/post#section-2", "https://example.com/post"),
        (
            "https://example.com/post?utm_source=x&b=2&fbclid=y&a=1&gclid=z&ref=hn",
            "https://example.com/post?a=1&b=2",
        ),
    ],
)
def test_canonical_url(url: str, expected: str) -> None:
    assert canonical_url(url) == expected


def paragraph(seed: str, n: int = 120) -> str:
    return " ".join(f"{seed}{i}" for i in range(n))


def test_identical_texts_keep_only_the_first() -> None:
    text = paragraph("w")
    assert find_near_duplicates([text, paragraph("other"), text]) == {2}


def test_lightly_edited_copy_is_a_near_duplicate() -> None:
    original = paragraph("w", 300)
    copy = original.replace("w150", "changed").replace("w151", "words")
    assert find_near_duplicates([original, copy]) == {1}


def test_different_texts_are_kept() -> None:
    assert find_near_duplicates([paragraph("a"), paragraph("b"), paragraph("c")]) == set()


def test_empty_texts_are_ignored() -> None:
    assert find_near_duplicates(["", ""]) == set()


def score(
    url: str,
    *,
    author: str | None = None,
    published: date | None = None,
    word_count: int = 1500,
) -> float:
    return credibility(url, author=author, published=published, word_count=word_count, today=TODAY)


def test_credibility_is_between_0_and_1() -> None:
    best = score("https://nih.gov/x", author="A", published=date(2026, 9, 1))
    worst = score("http://pinterest.com/x", word_count=50, published=date(2010, 1, 1))
    assert 0.0 <= worst < best <= 1.0


def test_institutional_and_reference_domains_rank_higher() -> None:
    generic = score("https://someblog.example/post")
    assert score("https://cdc.gov/post") > generic
    assert score("https://mit.edu/post") > generic
    assert score("https://en.wikipedia.org/wiki/X") > generic
    assert score("https://docs.python.org/3/") > generic


def test_low_quality_domains_rank_lower() -> None:
    assert score("https://www.quora.com/q") < score("https://someblog.example/post")


def test_author_and_recent_date_raise_credibility() -> None:
    base = score("https://someblog.example/post")
    assert score("https://someblog.example/post", author="Jane Doe") > base
    assert score("https://someblog.example/post", published=date(2026, 5, 1)) > base


def test_old_or_thin_pages_lose_credibility() -> None:
    base = score("https://someblog.example/post")
    assert score("https://someblog.example/post", published=date(2015, 1, 1)) < base
    assert score("https://someblog.example/post", word_count=250) < base
