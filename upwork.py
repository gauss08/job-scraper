import asyncio
import json
import re
import sys
import argparse
from datetime import datetime
from urllib.parse import quote_plus
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn,TimeElapsedColumn

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page
)



# ---------------------------------------------------------------------------
# Look-up tables
# ---------------------------------------------------------------------------


EXPERIENCE={
    "Any":"",
    "Entry Leve":"1",
    "Intermediate":"2",
    "Expert":"3"
}

SORT_BY={
    "Relevance":"relevance",
    "Newest":"recency"
}

CLIENT_HISTORY={
    "Any":"",
    "No hires":"0",
    "1 to 9 hires":"1-9",
    "10+ hires":"10-"
}

PROJECT_LENGTH={
    "Any":"",
    "Less than one month":"week",
    "1 to 3 months":"month",
    "3 to 6 months":"semester",
    "More than 6 months":"ongoing"
}

HOURS_PER_WEEK={
    "Any":"",
    "Less than 30 hrs/week":"as_needed",
    "More than 30 hrs/week":"full_time"
}


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def build_search_url(
    keywords: str,
    location: str = "",
    experience: list[int] | None = None,
    work_type: list[int] | None = None,
    work_mode: list[int] | None = None,
    employment_type: list[int] | None = None,
    salary: str = "",
    sort_by: int | None = None,
    radius: str = "",
) -> str:
    pass






# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

async def _dismiss_modal(page):
    """Attempt to close cookie banners or sign-in modals.
 
    Tries a small set of known CSS selectors, then falls back to pressing
    ``Escape``.  Failures are silently swallowed — callers should not depend
    on this function succeeding.
 
    Parameters
    ----------
    page:
        The Playwright :class:`~playwright.async_api.Page` to operate on.
    """


    MODAL_DISMISS_SELECTORS = [
    # Generic dismiss buttons
    'button.onetrust-close-btn-handler.banner-close-button'
    ]

    for sel in MODAL_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                await page.wait_for_timeout(300)
                return
        except Exception:
            pass

    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except Exception:
        pass