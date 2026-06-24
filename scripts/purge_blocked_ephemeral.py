"""
One-off maintenance script: purge ephemeral_embeddings rows whose URL belongs
to a now-blocked domain (social media, video platforms, UGC).

Run once after deploying the source_filter blocklist extension to remove
poisoned entries that were cached before the filter was applied.

Usage:
    python scripts/purge_blocked_ephemeral.py
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    from georisk_agent.db.client import get_session
    from georisk_agent.db.dal import delete_ephemeral_by_domain
    from georisk_agent.news.source_filter import BLOCKED_DOMAINS

    domains = sorted(BLOCKED_DOMAINS)
    logger.info("Purging ephemeral cache entries for %d blocked domains …", len(domains))

    async with get_session() as session:
        count = await delete_ephemeral_by_domain(session, domains)
        await session.commit()

    logger.info("Done — %d row(s) deleted.", count)


if __name__ == "__main__":
    asyncio.run(main())
