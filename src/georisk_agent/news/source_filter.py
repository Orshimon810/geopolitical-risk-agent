"""
Shared source-quality filter for all news and web-search paths.

`is_blocked_url(url)` is the single authority — called before persisting
any URL from the news fetcher, Tavily results, or ephemeral retrieval.
"""

from urllib.parse import urlparse

BLOCKED_DOMAINS: frozenset[str] = frozenset({
    # State media / propaganda
    "rt.com",
    "sputniknews.com",
    "tass.com",
    # Documented misinformation
    "globalresearch.ca",
    "naturalnews.com",
    "infowars.com",
    "beforeitsnews.com",
    "veteranstoday.com",
    "thegatewaypundit.com",
    "breitbart.com",
    # Sensationalist / inaccurate financial content
    "zerohedge.com",
    # Crypto-only outlets — not relevant for geopolitical analysis
    "beincrypto.com",
    "cryptobriefing.com",
    "coindesk.com",
    "cointelegraph.com",
    "decrypt.co",
    # Social media / UGC — not credible for institutional investment analysis
    "facebook.com",
    "m.facebook.com",
    "fb.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "t.me",
    "telegram.org",
    # Video platforms
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    # Unreviewed community content
    "reddit.com",
})


def _domain(url: str) -> str:
    """Extract bare domain (no www.) from a URL."""
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def is_blocked_url(url: str) -> bool:
    """Return True if the URL should be excluded from analysis sources."""
    d = _domain(url)
    if not d:
        return False
    return any(d == blocked or d.endswith("." + blocked) for blocked in BLOCKED_DOMAINS)
