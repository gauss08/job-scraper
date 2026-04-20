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
    "Relevance":"relevance,desc",
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

SALARY_TYPE={
    "Any":"",
    "Hourly":"0",
    "Fixed-Price":"1"
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

    params={}

    params["q"]=keywords
    params["location"] = client_location.strip() if client_location.strip() else ""

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
 
    params["sort"] = SORT_BY[sort_by] if sort_by else  "relevance,desc"

    base='https://www.upwork.com/nx/search/jobs/?nbs=1&'
    query="&".join(f"{k}={quote_plus(v)}" for k, v in params.items())


    return f"{base}{query}"






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



# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _prompt_multi(label: str, mapping: dict,single: bool = False) -> list | None:
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
    W=70

    print("-"*W)
    print(f"\n{label}:")
    for k, v in enumerate(mapping.keys(),1):
        temp[k]=v
        print(f"  {k} : {v}")
    raw = input(f"{label} (comma-separated, or blank to skip): ").strip()
    if not raw:
        return None
    try:
        if single:
            print(temp[int(raw)])
            return temp[int(raw)]
        else:
            return [temp[int(x)] for x in raw.split(",") if x.strip()]
    except ValueError:
        print(f"Could not parse '{raw}' — skipping {label} filter.")
        return None


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

async def scrape_jobs(base_url : str,
                      max_results: int = 25,
                      headless: bool = False,
                      ) -> list:

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
        page=await context.new_page()
        collected = []
        page_num=1
        
        try:
            while len(collected)<max_results:
                url=f"{base_url}&page={page_num}"
                print(f" 🔃 Collecting results (max={max_results}, page={page_num})...")
                
                try:
                    await page.goto(url,wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(3500)
                    await _dismiss_modal(page)
                except Exception as exc:
                    print(f" ⚠️ Failed to load page {page_num}: {exc}")
                    break
            
                # ── Phase 1: Harvest hrefs from currently visible cards ──────
                cards = page.locator("article.job-tile")
                count = await cards.count()
                
                if count==0:
                    print(" ✅ No more results found.")
                    break
                
                hrefs=[]
                for i in range(count):
                    try:
                        href = await cards.nth(i).locator("a").nth(0).get_attribute("href", timeout=2000)
                        if href:
                            link=href.split('?')[0][6:]
                            if link:
                                hrefs.append('https://www.upwork.com/freelance-jobs/apply/' + link)
                    except Exception:
                        continue
                    
                # ── Phase 2: Visit each job page ──────────────────────────
                # Progress bar setup
                remaining = max_results - len(collected)
                with Progress(
                    TextColumn("[bold green]{task.description}"),
                    BarColumn(),
                    TextColumn("{task.completed}/{task.total} done"),
                    TimeElapsedColumn(),
                ) as progress:
                
                    task = progress.add_task(f"Page {page_num}...", total=min(len(hrefs), remaining))

                    for full_link in hrefs:
                        if len(collected)>=max_results:
                            break
                        try:
                            await page.goto(full_link, wait_until="domcontentloaded", timeout=15000)
                            collected.append(full_link)
                            progress.update(task, advance=1)
                            try:
                                await page.go_back(wait_until="domcontentloaded", timeout=8000)
                                await page.wait_for_timeout(1200)
                                await _dismiss_modal(page)
                            except Exception as e:
                                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                                await page.wait_for_timeout(2_000)
                                await _dismiss_modal(page)
                        except Exception as e:
                            print(f" ⚠️ Failed to visit {full_link}: {e}")


                page_num+=1
                          
        finally:
            await page.close()  # Always clean up the page
            await context.close()
            await browser.close()

        print(f" ✅ Done. Collected {len(collected)} jobs.")
        return collected



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run_interactive() -> None:
    keywords = input("Keywords: ").strip()
    client_location = input("ClientLocation (blank = all): ").strip()
 
    experience = _prompt_multi("Experience Level", EXPERIENCE)
    client_history = _prompt_multi("Client History", CLIENT_HISTORY)
    project_length = _prompt_multi("Project Lenght", PROJECT_LENGTH)
    hours_per_week = _prompt_multi("Hours Per Week", HOURS_PER_WEEK)
    salary_type = _prompt_multi("Salary Type", SALARY_TYPE)

    sort_by = _prompt_multi("Sort By", SORT_BY, True)


    url=build_search_url(
        keywords=keywords,
        client_location=client_location,
        experience=experience,
        client_history=client_history,
        project_length=project_length,
        hours_per_week=hours_per_week,
        salary_type=salary_type,
        sort_by=sort_by
    )

    print(f" 🔅 URL : {url}")

    jobs=await scrape_jobs(url,max_results=25)








def main() -> None:
    """CLI entry point.
 
    Behaviour
    ---------
    * **No arguments** → interactive prompt session (:func:`_run_interactive`).
    * **With arguments** → non-interactive run (:func:`_run_from_args`).
    * ``--help`` / ``-h`` prints usage and exits (handled by argparse).
    """
 
    # If the script is called with no arguments, drop into interactive mode.
    asyncio.run(_run_interactive())
 
 

if __name__=="__main__":
    main()