"""
Indeed Job Scraper using Playwright
Handles: sign-in wall bypass, pagination, job details extraction
Extracts: title, company, location/country, description, salary
"""

import asyncio
import json
import csv
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Optional
from playwright.async_api import async_playwright, Page, BrowserContext


# ─── Config ───────────────────────────────────────────────────────────────────

SEARCH_QUERY   = "Python Developer"
SEARCH_LOCATION = "United States"
MAX_PAGES      = 5          # number of result pages to scrape
OUTPUT_CSV     = "indeed_jobs.csv"
OUTPUT_JSON    = "indeed_jobs.json"
HEADLESS       = False      # set True for silent mode (more likely to be blocked)
SLOW_MO        = 50         # ms delay between actions (helps avoid detection)


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Job:
    title:       str = ""
    company:     str = ""
    location:    str = ""
    country:     str = ""
    salary:      str = ""
    description: str = ""
    url:         str = ""
    job_id:      str = ""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def random_delay(min_s: float = 1.5, max_s: float = 3.5):
    time.sleep(random.uniform(min_s, max_s))


def extract_country(location: str) -> str:
    pass


# ─── Browser setup ────────────────────────────────────────────────────────────

async def build_context(playwright) -> tuple:
    """Launch browser with stealth-friendly settings."""
    browser = await playwright.chromium.launch(
        headless=HEADLESS,
        slow_mo=SLOW_MO,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-size=1366,768",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
        # Permissions that a real browser would have
        permissions=["geolocation"],
    )

    # Remove webdriver flag
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)

    return browser, context


# ─── Sign-in wall bypass ──────────────────────────────────────────────────────

async def dismiss_signin(page: Page) -> bool:
    pass


async def handle_captcha_or_block(page: Page):
    """Pause and prompt user if a CAPTCHA or block page is detected."""
    title = await page.title()
    url   = page.url
    blocked_signals = ["captcha", "blocked", "verify", "robot", "access denied"]
    if any(s in title.lower() for s in blocked_signals) or \
       any(s in url.lower()   for s in blocked_signals):
        print("\n⚠️  CAPTCHA / block page detected!")
        print("   Please solve it in the browser window, then press Enter here...")
        input("   Press Enter to continue ▶ ")


# ─── Pagination URL builder ───────────────────────────────────────────────────

def build_search_url(query: str, location: str, page_num: int) -> str:
    """
    Indeed pagination: start=0 for page 1, start=10 for page 2, etc.
    """
    from urllib.parse import urlencode, quote_plus
    start = (page_num - 1) * 10
    params = {
        "q":   query,
        "l":   location,
        "start": start,
        "sort": "date",      # newest first — change to "relevance" if preferred
    }
    return "https://www.indeed.com/jobs?" + urlencode(params)


# ─── Job listing page ─────────────────────────────────────────────────────────

async def scrape_listing_page(page: Page) -> list[dict]:
        pass


# ─── Job detail page ──────────────────────────────────────────────────────────

async def scrape_job_detail(page: Page, job_url: str) -> dict:
        pass


# ─── Main scraper ─────────────────────────────────────────────────────────────

async def scrape_indeed(query: str, location: str, max_pages: int) -> list[Job]:
        pass


# ─── Output ───────────────────────────────────────────────────────────────────

def save_csv(jobs: list[Job], path: str):
    if not jobs:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(jobs[0]).keys()))
        writer.writeheader()
        writer.writerows([asdict(j) for j in jobs])
    print(f"✅ CSV saved → {path}")


def save_json(jobs: list[Job], path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(j) for j in jobs], f, indent=2, ensure_ascii=False)
    print(f"✅ JSON saved → {path}")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"🔍 Searching Indeed for: '{SEARCH_QUERY}' in '{SEARCH_LOCATION}'")
    print(f"   Max pages: {MAX_PAGES} | Headless: {HEADLESS}\n")

    jobs = asyncio.run(scrape_indeed(SEARCH_QUERY, SEARCH_LOCATION, MAX_PAGES))

    print(f"\n✨ Total jobs scraped: {len(jobs)}")
    save_csv(jobs,  OUTPUT_CSV)
    save_json(jobs, OUTPUT_JSON)