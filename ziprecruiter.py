import asyncio
import json
import re
import sys
import os
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

RESULTS_PER_PAGE = 20
TIMEOUT_COOL_DOWN = 3500

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
        params["refine_by_salary_ceil"]=salary_ceil

    base='https://www.ziprecruiter.com/jobs-search?'
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
        "button._r_1k_",
        "#_r_13_",
    ]

    for sel in MODAL_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first
            # Use a short timeout so we don't stall if the element isn't present
            if await btn.is_visible(timeout=TIMEOUT_COOL_DOWN):
                await btn.click()
                await page.wait_for_timeout(TIMEOUT_COOL_DOWN)  # brief pause for the modal animation
                return
        except Exception:
            pass  # selector not found or click failed — try the next one


    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except Exception:
        pass

async def _read_details(page: Page) -> dict:
    info = {}

    link = await page.locator("div.flex.w-full.flex-row.justify-between a.inline-flex").first.get_attribute("href")
    info['job_url'] = "https://www.ziprecruiter.com"+link

    info['job_title'] = await page.locator("div.w-full div.grid h2.font-bold").inner_text()
    info['company_name'] = await page.locator("div.w-full div.grid a").first.inner_text()
    info['location'] = await page.locator("div.w-full div.grid div.mb-24 ").first.inner_text()

    info['info'] = await page.locator("div.w-full div.flex.flex-col.gap-y-8").first.inner_text()

    info['job_description'] = await page.locator(r"div.w-full div.flex.flex-col.gap-y-\[16px\]").inner_text() #div.w-full div.flex.flex-col.gap-y-\[16px\]

    return info
    


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
            return temp[int(raw)]
        else:
            return [temp[int(x)] for x in raw.split(",") if x.strip()]
    except ValueError:
        print(f"Could not parse '{raw}' — skipping {label} filter.")
        return None


# ─────────────────────────────────────────────
#  Scraper
# ─────────────────────────────────────────────


async def scrape_jobs(base_url : str, max_results: int = 25, headless: bool = False, fetch_descriptions: bool = True,) -> list:
    jobs=[]

    async with async_playwright() as p:

        context = await p.chromium.launch_persistent_context(
            user_data_dir=f"/home/{os.getenv('USER')}/.config/google-chrome",
            headless=False,                                  # always show the window
            channel="chromium",                              # use real Chromium binary
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],     # hides the "automated" banner
        )

        page = await context.new_page()
        page_num = 1

        try:

                            # ✅ Progress bar setup
            with Progress(
                    TextColumn("[bold green]{task.description}"),
                    BarColumn(),
                    TextColumn("[bold yellow]{task.completed}/{task.total} done"),
                    TimeElapsedColumn(),
                ) as progress:

                task = progress.add_task("Collecting jobs", total=max_results)
                print(f" 🔃 Collecting results (max = {max_results}")
                
                while len(jobs) < max_results:
        
                    url_1,url_2 = base_url.split('?')
                    url=f"{url_1}/{page_num}?{url_2}"
                    await page.goto(url,wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(3500)
                    await _dismiss_modal(page)

                    remaining = max_results - len(jobs)
                    processed_cards=1
                
                    # Parse job cards
                    cards =  await page.locator("div.job_result_two_pane_v2").all()
                    #print(f" 🔃 Collecting results (max={remaining}, page={page_num})...")

                    for card in cards:
                        await card.click()
                        await page.wait_for_timeout(2500)
                        await _dismiss_modal(page)
                        info = await _read_details(page)
                        jobs.append(info)
                        progress.update(
                            task,
                            completed=len(jobs),
                            description=f"Page {page_num} [{processed_cards}/{len(cards)}]"
                            )
                        processed_cards+=1
                        if len(jobs) >= max_results:
                            break
                    
                    if len(cards) < RESULTS_PER_PAGE:
                        break

                    page_num+=1


        except Exception as e:
            print(f"  ✗ Error: {e}")
        finally:
            await page.close()
            await context.close()

    return jobs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run_interactive() -> None:
    W=70
    """Prompt-driven session used when no CLI arguments are supplied."""
    print("-"*W)
    keywords = input("Keywords: ").strip()
    print("-"*W)
    location = input("Location (blank = USA): ").strip()

    date_filter = _prompt_multi("Date Filter", DATE_FILTER)
    apply_type = _prompt_multi("Apply Type", APPLY_TYPE)
 
    experience = _prompt_multi("Experience level", EXPERIENCE, False)
    employment_type = _prompt_multi("Employment type", EMPLOYMENT_TYPE)
    work_mode = _prompt_multi("Work mode", WORK_MODE)
 
    print("-"*W)
    salary_floor = input("\nMin salary ? (blank = no floor salary): ").strip()
    salary_ceil = input("\nMax salary ? (blank = no ceil salary): ").strip()
    print("-"*W)
    
    radius = input("Radius in km (blank to skip): ").strip()
    print("-"*W)

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
    jobs = await scrape_jobs(
        base_url = url,
        max_results = 25,
    )
 
    # Derive output filename: use the caller-supplied name or generate one
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ziprecruiter_jobs_{ts}.json"
    
    if len(jobs)>0:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(jobs)} jobs → {filename}")


if __name__=="__main__":
    asyncio.run(_run_interactive())