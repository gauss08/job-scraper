"""
upwork_scraper.py
=================
Asynchronous Upwork job scraper powered by Playwright.

Workflow
--------
1. The user supplies search keywords and optional filter criteria via the
   interactive CLI (or programmatically via build_search_url).
2. build_search_url() assembles a valid Upwork search URL from those inputs.
3. scrape_jobs() launches a persistent Chromium browser, paginates through
   the search results, visits every job page, and collects structured data.
4. Results are written to a timestamped JSON file.

Dependencies
------------
- playwright  : browser automation  (pip install playwright && playwright install chromium)
- rich        : progress bar display (pip install rich)
"""

import asyncio
import json
import re
import sys
import os
import argparse
import getpass
from datetime import datetime
from urllib.parse import quote_plus

# rich provides a live progress bar while pages are being scraped
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, TimeElapsedColumn

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
)


# ---------------------------------------------------------------------------
# Look-up tables
# ---------------------------------------------------------------------------
# Each dictionary maps the human-readable label shown in the CLI menu to the
# corresponding value that Upwork expects in the search query string.
# An empty string ("") means "no filter applied" (Upwork ignores the param).

EXPERIENCE = {
    "Any":          "",   # no contractor_tier filter
    "Entry Level":  "1",
    "Intermediate": "2",
    "Expert":       "3",
}

SORT_BY = {
    "Relevance": "relevance,desc",  # Upwork default ordering
    "Newest":    "recency",         # most recently posted first
}

CLIENT_HISTORY = {
    "Any":         "",     # no client_hires filter
    "No hires":    "0",    # clients who have never hired
    "1 to 9 hires":"1-9",  # small hiring history
    "10+ hires":   "10-",  # experienced clients
}

PROJECT_LENGTH = {
    "Any":                    "",          # no duration_v3 filter
    "Less than one month":    "week",
    "1 to 3 months":          "month",
    "3 to 6 months":          "semester",
    "More than 6 months":     "ongoing",
}

HOURS_PER_WEEK = {
    "Any":                     "",           # no workload filter
    "Less than 30 hrs/week":   "as_needed",  # part-time / flexible
    "More than 30 hrs/week":   "full_time",  # full-time commitment
}

SALARY_TYPE = {
    "Any":         "",   # no contract type filter
    "Hourly":      "0",  # pay by the hour
    "Fixed-Price": "1",  # fixed budget for the whole project
}


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def build_search_url(
    keywords: str,
    client_location: str = "",
    experience: list[str] | None = None,
    client_history: list[str] | None = None,
    project_length: list[str] | None = None,
    hours_per_week: list[str] | None = None,
    salary_type: list[str] | None = None,
    sort_by: str | None = None,
) -> str:
    """Build an Upwork job-search URL from filter criteria.

    Each list parameter accepts one or more label strings from the
    corresponding lookup table (e.g. ``["Expert"]`` or
    ``["Entry Level", "Intermediate"]``).  Multiple values are joined with
    commas, which Upwork interprets as an OR condition.

    Parameters
    ----------
    keywords:
        Free-text search query (mapped to the ``q=`` parameter).
    client_location:
        Optional geographic filter for the client's country / region.
    experience:
        Freelancer experience level(s) — values from :data:`EXPERIENCE`.
    client_history:
        Client hiring history filter — values from :data:`CLIENT_HISTORY`.
    project_length:
        Expected project duration — values from :data:`PROJECT_LENGTH`.
    hours_per_week:
        Weekly commitment — values from :data:`HOURS_PER_WEEK`.
    salary_type:
        Contract type (hourly vs fixed-price) — values from :data:`SALARY_TYPE`.
    sort_by:
        Single sort label from :data:`SORT_BY`.  Defaults to ``"Relevance"``.

    Returns
    -------
    str
        A fully qualified Upwork search URL ready to be loaded by Playwright.
    """
    params = {}

    # Core search term — always required
    params["q"] = keywords

    # Optional geographic restriction on the client side
    params["location"] = client_location.strip() if client_location.strip() else ""

    # Translate each filter list into a comma-joined URL value.
    # We look up every human-readable label in its respective dictionary.
    if experience:
        params["contractor_tier"] = ",".join(EXPERIENCE[w] for w in experience)

    if client_history:
        params["client_hires"] = ",".join(CLIENT_HISTORY[w] for w in client_history)

    if project_length:
        params["duration_v3"] = ",".join(PROJECT_LENGTH[w] for w in project_length)

    if hours_per_week:
        params["workload"] = ",".join(HOURS_PER_WEEK[w] for w in hours_per_week)

    if salary_type:
        params["t"] = ",".join(SALARY_TYPE[w] for w in salary_type)

    # Sort order — fall back to relevance if the caller didn't specify
    params["sort"] = SORT_BY[sort_by] if sort_by else "relevance,desc"

    # nbs=1 enables the newer search UI that this scraper's selectors target
    base = "https://www.upwork.com/nx/search/jobs/?nbs=1&"

    # URL-encode each value so special characters don't break the query string
    query = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())

    return f"{base}{query}"


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

async def _dismiss_modal(page: Page) -> None:
    """Attempt to close cookie banners or sign-in modals.

    Tries a small set of known CSS selectors, then falls back to pressing
    ``Escape``.  Failures are silently swallowed — callers should not depend
    on this function succeeding.

    Parameters
    ----------
    page:
        The Playwright :class:`~playwright.async_api.Page` to operate on.
    """
    # These selectors cover the two most common overlays encountered on Upwork:
    #   1. The OneTrust cookie-consent banner that appears on first visit
    #   2. The "Sign in to see more" modal that appears mid-session
    MODAL_DISMISS_SELECTORS = [
        "button.onetrust-close-btn-handler.banner-close-button",
        "button.air3-modal-close.modal-header-close-button",
    ]

    for sel in MODAL_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first
            # Use a short timeout so we don't stall if the element isn't present
            if await btn.is_visible(timeout=1000):
                await btn.click()
                await page.wait_for_timeout(300)  # brief pause for the modal animation
                return
        except Exception:
            pass  # selector not found or click failed — try the next one

    # Last-resort fallback: pressing Escape closes most modals
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except Exception:
        pass  # silently ignore if even Escape fails


async def _read_details(page: Page, login: bool = False) -> dict | None:
    """Scrape structured data from a single Upwork job page.

    Upwork renders a different DOM structure depending on whether the visitor
    is logged in, so two separate extraction paths exist (controlled by the
    ``login`` flag).

    Parameters
    ----------
    page:
        The Playwright page currently showing the job posting.
    login:
        ``True``  → use logged-in selectors (richer data, includes Connects cost).
        ``False`` → use logged-out / public selectors.

    Returns
    -------
    dict
        Extracted job data with the keys described below, or ``None`` if the
        job is marked as private / restricted.

    Extracted fields (logged-out)
    ------------------------------
    link             : str   – canonical URL
    job_name         : str   – job title
    location         : str   – client's country / region
    posted           : str   – relative post time ("2 hours ago", etc.)
    summary          : str   – full job description
    job_info         : list  – nested pairs [value, label] for budget, type, etc.
    skills           : list  – required skill tags
    activity_on_job  : list  – proposal / interview activity strings
    client_info      : list  – client history / rating pairs

    Additional field (logged-in only)
    -----------------------------------
    connects_required : list – number of Connects needed to apply
    """
    info = {}

    if not login:
        # ── Logged-out (public) extraction path ──────────────────────────

        # If this private-job indicator element exists, the page shows a
        # "This job is private" message rather than actual job data.
        if await page.locator("div.reason-text").count() > 0:
            return None  # signal to the caller that the job is private

        # Canonical link gives us a clean, parameter-free URL
        info["link"] = await page.locator("link[rel='canonical']").get_attribute("href")

        info["job_name"] = await page.locator("h1.m-0.h4").inner_text()

        # `.first` is used throughout because Upwork occasionally renders
        # duplicate elements; we always want the primary / topmost one.
        info["location"] = await page.locator("p.text-light-on-muted.m-0").first.inner_text()
        info["posted"]   = await page.locator("div.mt-5").first.inner_text()
        info["summary"]  = await page.locator("div.break.mt-2").first.inner_text()

        # job_info: each <li> in ul.features contains child elements whose
        # text forms a key-value pair (e.g. ["$500", "Fixed-price budget"]).
        # We use evaluate() to extract all direct children in one round-trip.
        items = await page.locator("ul.features li").all()
        job_info = []
        for item in items:
            parts = await item.evaluate("""el => {
                return [...el.children].map(child => child.textContent.trim()).filter(t => t);
            }""")
            job_info.append(parts)
        # Reverse each pair so the order is consistently [value, label]
        info["job_info"] = [j[::-1] for j in job_info]

        # Primary skills list — split on newlines to get individual tags
        skills_locator = await page.locator("div.skills-list").first.inner_text()
        skills = skills_locator.split("\n")

        # A second skills container may hold overflow tags hidden behind
        # a "show more" toggle; use a set to deduplicate any repeated entries
        more_skills_locator = page.locator("div.skills-list").nth(1).locator("span")
        more_skills_duplicated = await more_skills_locator.all_text_contents()
        more_skills = set(more_skills_duplicated) if more_skills_duplicated else set()

        # Drop the trailing empty string from the split, then merge overflow skills
        info["skills"] = skills[:-1] + list(more_skills)

        # Proposal activity strings (e.g. "5 to 10", "Last viewed by client 1 day ago")
        info["activity_on_job"] = await page.locator("ul.visitor li").all_text_contents()

        # Client history items — same evaluate() pattern as job_info
        cl_items = await page.locator("ul.ac-items li").all()
        client_info = []
        for item in cl_items:
            parts = await item.evaluate("""el => {
                return [...el.children].map(child => child.textContent.trim()).filter(t => t);
            }""")
            if parts:  # skip empty items produced by decorative list elements
                client_info.append(parts)
        info["client_info"] = client_info

    else:
        # ── Logged-in extraction path ─────────────────────────────────────
        # Upwork shows a richer, account-specific view when authenticated.
        # Several selectors differ from the public view.

        # The direct job link is embedded in an input element's value attribute
        # in the format "?source=...&ref=/jobs/~<uid>"; we split on "=" to
        # extract the relative path and prepend the base URL.
        href = await page.locator("section.mt-5 div.mt-2 input.air3-input").first.get_attribute("value")
        link = href.split("=")[-1]
        info["link"] = f"https://www.upwork.com{link}"

        # Logged-in job title sits in a different heading element
        info["job_name"] = await page.locator("h4.d-flex span.flex-1").inner_text()
        info["location"] = await page.locator("p.text-light-on-muted.m-0").first.inner_text()

        # Posting time is inside a span rather than a standalone div
        info["posted"]  = await page.locator("div.text-light-on-muted span").first.inner_text()
        info["summary"] = await page.locator("div.break.mt-2").first.inner_text()

        # job_info extraction is identical to the logged-out path
        items = await page.locator("ul.features li").all()
        job_info = []
        for item in items:
            parts = await item.evaluate("""el => {
                return [...el.children].map(child => child.textContent.trim()).filter(t => t);
            }""")
            job_info.append(parts)
        info["job_info"] = [j[::-1] for j in job_info]

        # When logged in, the skills container doesn't have an overflow section
        skills_locator = await page.locator("div.skills-list").first.inner_text()
        info["skills"] = [s for s in skills_locator.split("\n") if s.strip()]

        # Different list class for proposal activity in the authenticated view
        info["activity_on_job"] = await page.locator("ul.client-activity-items li").all_text_contents()

        # Reuse ul.features for client info in the logged-in layout
        cl_items = await page.locator("ul.features li").all()
        client_info = []
        for item in cl_items:
            parts = await item.evaluate("""el => {
                return [...el.children].map(child => child.textContent.trim()).filter(t => t);
            }""")
            if parts:
                client_info.append(parts)
        info["client_info"] = client_info

        # Exclusive to the logged-in view: how many Connects this job costs
        sel = ["mt-4", "mt-5"]
        for s in sel:
            try:
                connects_info = await page.locator(f"div.text-light-on-muted.{s}").inner_text()
                info["connects_required"] = connects_info.split("\n")
            except Exception as e:
                pass

    return info


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def _login(page: Page, user_mail: str, password: str) -> None:
    """Automate the Upwork two-step login flow.

    Navigates to the login page, fills the email field, clicks *Continue*,
    then fills the password field and clicks *Continue* again.

    .. warning::
        Two-factor authentication (2FA / OTP) is **not** handled.  If the
        account has 2FA enabled the function will stall on the OTP screen.

    Parameters
    ----------
    page:
        The Playwright page to use for navigation and form interaction.
    user_mail:
        Upwork account e-mail address.
    password:
        Upwork account password.
    """
    try:
        print("Login page...")
        login_url = "https://www.upwork.com/ab/account-security/login"

        # Step 1: load the login page
        await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)

        # Step 2: enter email and advance to the password step
        await page.locator("#login_username").fill(user_mail)
        await page.locator("#login_password_continue").click()

        # Wait for the password field to appear (Upwork uses a two-screen flow)
        await page.wait_for_timeout(3500)

        # Step 3: enter password and submit
        await page.locator("#login_password").fill(password)
        await page.locator("#login_control_continue").click()

        # Allow time for the post-login redirect and session cookies to settle
        await page.wait_for_timeout(4000)

        # Dismiss any welcome modal or cookie banner that appears after login
        await _dismiss_modal(page)

    except Exception as e:
        print(f"Error : {e}")


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

async def scrape_jobs(
    base_url: str,
    max_results: int = 25,
    headless: bool = False,
    login: bool = False,
    user_mail: str = "",
    password: str = "",
) -> tuple[list, list]:
    """Scrape Upwork job listings from a search URL.

    Launches a persistent Chromium context, optionally authenticates, then
    paginates through the search results pages until ``max_results`` jobs
    have been collected or no more pages exist.

    Parameters
    ----------
    base_url:
        Search URL produced by :func:`build_search_url`.
    max_results:
        Stop after collecting this many jobs (default 25).
    headless:
        Passed to the browser launcher — currently unused because
        ``launch_persistent_context`` is hardcoded to ``headless=False``
        (reserved for future use).
    login:
        If ``True``, call :func:`_login` before scraping.
    user_mail:
        Upwork email — only used when ``login=True``.
    password:
        Upwork password — only used when ``login=True``.

    Returns
    -------
    tuple[list, list]
        ``(collected, private_jobs)`` where:

        * ``collected``    – list of job-data dicts from :func:`_read_details`
        * ``private_jobs`` – list of job URLs that returned a private-job page
    """
    async with async_playwright() as p:

        # Use a persistent context so existing Chrome cookies / sessions are
        # reused, which significantly reduces bot-detection friction.
        # The --disable-blink-features flag and dropping --enable-automation
        # help mask the fact that the browser is being driven programmatically.
        context = await p.chromium.launch_persistent_context(
            user_data_dir=f"/home/{os.getenv('USER')}/.config/google-chrome",
            headless=False,                                  # always show the window
            channel="chromium",                              # use real Chromium binary
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],     # hides the "automated" banner
        )

        page = await context.new_page()
        collected = []    # successfully scraped job dicts
        private_jobs = [] # URLs of jobs hidden behind a login wall
        page_num = 1      # current search results page

        # Optionally authenticate before starting the scrape loop
        if login:
            try:
                await _login(page, user_mail, password)
            except Exception as e:
                print(f" ⚠️ Failed to load page : Error {e}")

        try:
            while len(collected) < max_results:
                # Append the page number to turn the base URL into a paginated URL
                url = f"{base_url}&page={page_num}"
                print(f" 🔃 Collecting results (max={max_results}, page={page_num})...")

                # ── Load the search results page ──────────────────────────
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    # Extra wait for dynamic content to finish rendering
                    await page.wait_for_timeout(3500)
                    await _dismiss_modal(page)
                except Exception as exc:
                    print(f" ⚠️ Failed to load page {page_num}: {exc}")
                    break  # unrecoverable page load failure — stop pagination

                # ── Phase 1: harvest hrefs from job cards ─────────────────
                # Each job on the results page is an <article class="job-tile">
                cards = page.locator("article.job-tile")
                count = await cards.count()

                if count == 0:
                    # No cards means we've gone past the last results page
                    print(" ✅ No more results found.")
                    break

                hrefs = []
                for i in range(count):
                    try:
                        # The apply link is always the first <a> inside the card.
                        # Strip query params (tracking tokens) to get a clean URL,
                        # then drop the "/jobs/~" prefix and rebuild as an apply URL.
                        href = await cards.nth(i).locator("a").nth(0).get_attribute("href", timeout=2000)
                        if href:
                            link = href.split("?")[0]  # remove query string and leading "/jobs/"
                            if link:
                                hrefs.append("https://www.upwork.com" + link)
                    except Exception:
                        continue  # skip cards where the link is inaccessible

                # ── Phase 2: visit each job page and extract data ──────────
                remaining = max_results - len(collected)

                with Progress(
                    TextColumn("[bold green]{task.description}"),
                    BarColumn(),
                    TextColumn("{task.completed}/{task.total} done"),
                    TimeElapsedColumn(),
                ) as progress:

                    task = progress.add_task(f"Page {page_num}...", total=min(len(hrefs), remaining))

                    for full_link in hrefs:
                        if len(collected) >= max_results:
                            break  # we've hit the requested cap — stop early

                        try:
                            await page.goto(full_link, wait_until="domcontentloaded", timeout=15000)

                            # Check for the "private job" heading that Upwork shows
                            # when a listing requires login to view in full
                            private = page.locator("h4.display-rebrand")
                            if await private.count() > 0:
                                # Record the URL so the caller can report / retry with login
                                private_jobs.append(full_link)
                            else:
                                info = await _read_details(page, login)
                                if info:
                                    collected.append(info)
                                progress.update(task, advance=1)

                            # Navigate back to the results page for the next iteration.
                            # If go_back() fails (e.g. navigation stack is empty), fall
                            # back to a fresh goto() of the search results URL.
                            try:
                                await page.go_back(wait_until="domcontentloaded", timeout=8000)
                                await page.wait_for_timeout(1200)  # brief cool-down between requests
                                await _dismiss_modal(page)
                            except Exception:
                                # Fallback: reload the search page directly
                                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                                await page.wait_for_timeout(2000)
                                await _dismiss_modal(page)

                        except Exception as e:
                            print(f" ⚠️ Failed to visit {full_link}: {e}")

                page_num += 1  # advance to the next search results page

        finally:
            # Always clean up the browser resources, even if an exception occurred
            await page.close()
            await context.close()

    print(f" ✅ Done. Collected {len(collected)} jobs.")
    return collected, private_jobs


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _prompt_multi(label: str, mapping: dict, single: bool = False) -> list | str | None:
    """Display a numbered menu and return the user's selection(s).

    Prints each key in ``mapping`` as a numbered option, reads a
    comma-separated response from stdin, and converts the entered numbers
    back to their corresponding label strings.

    Parameters
    ----------
    label:
        Short descriptive name shown above the menu (e.g. ``"Experience Level"``).
    mapping:
        One of the module-level look-up tables (e.g. :data:`EXPERIENCE`).
    single:
        If ``True``, expect a single integer and return a plain string
        instead of a list (used for the Sort By option).

    Returns
    -------
    list[str] | str | None
        * ``list[str]``  – selected label(s) when ``single=False``
        * ``str``        – selected label when ``single=True``
        * ``None``       – user pressed Enter with no input (skip this filter)
    """
    temp = {}   # maps display number → label string
    W = 70      # separator width for visual clarity

    print("-" * W)
    print(f"\n{label}:")

    # Build the display index and print each option
    for k, v in enumerate(mapping.keys(), 1):
        temp[k] = v
        print(f"  {k} : {v}")

    raw = input(f"{label} (comma-separated, or blank to skip): ").strip()

    if not raw:
        return None  # blank input = "no preference" → filter will be omitted

    try:
        if single:
            # Return a single label string for menus that allow only one choice
            return temp[int(raw)]
        else:
            # Parse each comma-separated number and look up the corresponding label
            return [temp[int(x)] for x in raw.split(",") if x.strip()]
    except ValueError:
        print(f"Could not parse '{raw}' — skipping {label} filter.")
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run_interactive() -> None:
    """Collect search parameters interactively and execute a scrape run.

    Prompts the user for keywords, optional filters, login credentials, and
    then calls :func:`build_search_url` + :func:`scrape_jobs`.  Results are
    saved to a timestamped JSON file in the current working directory.
    """
    # Core search term — the only mandatory input
    keywords = input("Keywords: ").strip()

    # Optional: restrict results to jobs posted by clients in a specific location
    client_location = input("Client Location (blank = all): ").strip()

    # Collect optional filter selections via the numbered menus
    experience    = _prompt_multi("Experience Level", EXPERIENCE)
    client_history = _prompt_multi("Client History",  CLIENT_HISTORY)
    project_length = _prompt_multi("Project Length",  PROJECT_LENGTH)
    hours_per_week = _prompt_multi("Hours Per Week",  HOURS_PER_WEEK)
    salary_type    = _prompt_multi("Salary Type",     SALARY_TYPE)

    # Sort order is a single-choice menu (not multi-select)
    sort_by = _prompt_multi("Sort By", SORT_BY, single=True)

    # Optionally authenticate — required to see private jobs and Connects cost
    login = input("Login y/n: ").strip().lower().startswith("y")
    user_mail = input("User mail: ").strip() if login else ""
    password  = getpass.getpass("Password: ").strip()  if login else ""

    # Assemble the search URL from all gathered parameters
    url = build_search_url(
        keywords=keywords,
        client_location=client_location,
        experience=experience,
        client_history=client_history,
        project_length=project_length,
        hours_per_week=hours_per_week,
        salary_type=salary_type,
        sort_by=sort_by,
    )

    print(f" 🔅 URL : {url}")

    # Run the scraper (hard-coded to 5 results for the interactive demo)
    jobs, private_jobs = await scrape_jobs(
        url,
        max_results=5,
        login=login,
        user_mail=user_mail,
        password=password,
    )

    # Persist results to a timestamped JSON file so runs don't overwrite each other
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"upwork_jobs_{ts}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(jobs)} jobs → {filename}")

    # Save private job URLs separately so the user can retry them with login
    if not login and len(private_jobs) > 0:
        with open(f"private_jobs_{ts}.json", "w", encoding="utf-8") as f:
            json.dump(private_jobs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(private_jobs)} Private jobs")


def main() -> None:
    """CLI entry point.

    Currently always runs in interactive mode (:func:`_run_interactive`).
    The ``argparse`` import is in place for a future non-interactive mode
    where all parameters can be passed as command-line flags.
    """
    asyncio.run(_run_interactive())


if __name__ == "__main__":
    main()