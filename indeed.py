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

SEARCH_QUERY    = "Python Developer"
SEARCH_LOCATION = "United States"
MAX_PAGES       = 5          # number of result pages to scrape
OUTPUT_CSV      = "indeed_jobs.csv"
OUTPUT_JSON     = "indeed_jobs.json"
HEADLESS        = False      # set True for silent mode (more likely to be blocked)
SLOW_MO         = 50         # ms delay between actions (helps avoid detection)


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
    """
    Derive country from a location string.
    Handles patterns like:
      "Austin, TX 78701"          → "United States"
      "London, England"           → "United Kingdom"
      "Remote"                    → "Remote"
      "Paris, Île-de-France"      → "France"
    Falls back to returning the last comma-separated token when unknown.
    """
    if not location:
        return ""

    # Common US state abbreviations
    us_states = {
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
        "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
        "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
        "TX","UT","VT","VA","WA","WV","WI","WY","DC","PR","GU","VI","AS","MP",
    }

    # Keyword → country mapping
    keyword_map = {
        "united states": "United States",
        "usa":           "United States",
        "u.s.":          "United States",
        "united kingdom": "United Kingdom",
        "uk":            "United Kingdom",
        "england":       "United Kingdom",
        "scotland":      "United Kingdom",
        "wales":         "United Kingdom",
        "canada":        "Canada",
        "australia":     "Australia",
        "germany":       "Germany",
        "deutschland":   "Germany",
        "france":        "France",
        "india":         "India",
        "remote":        "Remote",
    }

    loc_lower = location.lower()

    # Check keyword map first
    for keyword, country in keyword_map.items():
        if keyword in loc_lower:
            return country

    # Check for US state abbreviations (e.g. "Austin, TX" or "TX 78701")
    parts = [p.strip() for p in location.replace(",", " ").split()]
    for part in parts:
        # Strip zip codes: a pure digit string
        if part.isdigit():
            continue
        # State abbrev is 2 uppercase letters
        clean = part.upper().rstrip(".")
        if clean in us_states:
            return "United States"

    # Fallback: return the last comma-separated token
    tokens = [t.strip() for t in location.split(",")]
    return tokens[-1] if tokens else location


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
        permissions=["geolocation"],
    )

    # Remove webdriver flag
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)

    return browser, context


# ─── Sign-in wall bypass ──────────────────────────────────────────────────────

async def dismiss_signin(page: Page) -> bool:
    """
    Detect and close Indeed's sign-in modal/wall.
    Returns True if a modal was found and dismissed, False otherwise.
    """
    # Selectors that Indeed uses for sign-in overlays (updated periodically)
    close_selectors = [
        # Generic "close" / "dismiss" buttons on the modal
        'button[aria-label="close"]',
        'button[aria-label="Close"]',
        '[data-testid="modal-close-button"]',
        '.icl-CloseButton',
        'button.icl-CloseButton',
        # "Skip" / "Not now" links
        'a[data-tn-element="skip-signin-link"]',
        'button[data-tn-element="skip-signin"]',
        '#indeed-ia-skip-link',
        # Overlay backdrop (clicking outside closes some modals)
        '.icl-Modal-backdrop',
    ]

    for selector in close_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2_000):
                await el.click()
                print(f"   ✓ Dismissed sign-in modal via: {selector}")
                await page.wait_for_timeout(800)
                return True
        except Exception:
            continue  # selector not found or not visible — try next

    # Check for a full-page sign-in redirect
    if "signin" in page.url or "login" in page.url:
        print("   ⚠️  Redirected to sign-in page — attempting to navigate back.")
        await page.go_back()
        await page.wait_for_load_state("domcontentloaded")
        return True

    return False


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
    from urllib.parse import urlencode
    start = (page_num - 1) * 10
    params = {
        "q":     query,
        "l":     location,
        "start": start,
        "sort":  "date",   # newest first — change to "relevance" if preferred
    }
    return "https://www.indeed.com/jobs?" + urlencode(params)


# ─── Job listing page ─────────────────────────────────────────────────────────

async def scrape_listing_page(page: Page) -> list[dict]:
    """
    Parse the search-results page and return a list of dicts with:
      job_id, title, company, location, salary, url
    Uses multiple selector strategies to handle Indeed's A/B layouts.
    """
    jobs = []

    # Wait for job cards to appear
    try:
        await page.wait_for_selector(
            '[data-testid="slider_item"], .job_seen_beacon, .tapItem',
            timeout=10_000,
        )
    except Exception:
        print("   ⚠️  No job cards found on this page.")
        return jobs

    # Gather all job card elements
    card_selectors = [
        '[data-testid="slider_item"]',
        '.job_seen_beacon',
        '.tapItem',
        'li.css-5lfssm',   # another variant
    ]
    cards = []
    for sel in card_selectors:
        cards = await page.query_selector_all(sel)
        if cards:
            break

    print(f"   Found {len(cards)} job cards.")

    for card in cards:
        job: dict = {}

        # ── job_id ──────────────────────────────────────────────────────────
        job_id = await card.get_attribute("data-jk") or \
                 await card.get_attribute("id") or ""
        job["job_id"] = job_id.replace("job_", "").strip()

        # ── title ───────────────────────────────────────────────────────────
        for sel in [
            '[data-testid="jobTitle"] span',
            'h2.jobTitle span[title]',
            'h2.jobTitle span',
            '.jcs-JobTitle span',
        ]:
            el = await card.query_selector(sel)
            if el:
                job["title"] = (await el.inner_text()).strip()
                break

        # ── company ─────────────────────────────────────────────────────────
        for sel in [
            '[data-testid="company-name"]',
            '.companyName',
            'span.css-92r8pb',
        ]:
            el = await card.query_selector(sel)
            if el:
                job["company"] = (await el.inner_text()).strip()
                break

        # ── location ────────────────────────────────────────────────────────
        for sel in [
            '[data-testid="text-location"]',
            '.companyLocation',
            'div.css-1restlb',
        ]:
            el = await card.query_selector(sel)
            if el:
                job["location"] = (await el.inner_text()).strip()
                break

        # ── salary (optional) ────────────────────────────────────────────────
        for sel in [
            '[data-testid="attribute_snippet_testid"]',
            '.salary-snippet-container',
            '.metadata.salary-snippet',
            'div.css-1cvvo1q',
        ]:
            el = await card.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                # Salary lines usually contain "$" or "per"
                if "$" in text or "per " in text.lower() or "year" in text.lower():
                    job["salary"] = text
                    break

        # ── URL ──────────────────────────────────────────────────────────────
        for sel in [
            'h2.jobTitle a',
            'a[data-jk]',
            'a.jcs-JobTitle',
        ]:
            el = await card.query_selector(sel)
            if el:
                href = await el.get_attribute("href") or ""
                if href.startswith("/"):
                    href = "https://www.indeed.com" + href
                job["url"] = href
                # Prefer job_id embedded in URL
                if not job.get("job_id") and "jk=" in href:
                    job["job_id"] = href.split("jk=")[1].split("&")[0]
                break

        if job.get("title"):  # only keep cards we actually parsed
            jobs.append(job)

    return jobs


# ─── Job detail page ──────────────────────────────────────────────────────────

async def scrape_job_detail(page: Page, job_url: str) -> dict:
    """
    Navigate to a single job page and extract:
      description, salary (if richer than listing), location refinement.
    Returns a dict with only the keys that were successfully found.
    """
    detail: dict = {}

    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(random.randint(800, 1_500))
        await handle_captcha_or_block(page)
        await dismiss_signin(page)
    except Exception as e:
        print(f"   ⚠️  Could not load job detail: {e}")
        return detail

    # ── description ─────────────────────────────────────────────────────────
    for sel in [
        '[data-testid="jobsearch-JobComponent-description"]',
        '#jobDescriptionText',
        '.jobsearch-jobDescriptionText',
        'div.js-match-insights-provider-tvvxwd',
    ]:
        el = await page.query_selector(sel)
        if el:
            detail["description"] = (await el.inner_text()).strip()
            break

    # ── salary (detail page often has more complete info) ────────────────────
    for sel in [
        '[data-testid="jobsearch-OtherJobDetailsContainer"] [data-testid="attribute_snippet_testid"]',
        '#salaryInfoAndJobType span',
        '.js-match-insights-provider-68m1xr',
        'span.css-19j1a75',
    ]:
        el = await page.query_selector(sel)
        if el:
            text = (await el.inner_text()).strip()
            if "$" in text or "per " in text.lower():
                detail["salary"] = text
                break

    # ── location (sometimes more precise on the detail page) ────────────────
    for sel in [
        '[data-testid="jobsearch-JobInfoHeader-subtitle"] [data-testid="inlineHeader-companyLocation"]',
        'div.css-6td0tu',
    ]:
        el = await page.query_selector(sel)
        if el:
            detail["location"] = (await el.inner_text()).strip()
            break

    return detail


# ─── Main scraper ─────────────────────────────────────────────────────────────

async def scrape_indeed(query: str, location: str, max_pages: int) -> list[Job]:
    """
    Orchestrates the full scrape:
      1. Iterates over search result pages.
      2. For each job card, visits the detail page for description & richer data.
      3. Returns a deduplicated list of Job objects.
    """
    jobs: list[Job] = []
    seen_ids: set[str] = set()

    async with async_playwright() as pw:
        browser, context = await build_context(pw)
        page = await context.new_page()

        for page_num in range(1, max_pages + 1):
            url = build_search_url(query, location, page_num)
            print(f"\n📄 Page {page_num}: {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await page.wait_for_timeout(random.randint(1_000, 2_000))
            except Exception as e:
                print(f"   ⚠️  Failed to load page {page_num}: {e}")
                break

            await handle_captcha_or_block(page)
            await dismiss_signin(page)

            # Check if we've gone past the last results page
            no_results = await page.query_selector('[data-testid="no-results"], .no_results')
            if no_results:
                print("   ℹ️  No more results — stopping pagination.")
                break

            listings = await scrape_listing_page(page)
            if not listings:
                print("   ℹ️  Empty listing page — stopping.")
                break

            for listing in listings:
                job_id = listing.get("job_id", "")
                if job_id and job_id in seen_ids:
                    continue
                if job_id:
                    seen_ids.add(job_id)

                job = Job(
                    title    = listing.get("title",    ""),
                    company  = listing.get("company",  ""),
                    location = listing.get("location", ""),
                    salary   = listing.get("salary",   ""),
                    url      = listing.get("url",      ""),
                    job_id   = job_id,
                )
                job.country = extract_country(job.location)

                # Fetch detail page for description (and possibly richer data)
                if job.url:
                    print(f"   🔎 {job.title} @ {job.company}")
                    detail = await scrape_job_detail(page, job.url)
                    if detail.get("description"):
                        job.description = detail["description"]
                    if detail.get("salary") and not job.salary:
                        job.salary = detail["salary"]
                    if detail.get("location") and not job.location:
                        job.location  = detail["location"]
                        job.country   = extract_country(job.location)

                    # Polite delay between detail requests
                    random_delay(1.0, 2.5)

                jobs.append(job)

            # Polite delay between listing pages
            random_delay(2.0, 4.0)

        await browser.close()

    return jobs


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