"""Unit tests for georisk_agent.news.source_filter."""

import pytest
from georisk_agent.news.source_filter import is_blocked_url


@pytest.mark.parametrize("url", [
    "https://www.facebook.com/etnow/posts/geopolitical-tensions/1340889624736605",
    "https://m.facebook.com/somepost",
    "https://www.youtube.com/watch?v=W8Z7qbXpS3s",
    "https://youtu.be/W8Z7qbXpS3s",
    "https://www.instagram.com/p/abc123/",
    "https://twitter.com/user/status/123",
    "https://x.com/user/status/123",
    "https://t.me/somechannel",
    "https://www.reddit.com/r/investing/comments/abc/",
    "https://www.tiktok.com/@user/video/123",
    "https://rt.com/news/some-article",
    "https://sputniknews.com/world/article",
    "https://zerohedge.com/markets/article",
    "https://coindesk.com/markets/btc",
])
def test_blocked_urls(url: str) -> None:
    assert is_blocked_url(url), f"Expected {url!r} to be blocked"


@pytest.mark.parametrize("url", [
    "https://www.reuters.com/world/article",
    "https://www.ft.com/content/article",
    "https://www.imf.org/en/blogs/articles/2026/03/30/article",
    "https://www.worldbank.org/en/publication/global-economic-prospects",
    "https://www.brookings.edu/research/article",
    "https://www.rand.org/pubs/research_reports/article.html",
    "https://finance.yahoo.com/news/defense-etfs-gain-140500272.html",
    "https://www.atlanticcouncil.org/in-depth-research-reports/report/article",
    "https://hanetf.com/monthly-reports/defence-etf-report-april-2026",
    "https://www.oecd.org/en/publications/2026/04/article.html",
])
def test_allowed_urls(url: str) -> None:
    assert not is_blocked_url(url), f"Expected {url!r} to be allowed"


def test_empty_url_not_blocked() -> None:
    assert not is_blocked_url("")


def test_malformed_url_not_blocked() -> None:
    assert not is_blocked_url("not-a-url")
