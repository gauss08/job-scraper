"""
ziprecruiter_scraper.py
=======================
Interactive CLI tool that scrapes job listings from ZipRecruiter using
Playwright (persistent Chromium context) and saves results to a JSON file.

Usage
-----
    python ziprecruiter_scraper.py                  # interactive prompts
    python ziprecruiter_scraper.py --help           # argument reference

The script prompts for search parameters interactively (or accepts them via
CLI flags), builds a search URL, then navigates each result page, clicks each
job card to open the detail pane, and collects structured job data.

Jobs are written to the output file incrementally so that a partial run is
never lost.

Dependencies
------------
    pip install playwright rich
    playwright install chromium

Typical output  (ziprecruiter_jobs_20240501_143022.json)
--------------------------------------------------------
    [
      {
        "job_url": "https://www.ziprecruiter.com/...",
        "job_title": "Software Engineer",
        "company_name": "Acme Corp",
        "location": "Remote",
        "info": "...",
        "job_description": "..."
      },
      ...
    ]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import random
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit, urlunsplit

from playwright.async_api import Page, async_playwright
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Number of job cards ZipRecruiter returns per full page.
RESULTS_PER_PAGE: int = 20

#: How long (ms) to wait for a modal element to become visible before giving up.
VISIBILITY_TIMEOUT_MS: int = 3_500

#: Random delay range (seconds) applied after each page navigation.
POST_NAV_DELAY_S: tuple[float, float] = (3.2, 4.5)

#: Random delay range (seconds) applied after each card click.
POST_CLICK_DELAY_S: tuple[float, float] = (2.0, 3.5)

#: Selectors used to find and dismiss cookie / sign-in modals.
MODAL_DISMISS_SELECTORS: tuple[str, ...] = (
    "button._r_1k_",
    "#_r_13_",
)

#: Base URL for ZipRecruiter job search (no query string).
_ZIPRECRUITER_BASE: str = "https://www.ziprecruiter.com/jobs-search"

# ---------------------------------------------------------------------------
# Look-up tables
# ---------------------------------------------------------------------------
# Each dict maps a human-readable CLI label to the query-parameter value
# expected by ZipRecruiter's search API.  Using ``str`` values throughout
# keeps the URL builder simple; DATE_FILTER is the only exception because its
# values are integers (day counts).

APPLY_TYPE: dict[str, str] = {
    "All apply types": "",
    "Quick apply only": "has_zipapply",
}

WORK_MODE: dict[str, str] = {
    "All remote/on-site": "",
    "Remote": "only_remote",
    "Hybrid": "hybrid",
    "On-site": "no_remote",
    "Any": "",
}

#: Maps label → number of days, or ``""`` for "no filter".
DATE_FILTER: dict[str, int | str] = {
    "Within 1 day": 1,
    "Within 5 days": 5,
    "Within 10 days": 10,
    "Within 30 days": 30,
    "Posted anytime": "",
}

EXPERIENCE: dict[str, str] = {
    "No experience needed": "no_experience",
    "Junior": "junior",
    "Middle": "mid",
    "Senior level and above": "senior",
    "Any": "",
}

EMPLOYMENT_TYPE: dict[str, str] = {
    "Full Time": "full_time",
    "Part Time": "part_time",
    "Contract": "contract",
    "Per Diem": "as_needed",
    "Temporary": "temporary",
    "Other": "other",
    "Any": "",
}

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: A single scraped job record.
JobRecord = dict[str, str]

# ---------------------------------------------------------------------------
# Platform helper
# ---------------------------------------------------------------------------


def _default_chrome_profile() -> Path:
    """Return the OS-appropriate path to the user's default Chrome profile.

    Supports Linux, macOS, and Windows.  Falls back to the Linux XDG path on
    unrecognised platforms.

    Returns
    -------
    Path
        Absolute path to the Chrome user-data directory.
    """
    system = platform.system()
    if system == "Darwin":
        return (
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        )
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        return Path(local_app_data) / "Google" / "Chrome" / "User Data"
    # Linux — and any other POSIX platform as a safe default.
    return Path.home() / ".config" / "google-chrome"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def build_search_url(
    keywords: str,
    location: str = "",
    date_filter: str = "",
    apply_type: str = "",
    experience: list[str] | None = None,
    work_mode: str | None = None,
    employment_type: str = "",
    salary_floor: str = "",
    salary_ceil: str = "",
    radius: str = "",
) -> str:
    """Construct a ZipRecruiter job-search URL from filter parameters.

    Only parameters with non-empty / non-None values are included in the
    query string; inactive filters are silently omitted.

    Parameters
    ----------
    keywords:
        Free-text job search terms, e.g. ``"Python developer"``.
    location:
        City, state, or ZIP code.  Defaults to ``"USA"`` when blank.
    date_filter:
        A key from :data:`DATE_FILTER`, e.g. ``"Within 5 days"``.
        Pass ``""`` to omit this filter.
    apply_type:
        A key from :data:`APPLY_TYPE`.  Pass ``""`` to omit.
    experience:
        List of keys from :data:`EXPERIENCE` (multi-select allowed).
        Pass ``None`` or ``[]`` to omit.
    work_mode:
        A key from :data:`WORK_MODE`.  Pass ``None`` or ``""`` to omit.
    employment_type:
        A key from :data:`EMPLOYMENT_TYPE`.  Pass ``""`` to omit.
    salary_floor:
        Minimum annual salary as a numeric string, e.g. ``"60000"``.
    salary_ceil:
        Maximum annual salary as a numeric string.
    radius:
        Search radius in kilometres.  Defaults to ``"5000"`` (nationwide).

    Returns
    -------
    str
        Fully-formed ZipRecruiter search URL with all active filters applied.

    Examples
    --------
    >>> url = build_search_url("data engineer", location="New York",
    ...                        work_mode="Remote")
    >>> url.startswith("https://www.ziprecruiter.com/jobs-search?")
    True
    """
    params: dict[str, str] = {}

    params["search"] = keywords.strip()
    params["location"] = location.strip() or "USA"

    if experience:
        params["refine_by_experience_level"] = ",".join(
            EXPERIENCE[e] for e in experience
        )

    if date_filter:
        params["days"] = str(DATE_FILTER[date_filter])

    if work_mode:
        params["refine_by_location_type"] = WORK_MODE[work_mode]

    if employment_type:
        params["refine_by_employment"] = (
            f"employment_type:{EMPLOYMENT_TYPE[employment_type]}"
        )

    if apply_type:
        params["refine_by_apply_type"] = APPLY_TYPE[apply_type]

    params["radius"] = radius or "5000"

    if salary_floor:
        params["refine_by_salary"] = salary_floor

    if salary_ceil:
        params["refine_by_salary_ceil"] = salary_ceil

    query = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
    return f"{_ZIPRECRUITER_BASE}?{query}"


def _paginate_url(base_url: str, page_num: int) -> str:
    """Return *base_url* with a page-number segment inserted into the path.

    ZipRecruiter paginates by inserting a numeric segment before the query
    string, e.g. ``/jobs-search/2?...``.  Using :mod:`urllib.parse` rather
    than ``str.split("?")`` ensures correctness when query-string values
    themselves contain encoded ``%3F`` characters.

    Parameters
    ----------
    base_url:
        The base search URL with no page segment in the path.
    page_num:
        1-based page index to navigate to.

    Returns
    -------
    str
        URL with the page number segment appended to the path component.
    """
    parts = urlsplit(base_url)
    new_path = parts.path.rstrip("/") + f"/{page_num}"
    return urlunsplit(parts._replace(path=new_path))


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _jitter(delay_range: tuple[float, float]) -> None:
    """Sleep for a uniformly random duration within *delay_range*.

    Randomising inter-action delays makes the scraper's traffic pattern less
    mechanical and harder for bot-detection systems to fingerprint.

    Parameters
    ----------
    delay_range:
        ``(min_seconds, max_seconds)`` pair.  Both values must be non-negative
        and ``min_seconds`` must be ≤ ``max_seconds``.
    """
    await asyncio.sleep(random.uniform(*delay_range))


async def _dismiss_modal(page: Page) -> None:
    """Attempt to close cookie banners or sign-in modals.

    Iterates over :data:`MODAL_DISMISS_SELECTORS` and clicks the first visible
    match.  Falls back to pressing ``Escape`` if no selector matches.  All
    failures are caught and logged at DEBUG level — callers must not rely on
    this function succeeding.

    Parameters
    ----------
    page:
        The Playwright :class:`~playwright.async_api.Page` to operate on.
    """
    for sel in MODAL_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=VISIBILITY_TIMEOUT_MS):
                await btn.click()
                await _jitter((0.8, 1.5))
                return
        except Exception as exc:  # noqa: BLE001
            logger.debug("Modal selector %r did not match: %s", sel, exc)

    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Escape key press failed: %s", exc)


async def _read_details(page: Page) -> JobRecord:
    """Extract job details from the currently-open ZipRecruiter detail pane.

    The caller must have already clicked a job card so that the right-hand
    detail panel is visible.  Each field is extracted independently; a failed
    locator sets that field to ``""`` rather than aborting the whole record,
    making the scraper resilient to incremental ZipRecruiter DOM changes.

    Parameters
    ----------
    page:
        Active Playwright page with a job detail panel open.

    Returns
    -------
    JobRecord
        Dictionary with the following ``str``-valued keys:

        * ``"job_url"``      — Absolute URL of the individual listing.
        * ``"job_title"``    — Job title from the panel heading.
        * ``"company_name"`` — Hiring company name.
        * ``"location"``     — Location string (city/state or ``"Remote"``).
        * ``"info"``         — Short blurb (pay range, hours, benefits).
        * ``"job_description"`` — Full job description text.
    """
    record: JobRecord = {
        "job_url": "",
        "job_title": "",
        "company_name": "",
        "location": "",
        "info": "",
        "job_description": "",
    }

    # job_url — normalise to absolute form regardless of href shape
    try:
        href: str | None = await (
            page.locator(
                "div.flex.w-full.flex-row.justify-between a.inline-flex"
            ).first.get_attribute("href")
        )
        if href:
            record["job_url"] = (
                href
                if href.startswith("http")
                else f"https://www.ziprecruiter.com{href}"
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not extract job_url: %s", exc)

    _selectors: dict[str, str] = {
        "job_title":      "div.w-full div.grid h2.font-bold",
        "company_name":   "div.w-full div.grid a",
        "location":       "div.w-full div.grid div.mb-24",
        "info":           "div.w-full div.flex.flex-col.gap-y-8",
        "job_description": r"div.w-full div.flex.flex-col.gap-y-\[16px\]",
    }

    for field, selector in _selectors.items():
        try:
            record[field] = await page.locator(selector).first.inner_text()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not extract %r using %r: %s", field, selector, exc)

    return record


# ---------------------------------------------------------------------------
# Incremental writer
# ---------------------------------------------------------------------------


class _IncrementalWriter:
    """Persist job records to a JSON array file, one record at a time.

    Each :meth:`append` call serialises the full in-memory list to a
    temporary file in the same directory, then atomically replaces the
    output file via :func:`os.replace`.  The output file is therefore always
    valid JSON; a crash between writes loses at most the single record being
    appended.

    Parameters
    ----------
    output_path:
        Destination ``.json`` file.  The parent directory must already exist.

    Examples
    --------
    >>> writer = _IncrementalWriter(Path("jobs.json"))
    >>> writer.append({"job_title": "Engineer", "company_name": "Acme"})
    >>> writer.count
    1
    """

    def __init__(self, output_path: Path) -> None:
        self._path: Path = output_path
        self._records: list[JobRecord] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def append(self, record: JobRecord) -> None:
        """Append *record* to the list and flush to disk atomically."""
        self._records.append(record)
        self._flush()

    def close(self) -> None:
        """No-op — present for symmetry and potential future cleanup."""

    @property
    def count(self) -> int:
        """Number of records written so far."""
        return len(self._records)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Serialise :attr:`_records` and atomically replace the output file.

        Raises
        ------
        OSError
            Propagated from :func:`json.dump` or :func:`os.replace` on I/O
            failure.  The temporary file is cleaned up before re-raising.
        """
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent, suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(self._records, fh, indent=2, ensure_ascii=False)
            Path(tmp_name).replace(self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


async def scrape_jobs(
    base_url: str,
    max_results: int = 25,
    output_path: Path | None = None,
    chrome_profile: Path | None = None,
) -> list[JobRecord]:
    """Scrape job listings from a ZipRecruiter search URL.

    Opens a persistent Chromium context (reusing the local Chrome profile to
    minimise bot-detection risk), paginates through results, clicks each card
    to load the detail pane, and collects structured records via
    :func:`_read_details`.  When *output_path* is given, each record is
    written to disk immediately via :class:`_IncrementalWriter`.

    Parameters
    ----------
    base_url:
        ZipRecruiter search URL as produced by :func:`build_search_url`.
        Page numbers are injected automatically by :func:`_paginate_url`.
    max_results:
        Upper bound on records to collect.  The actual count may be lower if
        fewer results exist.
    output_path:
        Destination JSON file for incremental writes.  Pass ``None`` to
        return results only in memory without writing any file.
    chrome_profile:
        Chrome user-data directory.  Defaults to the OS-appropriate path
        returned by :func:`_default_chrome_profile` when ``None``.

    Returns
    -------
    list[JobRecord]
        Collected job records, possibly fewer than *max_results*.

    Raises
    ------
    playwright.async_api.Error
        Propagated from Playwright if the browser cannot be launched.

    Notes
    -----
    * Reusing a real Chrome profile gives ZipRecruiter's bot-detection the
      appearance of a normal, previously-used browser with cookies and history.
    * ``--disable-blink-features=AutomationControlled`` combined with removing
      ``--enable-automation`` suppresses the "automated software" banner.
    * All navigation and click actions include randomised jitter delays
      (see :func:`_jitter`) to mimic human pacing.
    """
    profile_dir = chrome_profile or _default_chrome_profile()
    writer = _IncrementalWriter(output_path) if output_path is not None else None
    jobs: list[JobRecord] = []

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            channel="chromium",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )

        page = await context.new_page()
        page_num = 1

        try:
            with Progress(
                TextColumn("[bold green]{task.description}"),
                BarColumn(),
                TextColumn("[bold yellow]{task.completed}/{task.total} done"),
                TimeElapsedColumn(),
            ) as progress:
                task = progress.add_task("Collecting jobs", total=max_results)

                while len(jobs) < max_results:
                    url = _paginate_url(base_url, page_num)
                    logger.debug("Navigating to %s", url)

                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=20_000
                    )
                    await _jitter(POST_NAV_DELAY_S)
                    await _dismiss_modal(page)

                    cards = await page.locator(
                        "div.job_result_two_pane_v2"
                    ).all()

                    if not cards:
                        logger.info(
                            "No cards found on page %d — stopping.", page_num
                        )
                        break

                    for idx, card in enumerate(cards, start=1):
                        await card.click()
                        await _jitter(POST_CLICK_DELAY_S)
                        await _dismiss_modal(page)

                        record = await _read_details(page)
                        jobs.append(record)

                        if writer is not None:
                            writer.append(record)

                        progress.update(
                            task,
                            completed=len(jobs),
                            description=(
                                f"Page {page_num} [{idx}/{len(cards)}]"
                            ),
                        )

                        if len(jobs) >= max_results:
                            break

                    # A page with fewer cards than the full quota is the last page.
                    if len(cards) < RESULTS_PER_PAGE:
                        break

                    page_num += 1

        except Exception:
            logger.exception("Scrape failed on page %d", page_num)
        finally:
            await page.close()
            await context.close()

    return jobs


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

#: Separator line width used throughout the interactive CLI.
_CLI_WIDTH: int = 70


def _prompt_multi(
    label: str,
    mapping: dict[str, Any],
    *,
    single: bool = True,
    retries: int = 2,
) -> list[str] | str | None:
    """Display a numbered menu and return the user's selection.

    Re-prompts on invalid input up to *retries* additional times before
    giving up and returning ``None``.

    Parameters
    ----------
    label:
        Descriptive name shown above the menu, e.g. ``"Work mode"``.
    mapping:
        A module-level look-up table whose *keys* form the menu options.
    single:
        ``True`` (default) — accept exactly one number; return a ``str`` key.
        ``False`` — accept comma-separated numbers; return a ``list[str]``.
    retries:
        Extra attempts allowed after the first invalid input.  ``0`` means
        one attempt only.

    Returns
    -------
    str or list[str] or None
        Selected key (``single=True``) or list of keys (``single=False``).
        Returns ``None`` on blank input or once all retries are exhausted.
    """
    index: dict[int, str] = {i: k for i, k in enumerate(mapping.keys(), 1)}

    print("-" * _CLI_WIDTH)
    print(f"\n{label}:")
    for num, key in index.items():
        print(f"  {num} : {key}")

    hint = "number" if single else "comma-separated numbers"
    prompt = f"{label} ({hint}, or blank to skip): "

    for attempt in range(retries + 1):
        raw = input(prompt).strip()
        if not raw:
            return None
        try:
            if single:
                return index[int(raw)]
            return [index[int(token)] for token in raw.split(",") if token.strip()]
        except (ValueError, KeyError):
            remaining = retries - attempt
            if remaining > 0:
                print(
                    f"  Invalid — enter a number from 1 to {len(index)}. "
                    f"({remaining} attempt{'s' if remaining != 1 else ''} left)"
                )
            else:
                print(f"  Could not parse '{raw}' — skipping {label}.")

    return None


def _build_arg_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser for the scraper.

    All arguments are optional.  Omitted fields fall back to interactive
    prompts at runtime.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="ziprecruiter_scraper",
        description="Scrape job listings from ZipRecruiter and save to JSON.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--keywords", "-k",
        help="Search keywords, e.g. 'Python developer'.",
    )
    parser.add_argument(
        "--location", "-l",
        help="Location, e.g. 'New York'. Defaults to USA when blank.",
    )
    parser.add_argument(
        "--max", "-n",
        type=int,
        default=25,
        metavar="N",
        help="Maximum number of results to collect.",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON filename. Auto-timestamped when omitted.",
    )
    parser.add_argument(
        "--work-mode", "-w",
        choices=list(WORK_MODE.keys()),
        help="Remote / hybrid / on-site filter.",
    )
    parser.add_argument(
        "--employment-type", "-e",
        choices=list(EMPLOYMENT_TYPE.keys()),
        help="Employment type filter.",
    )
    parser.add_argument(
        "--salary-floor",
        default="",
        metavar="AMOUNT",
        help="Minimum annual salary (integer).",
    )
    parser.add_argument(
        "--salary-ceil",
        default="",
        metavar="AMOUNT",
        help="Maximum annual salary (integer).",
    )
    parser.add_argument(
        "--radius",
        default="",
        metavar="KM",
        help="Search radius in kilometres.",
    )
    parser.add_argument(
        "--chrome-profile",
        metavar="PATH",
        help="Chrome user-data directory. Auto-detected per OS when omitted.",
    )
    return parser


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> None:
    """Gather remaining inputs interactively, then execute the scrape.

    Fields already provided via CLI arguments bypass their interactive prompt.

    Parameters
    ----------
    args:
        Parsed namespace from :func:`_build_arg_parser`.
    """
    # --- Keywords (required; cannot be blank) ----------------------------
    keywords: str = args.keywords or ""
    while not keywords:
        print("-" * _CLI_WIDTH)
        keywords = input("Keywords: ").strip()
        if not keywords:
            print("  Keywords cannot be blank.")

    # --- Location ---------------------------------------------------------
    location: str = args.location if args.location is not None else ""
    if args.location is None:
        print("-" * _CLI_WIDTH)
        location = input("Location (blank = USA): ").strip()

    # --- Filters always collected interactively ---------------------------
    date_filter = _prompt_multi("Date Filter",      DATE_FILTER)
    apply_type  = _prompt_multi("Apply Type",       APPLY_TYPE)
    experience  = _prompt_multi("Experience level", EXPERIENCE, single=False)

    # --- Filters that may come from CLI or interactive prompt -------------
    employment_type: str | None = args.employment_type or _prompt_multi(
        "Employment type", EMPLOYMENT_TYPE
    )
    work_mode: str | None = args.work_mode or _prompt_multi(
        "Work mode", WORK_MODE
    )

    # --- Salary (prompt only when both CLI values are absent) ------------
    salary_floor: str = args.salary_floor
    salary_ceil:  str = args.salary_ceil
    if not salary_floor and not salary_ceil:
        print("-" * _CLI_WIDTH)
        salary_floor = input("Min salary? (blank = no floor): ").strip()
        salary_ceil  = input("Max salary? (blank = no ceil): ").strip()

    # --- Radius -----------------------------------------------------------
    radius: str = args.radius
    if not radius:
        print("-" * _CLI_WIDTH)
        radius = input("Radius in km (blank to skip): ").strip()

    # --- Output path ------------------------------------------------------
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output or f"ziprecruiter_jobs_{ts}.json")

    # --- Chrome profile ---------------------------------------------------
    chrome_profile = Path(args.chrome_profile) if args.chrome_profile else None

    # --- Build URL and summarise -----------------------------------------
    url = build_search_url(
        keywords=keywords,
        location=location,
        date_filter=date_filter or "",
        apply_type=apply_type or "",
        experience=experience if isinstance(experience, list) else None,
        employment_type=employment_type or "",
        work_mode=work_mode or None,
        salary_floor=salary_floor,
        salary_ceil=salary_ceil,
        radius=radius,
    )

    print("-" * _CLI_WIDTH)
    print(f"Search URL : {url}")
    print(f"Output     : {output_path}")
    print(f"Max results: {args.max}")
    print("-" * _CLI_WIDTH)

    # --- Scrape ----------------------------------------------------------
    jobs = await scrape_jobs(
        base_url=url,
        max_results=args.max,
        output_path=output_path,
        chrome_profile=chrome_profile,
    )

    count = len(jobs)
    suffix = "s" if count != 1 else ""
    if count:
        print(f"\n✓ Saved {count} job{suffix} → {output_path}")
    else:
        print("\n  No jobs collected.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and run the async scraper.

    This is the setuptools entry point as well as the ``__main__`` guard.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    parser = _build_arg_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()