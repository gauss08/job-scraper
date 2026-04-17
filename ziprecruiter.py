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

DATE_FILTER = {
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

EMPLOYMENT_TYPE = {
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
    date_filter: str = "",
    apply_type: str = "",
    experience: list[int] | None = None,
    work_mode: list[int] | None = None,
    employment_type: str = "",
    salary_floor: str = "",
    salary_ceil: str = "",
    radius: str = "",
) -> str:

    params={}

    params["search"]=keywords.strip()
    params["location"]=location.strip() if location else "USA"

    if experience:
        params["refine_by_experience_level"] = ",".join(EXPERIENCE[e] for e in experience)

    if date_filter:
        params["days"] = str(DATE_FILTER[date_filter])
    
    if work_mode:
        params["refine_by_location_type"] = WORK_MODE[work_mode]
 
    if employment_type:
        params["refine_by_employment"] = f"employment_type:{EMPLOYMENT_TYPE[employment_type]}"

    if apply_type:
        params["refine_by_apply_type"] = APPLY_TYPE[apply_type]
 
    params["radius"] = str(radius) if radius else "5000"

    if salary_floor:
        params["refine_by_salary"]=salary_floor
    
    if salary_ceil:
        params["refine_by_salary"]=salary_ceil

    base='https://www.ziprecruiter.com/jobs-search?'
    query="&".join(f"{k}={quote_plus(v)}" for k, v in params.items())

    return f"{base}{query}"


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _prompt_multi(label: str, mapping: dict, single: bool = True) -> list | None:
    """Print a numbered menu and return a list of selected integer keys.
 
    The user may type a single number (``2``) or a comma-separated list
    (``1,3``).  An empty input is treated as *no selection* (returns
    ``None``).
 
    Parameters
    ----------
    label:
        Short descriptive name shown before the menu.
    mapping:
        One of the module-level look-up tables (e.g. :data:`EXPERIENCE`).
 
    Returns
    -------
    list[int] or None
    """
    temp={}

    print(f"\n{label}:")
    for k, v in enumerate(mapping.keys(),1):
        temp[k]=v
        print(f"  {k} : {v}")
    raw = input(f"{label} (comma-separated, or blank to skip): ").strip()
    if not raw:
        return None
    try:
        if single:
            return temp[int(raw)]
        else:
            return [temp[int(x)] for x in raw.split(",") if x.strip()]
    except ValueError:
        print(f"Could not parse '{raw}' — skipping {label} filter.")
        return None



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run_interactive() -> None:
    """Prompt-driven session used when no CLI arguments are supplied."""
    keywords = input("Keywords: ").strip()
    location = input("Location (blank = USA): ").strip()

    date_filter = _prompt_multi("Date Filter", DATE_FILTER)
    apply_type = _prompt_multi("Apply Type", APPLY_TYPE)
 
    experience = _prompt_multi("Experience level", EXPERIENCE, False)
    employment_type = _prompt_multi("Employment type", EMPLOYMENT_TYPE)
    work_mode = _prompt_multi("Work mode", WORK_MODE)
 
    salary_floor = input("\nMin salary ? (blank = no floor salary): ").strip()
    salary_ceil = input("\nMax salary ? (blank = no ceil salary): ").strip()
 
    radius = input("Radius in km (blank to skip): ").strip()

    #max_raw = input("\nMax results [default 10]: ").strip()
    #max_results = int(max_raw) if max_raw.isdigit() else 10

    url=build_search_url(
        keywords=keywords,
        location=location,
        date_filter=date_filter,
        apply_type=apply_type,
        experience=experience,
        employment_type=employment_type,
        work_mode=work_mode,
        salary_floor=salary_floor,
        salary_ceil=salary_ceil,
        radius=radius,
    )

    print(f"Full URL : {url}")


if __name__=="__main__":
    asyncio.run(_run_interactive())