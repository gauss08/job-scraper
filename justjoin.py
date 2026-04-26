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
 
#: Mapping of numeric shorthand → JustJoin.it query value for workplace type.
WORK_MODE: dict[int, str] = {
    1: "remote",
    2: "hybrid",
    3: "office",
}
 
#: Mapping of numeric shorthand → JustJoin.it query value for working hours.
WORK_TYPE: dict[int, str] = {
    1: "full-time",
    2: "part-time",
    3: "practice-internship",
    4: "freelance",
}
 
#: Mapping of numeric shorthand → JustJoin.it query value for seniority level.
EXPERIENCE: dict[int, str] = {
    1: "junior",
    2: "mid",
    3: "senior",
    4: "c-level",
}
 
#: Mapping of numeric shorthand → JustJoin.it query value for contract type.
EMPLOYMENT_TYPE: dict[int, str] = {
    1: "b2b",
    2: "permanent",
    3: "internship",
    4: "mandate-contract",
    5: "specific-task-contract",
}
 
#: Mapping of numeric shorthand → JustJoin.it sort field / direction.
SORT_BY: dict[int, str] = {
    1: "published",
    2: "newest",
    3: "salary desc",
    4: "salary asc",
}
 
#: Human-readable skill proficiency labels used in job detail pages.
SKILL_LEVELS: dict[str, int] = {
    "Nice To Have": 1,
    "Junior": 2,
    "Regular": 3,
    "Advanced": 4,
    "Master": 5,
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

    """Construct a JustJoin.it search URL from filter parameters.
 
    Parameters
    ----------
    keywords:
        Free-text job title / skill keywords (e.g. ``"python backend"``).
    location:
        City or region slug accepted by JustJoin.it (e.g. ``"warsaw"``).
        Defaults to ``"all-locations"`` when empty.
    experience:
        List of seniority keys from :data:`EXPERIENCE`
        (e.g. ``[1, 2]`` → junior + mid).
    work_type:
        List of working-hours keys from :data:`WORK_TYPE`.
    work_mode:
        List of workplace keys from :data:`WORK_MODE`.
    employment_type:
        List of contract-type keys from :data:`EMPLOYMENT_TYPE`.
    salary:
        Pass any truthy string (e.g. ``"1"``) to filter for offers with
        a published salary range.
    sort_by:
        Key from :data:`SORT_BY`.  When omitted the site's default order
        is used.
    radius:
        Search radius in kilometres (e.g. ``"50"``).  Ignored when
        *location* is empty.
 
    Returns
    -------
    str
        A fully-formed URL ready to be passed to :func:`scrape_jobs`.
 
    Raises
    ------
    KeyError
        If any numeric key is not present in the corresponding look-up table.
 
    Examples
    --------
    >>> build_search_url("python", "warsaw", experience=[1], work_mode=[1, 2])
    'https://justjoin.it/job-offers/warsaw?keyword=python&...'
    """

    params={}

    params['keyword']=keywords
    location = location.strip() if location.strip() else "all-locations"

    if experience:
        params["experience-level"] = ",".join(EXPERIENCE[int(e)] for e in experience)
 
    if work_type:
        params["working-hours"] = ",".join(WORK_TYPE[int(w)] for w in work_type)
 
    if work_mode:
        params["workplace"] = ",".join(WORK_MODE[int(w)] for w in work_mode)
 
    if employment_type:
        params["employment-type"] = ",".join(
            EMPLOYMENT_TYPE[int(e)] for e in employment_type
        )
 
    if salary:
        params["with-salary"] = salary
 
    if radius:
        params["radius"] = str(radius)

    base='https://justjoin.it/job-offers/'
    query="&".join(f"{k}={quote_plus(v)}" for k, v in params.items())


    if sort_by:
        orderBy='ASC' if int(sort_by) == 4 else 'DESC'
        sortBy='salary' if int(sort_by) in [3,4] else SORT_BY[int(sort_by)]
        query+=f'&orderBy={orderBy}&sortBy={sortBy}'

    return f"{base}{location}?{query}"


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
    '.cookiescript_pre_header'
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




async def _read_details(page: Page) -> dict:
    """Extract all structured data from a single job-detail page.
 
    The function reads the canonical URL, job metadata, salary ranges,
    the full job description, and the required tech-stack with proficiency
    levels.
 
    Parameters
    ----------
    page:
        A Playwright :class:`~playwright.async_api.Page` already navigated
        to a JustJoin.it job-detail URL.
 
    Returns
    -------
    dict
        Keys: ``link``, ``job_name``, ``company``, ``location``,
        ``work_type``, ``employment_type``, ``experience``, ``work_mode``,
        ``salary``, ``description``, ``skills``, ``days_left``,
        ``last_date``.
        Any field that cannot be read is stored as ``None`` rather than
        raising an exception.
 
    Notes
    -----
    CSS class names on JustJoin.it are generated at build time and may
    change without notice.  If scraping breaks, inspect the selectors below
    against the live page and update as needed.
    """


    info={}

    # -- Canonical URL -------------------------------------------------------
    info["link"]=await page.locator("link[rel='canonical']").get_attribute('href')

    # -- Header fields -------------------------------------------------------
    info["job_name"]=await page.locator('h1.mui-1w3djua').inner_text()
    info["company"]=await page.locator('h2.MuiBox-root').inner_text()
    info["location"]=await page.locator('div.MuiBox-root.mui-1lgfpg4').first.inner_text()

    # The four badge-style metadata chips share the same CSS class; their
    # order is: work-type, employment-type, experience, work-mode.
    chips = page.locator("div.MuiStack-root.mui-9ffzmz")
    labels = ("work_type", "employment_type", "experience", "work_mode")
    for idx, key in enumerate(labels):
        try:
            info[key] = await chips.nth(idx).inner_text()
        except Exception:
            info[key] = None
    

    # -- Salary (optional) ---------------------------------------------------
    salary_locator=page.locator('div.MuiTypography-root.mui-1f21jp8')
    if await salary_locator.count()>0:
        info["salary"]=await page.locator('div.MuiTypography-root.mui-1f21jp8').all_inner_texts()
    else:
        info["salary"]=None


    # -- Job description -----------------------------------------------------
    try:
        info["description"] = await page.locator("div.MuiStack-root.mui-qd57u1").inner_text()
    except Exception:
        info["description"] = None


    # -- Tech-stack / skills -------------------------------------------------
    # The block is formatted as alternating skill-name / proficiency-level
    # lines after a header line, e.g.:
    #   Tech stack\nPython\nAdvanced\nDocker\nRegular
    skills={}
    try:
        tech_stack=await page.locator('div.MuiStack-root.mui-j7qwjs').inner_text()
        sp=tech_stack.split('\n')
        # parts[0] is the section heading; skills start at index 1
        for i in range(1,len(sp),2):
            skills[f'{sp[i].strip()}']=sp[i+1].strip()
    except Exception:
        pass

    info['skills']=skills    

    # -- Application deadline ------------------------------------------------
    # Format example: "3 days left (2025-07-10)"
    try:
        expires = await page.locator("div.MuiStack-root.mui-1uqbqus").inner_text()
        paren_match = re.search(r"\(([^)]+)\)", expires)
        info["days_left"] = expires[: expires.rfind("(")].strip() if paren_match else expires.strip()
        info["last_date"] = paren_match.group(1) if paren_match else None
    except Exception:
        info["days_left"] = None
        info["last_date"] = None
 
    return info


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------


async def scrape_jobs(url : str,
                      max_results: int = 50,
                      headless: bool = False,
                      ) -> list:
    """Scrape job listings from a JustJoin.it search URL.
 
    Launches a Chromium browser, navigates to *url*, then iterates over
    result cards — visiting each individual job page to collect full details.
    The browser scrolls the list to trigger lazy-loaded batches until
    *max_results* is reached or no new cards appear for several rounds.
 
    Parameters
    ----------
    url:
        A search URL produced by :func:`build_search_url` (or built
        manually).
    max_results:
        Maximum number of job postings to collect.  Defaults to ``200``.
    headless:
        When ``True`` (default) the browser runs invisibly in the
        background.  Set to ``False`` to watch the browser while debugging.
 
    Returns
    -------
    list[dict]
        A list of job-detail dictionaries as returned by
        :func:`_read_details`.  The list may be shorter than *max_results*
        if the search yields fewer results.
 
    Notes
    -----
    * The scraper deliberately introduces short delays between requests to
      avoid overwhelming the server and to reduce the chance of being
      rate-limited.
    * The ``fetch_descriptions`` parameter has been removed: detail pages
      are always visited because they are the only reliable source for most
      fields.
    """
    jobs=[]

    async with async_playwright() as p:
        browser=await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
    
        context=await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1200, "height": 800},
            locale="en-US",
        )

        
        try:
            page=await context.new_page()
            #print(f" 🔅 Opening : {url[:90]}...")
            await page.goto(url,wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3500)
            await _dismiss_modal(page)

            seen_links = set()
            total = 0
            no_new_rounds = 0
            # Stop after this many consecutive scroll rounds with zero new cards.
            max_idle_rounds = max(3, max_results // 10 + 1)

            print(f" 🔃 Collecting results (max={max_results})...")

            # ✅ Progress bar setup
            with Progress(
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total} done"),
                TimeElapsedColumn(),
            ) as progress:
                
                task = progress.add_task("Scraping jobs...", total=max_results)

                while True:
                    # ── Phase 1: Harvest hrefs from currently visible cards ──────
                    cards = page.locator("ul.MuiStack-root li")
                    count = await cards.count()
                    new_this_round = 0
                    batch = []

                    for i in range(count):
                        try:
                            link = await cards.nth(i).locator("a").nth(0).get_attribute("href", timeout=2000)
                            if not link:
                                continue
                            full_link = 'https://justjoin.it' + link
                            if full_link not in seen_links:
                                batch.append(full_link)
                        except Exception:
                            continue
                    

                    # ── Phase 2: Visit each newly discovered link ─────────────────
                    for full_link in batch:
                        if full_link in seen_links:
                            continue
                        seen_links.add(full_link)
                        new_this_round += 1

                        try:
                            await page.goto(full_link, wait_until="domcontentloaded", timeout=15000)
                            await _dismiss_modal(page)
                            info = await _read_details(page)
                            jobs.append(info)
                            total += 1

                            # ✅ UPDATE PROGRESS HERE
                            progress.update(task, advance=1)
                            
                        except Exception as e:
                            pass
                        finally:
                            # Always navigate back so the list page is restored.
                            try:
                                await page.go_back(wait_until="domcontentloaded", timeout=8000)
                                await page.wait_for_timeout(1200)
                                await _dismiss_modal(page)
                            except Exception as e:
                                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                                await page.wait_for_timeout(2_000)
                                await _dismiss_modal(page)

                        #print(f"{total+1} : {full_link}")
                        

                        if total >= max_results:
                            break
                        
                    if total >= max_results:
                        break
                    
                    # ── Scroll to reveal the next lazy-loaded batch ───────────────
                    try:
                        cards = page.locator("ul.MuiStack-root li")
                        await cards.last.scroll_into_view_if_needed()
                        await page.wait_for_timeout(1500)
                    except Exception:
                        pass
                    
                    # ── Idle-round end-detection ──────────────────────────────────
                    if new_this_round == 0:
                        no_new_rounds += 1
                    else:
                        no_new_rounds = 0

                    if no_new_rounds >= max_idle_rounds:
                        print(f" ✅ No new cards after {max_idle_rounds} scroll rounds, stopping.")
                        break
                
        except Exception as exc:
            print(f"Scraper encountered a fatal error: {exc}")

    print(f" ❇️  Extracted {total} jobs total.")
    return jobs


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _prompt_multi(label: str, mapping: dict) -> list | None:
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
    print(f"\n{label}:")
    for k, v in mapping.items():
        print(f"  {k} : {v}")
    raw = input(f"{label} (comma-separated, or blank to skip): ").strip()
    if not raw:
        return None
    try:
        return [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        print(f"Could not parse '{raw}' — skipping {label} filter.")
        return None



def _build_arg_parser() -> argparse.ArgumentParser:
    """Return a fully-configured :class:`argparse.ArgumentParser`.
 
    The parser covers every filter that :func:`build_search_url` accepts
    plus output / browser options.  Multi-value arguments (``--experience``,
    ``--work-type``, ``--work-mode``, ``--employment-type``) accept one or
    more space-separated integers that map to the corresponding look-up
    tables.
 
    Returns
    -------
    argparse.ArgumentParser
    """
 
    def _fmt_choices(mapping: dict[int, str]) -> str:
        """Inline choice legend for help text."""
        return "  |  ".join(f"{k}={v}" for k, v in mapping.items())
 
    parser = argparse.ArgumentParser(
        prog="justjoin_scraper",
        description=(
            "Scrape job listings from justjoin.it and save them as JSON.\n\n"
            "When called with NO arguments the script starts an interactive\n"
            "prompt-driven session instead."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples\n"
            "--------\n"
            "  # Python backend jobs in Warsaw, remote, junior/mid, with salary:\n"
            "  python justjoin_scraper.py \\\n"
            '      --keywords "python backend" --location warsaw \\\n'
            "      --experience 1 2 --work-mode 1 --salary \\\n"
            "      --max-results 30 --output warsaw_python.json\n\n"
            "  # Interactive mode:\n"
            "  python justjoin_scraper.py\n"
        ),
    )
 
    # ── Search filters ───────────────────────────────────────────────────────
    search = parser.add_argument_group("search filters")
 
    search.add_argument(
        "-k", "--keywords",
        metavar="KEYWORDS",
        required=False,
        help="Job title / skill keywords (e.g. 'python backend').",
    )
    search.add_argument(
        "-l", "--location",
        metavar="LOCATION",
        default="",
        help="City or region slug (e.g. 'warsaw').  Default: all locations.",
    )
    search.add_argument(
        "-e", "--experience",
        metavar="N",
        nargs="+",
        type=int,
        choices=list(EXPERIENCE),
        help=f"Seniority level(s).  Choices: {_fmt_choices(EXPERIENCE)}",
    )
    search.add_argument(
        "-wt", "--work-type",
        metavar="N",
        nargs="+",
        type=int,
        choices=list(WORK_TYPE),
        help=f"Working hours.  Choices: {_fmt_choices(WORK_TYPE)}",
    )
    search.add_argument(
        "-wm", "--work-mode",
        metavar="N",
        nargs="+",
        type=int,
        choices=list(WORK_MODE),
        help=f"Workplace type.  Choices: {_fmt_choices(WORK_MODE)}",
    )
    search.add_argument(
        "-et", "--employment-type",
        metavar="N",
        nargs="+",
        type=int,
        choices=list(EMPLOYMENT_TYPE),
        help=f"Contract type.  Choices: {_fmt_choices(EMPLOYMENT_TYPE)}",
    )
    search.add_argument(
        "--salary",
        action="store_true",
        help="Only return offers that display a salary range.",
    )
    search.add_argument(
        "--radius",
        metavar="KM",
        default="",
        help="Search radius in kilometres (ignored when location is omitted).",
    )
    search.add_argument(
        "-s", "--sort-by",
        metavar="N",
        type=int,
        choices=list(SORT_BY),
        help=f"Result ordering.  Choices: {_fmt_choices(SORT_BY)}",
    )
 
    # ── Output / runtime options ─────────────────────────────────────────────
    output = parser.add_argument_group("output & runtime")
 
    output.add_argument(
        "-n", "--max-results",
        metavar="N",
        type=int,
        default=10,
        help="Maximum number of job postings to collect (default: 10).",
    )
    output.add_argument(
        "-o", "--output",
        metavar="FILE",
        default="justjoin_jobs.json",
        help="Destination JSON file (default: justjoin_jobs.json).",
    )
    output.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run Chromium in headless mode (default: on).",
    )
    output.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Show the browser window (useful for debugging).",
    )
    output.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level log output.",
    )
 
    return parser



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run_interactive() -> None:
    """Prompt-driven session used when no CLI arguments are supplied."""
    keywords = input("Keywords: ").strip()
    location = input("Location (blank = all): ").strip()
 
    experience = _prompt_multi("Experience level", EXPERIENCE)
    work_type = _prompt_multi("Work type", WORK_TYPE)
    employment_type = _prompt_multi("Employment type", EMPLOYMENT_TYPE)
    work_mode = _prompt_multi("Work mode", WORK_MODE)
 
    salary = input("\nWith salary only? (y/blank): ").strip()
    salary = "1" if salary.lower() == "y" else ""
 
    radius = input("Radius in km (blank to skip): ").strip()

    print("\nSort by:")
    for k, v in SORT_BY.items():
        print(f"  {k} : {v}")
    sort_raw = input("Sort by (blank = default): ").strip()
    sort_by = int(sort_raw) if sort_raw else None

    max_raw = input("\nMax results [default 10]: ").strip()
    max_results = int(max_raw) if max_raw.isdigit() else 10

    url=build_search_url(
        keywords=keywords,
        location=location,
        experience=experience,
        work_type=work_type,
        employment_type=employment_type,
        work_mode=work_mode,
        salary=salary,
        radius=radius,
        sort_by=sort_by
    )

    print(f" 🔅 Opening : {url[:90]}...")

    jobs=await scrape_jobs(url,max_results=max_results)

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    filename=f'justjoin_jobs_{ts}.json'
    with open(filename,"w",encoding="utf-8") as f:
        json.dump(jobs,f, indent=2, ensure_ascii=False)
    print(f"Saved {len(jobs)} jobs → {filename}")


async def _run_from_args(args: argparse.Namespace) -> None:
    """Non-interactive run driven by parsed CLI arguments.
 
    Parameters
    ----------
    args:
        Namespace returned by :meth:`argparse.ArgumentParser.parse_args`.
    """
    if not args.keywords:
        print("--keywords is required when running in non-interactive mode.")
        sys.exit(1)
 
    url = build_search_url(
        keywords=args.keywords,
        location=args.location,
        experience=args.experience,
        work_type=args.work_type,
        work_mode=args.work_mode,
        employment_type=args.employment_type,
        salary="1" if args.salary else "",
        radius=args.radius,
        sort_by=args.sort_by,
    )
    print(f"Search URL: {url}")
 
    jobs = await scrape_jobs(url, max_results=args.max_results, headless=args.headless)

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    filename=f'justjoin_jobs_{ts}.json'
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(jobs)} jobs → {args.output}")


def main() -> None:
    """CLI entry point.
 
    Behaviour
    ---------
    * **No arguments** → interactive prompt session (:func:`_run_interactive`).
    * **With arguments** → non-interactive run (:func:`_run_from_args`).
    * ``--help`` / ``-h`` prints usage and exits (handled by argparse).
    """
    parser = _build_arg_parser()
 
    # If the script is called with no arguments, drop into interactive mode.
    if len(sys.argv) == 1:
        asyncio.run(_run_interactive())
        return
 
    args = parser.parse_args()
 
 
    asyncio.run(_run_from_args(args))


if __name__=="__main__":
    main()
