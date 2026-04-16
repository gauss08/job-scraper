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


APPLY_TYPE = {
    "All apply types":"",
    "Quick apply only":"has_zipapply",
}

WORK_MODE = {
    "All remote/on-site":"",
    "Remote": "only_remote",
    "Hybrid": "hybrid",
    "On-site": "no_remote",
    "Any":"",
}

DATE_FILTERS = {
    "Within 1 day":1,
    "Within 5 days":5,
    "Within 10 days":10,
    "Within 30 days":30,
    "Posted anytime":"",
}

EXPERIENCE = {
    "No experience needed":"no_experience",
    "Junior":"junior",
    "Middle":"mid",
    "Senior level and above":"senior",
    "Any":"",
}

EMPLOYMENT_TYPES = {
    "Full Time":"full_time",
    "Part Time":"part_time",
    "Contract":"contract",
    "Per Diem":"as_needed",
    "Temporary":"temporary",
    "Other":"other",
    "Any":"",
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