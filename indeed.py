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
    """Best-effort country extraction from Indeed location strings."""
    location = location.strip()
    # Indeed US jobs often have "City, ST" or "Remote" — treat as US
    us_indicators = ["remote", "united states", " al ", " ak ", " az ", " ar ",
                     " ca ", " co ", " ct ", " de ", " fl ", " ga ", " hi ",
                     " id ", " il ", " in ", " ia ", " ks ", " ky ", " la ",
                     " me ", " md ", " ma ", " mi ", " mn ", " ms ", " mo ",
                     " mt ", " ne ", " nv ", " nh ", " nj ", " nm ", " ny ",
                     " nc ", " nd ", " oh ", " ok ", " or ", " pa ", " ri ",
                     " sc ", " sd ", " tn ", " tx ", " ut ", " vt ", " va ",
                     " wa ", " wv ", " wi ", " wy ", " dc "]
    loc_lower = f" {location.lower()} "
    for indicator in us_indicators:
        if indicator in loc_lower:
            return "United States"

    # If the location contains a comma, the last part is often country/state
    parts = [p.strip() for p in location.split(",")]
    if len(parts) >= 2:
        return parts[-1]
    return location  # fallback: return full location


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
    """
    Detect and dismiss the Indeed sign-in / continue modal.
    Returns True if a modal was found and dismissed.
    """
    selectors = [
        # "Continue without signing in" / "Skip" button variants
        'button[data-testid="skip-sign-in-button"]',
        'button:has-text("Continue without signing in")',
        'button:has-text("Skip")',
        'a:has-text("Skip")',
        'button:has-text("Continue as guest")',
        '[aria-label="Close"]',
        'button[class*="close"]',
        # Modal overlay dismiss
        '#indeed-ia-skip-link',
        'a[data-testid="noSignIn"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                print(f"  ↳ Dismissed sign-in modal ({sel})")
                await page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
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
    """Extract job cards from a search results page."""
    await page.wait_for_timeout(2000)
    await dismiss_signin(page)
    await handle_captcha_or_block(page)

    # Wait for job cards
    try:
        await page.wait_for_selector('[data-testid="slider_item"], .job_seen_beacon, .jobsearch-ResultsList li', timeout=10000)
    except Exception:
        print("  ⚠ Could not find job cards on this page")
        return []

    cards = await page.query_selector_all(
        '[data-testid="slider_item"], .job_seen_beacon'
    )
    print(f"  Found {len(cards)} job cards")

    jobs_basic = []
    for card in cards:
        try:
            # Job ID / link
            link_el = await card.query_selector('a[data-jk], a[id^="job_"], h2 a')
            job_url, job_id = "", ""
            if link_el:
                href    = await link_el.get_attribute("href") or ""
                data_jk = await link_el.get_attribute("data-jk") or ""
                job_id  = data_jk
                job_url = "https://www.indeed.com" + href if href.startswith("/") else href

            # Title
            title_el = await card.query_selector('h2.jobTitle span, [data-testid="jobTitle"] span, h2 a span')
            title = (await title_el.inner_text()).strip() if title_el else ""

            # Company
            comp_el = await card.query_selector('[data-testid="company-name"], .companyName')
            company = (await comp_el.inner_text()).strip() if comp_el else ""

            # Location
            loc_el = await card.query_selector('[data-testid="text-location"], .companyLocation')
            location = (await loc_el.inner_text()).strip() if loc_el else ""

            # Salary (may not be present on all cards)
            sal_el = await card.query_selector('[data-testid="attribute_snippet_testid"], .salary-snippet-container, .metadataContainer .attribute_snippet')
            salary = (await sal_el.inner_text()).strip() if sal_el else ""

            jobs_basic.append({
                "title":    title,
                "company":  company,
                "location": location,
                "salary":   salary,
                "url":      job_url,
                "job_id":   job_id,
            })
        except Exception as e:
            print(f"  ⚠ Card parse error: {e}")
            continue

    return jobs_basic


# ─── Job detail page ──────────────────────────────────────────────────────────

async def scrape_job_detail(page: Page, job_url: str) -> dict:
    """Navigate to the job detail page and extract full description + salary."""
    if not job_url:
        return {"description": "", "salary": ""}

    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(random.randint(1000, 2000))
        await dismiss_signin(page)
        await handle_captcha_or_block(page)

        # Description
        desc_el = await page.query_selector(
            '#jobDescriptionText, [data-testid="jobsearch-jobDescriptionText"], .jobsearch-jobDescriptionText'
        )
        description = (await desc_el.inner_text()).strip() if desc_el else ""

        # Salary (detail page often has more complete salary info)
        sal_el = await page.query_selector(
            '[data-testid="attribute_snippet_testid"], '
            '#salaryInfoAndJobType .attribute_snippet, '
            '.jobsearch-JobMetadataHeader-item span'
        )
        salary = (await sal_el.inner_text()).strip() if sal_el else ""

        return {"description": description, "salary": salary}

    except Exception as e:
        print(f"    ⚠ Detail page error: {e}")
        return {"description": "", "salary": ""}


# ─── Main scraper ─────────────────────────────────────────────────────────────

async def scrape_indeed(query: str, location: str, max_pages: int) -> list[Job]:
    all_jobs: list[Job] = []

    async with async_playwright() as playwright:
        browser, context = await build_context(playwright)
        page: Page = await context.new_page()

        # Block unnecessary resources to speed things up
        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", lambda r: r.abort())

        try:
            for page_num in range(1, max_pages + 1):
                url = build_search_url(query, location, page_num)
                print(f"\n📄 Page {page_num}: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                random_delay(1.5, 2.5)

                jobs_basic = await scrape_listing_page(page)
                if not jobs_basic:
                    print("  No jobs found — stopping pagination.")
                    break

                # Check if we've hit the last page (Indeed repeats page 1 data)
                if page_num > 1 and all_jobs:
                    first_ids = {j.job_id for j in all_jobs[:10]}
                    new_ids   = {j["job_id"] for j in jobs_basic}
                    if new_ids and new_ids.issubset(first_ids):
                        print("  Duplicate page detected — reached end of results.")
                        break

                for i, basic in enumerate(jobs_basic, 1):
                    print(f"  [{i}/{len(jobs_basic)}] {basic['title']} @ {basic['company']}")

                    # Fetch detail page for description + better salary
                    detail = await scrape_job_detail(page, basic["url"])
                    random_delay(1.0, 2.0)

                    location_str = basic["location"]
                    job = Job(
                        title=basic["title"],
                        company=basic["company"],
                        location=location_str,
                        country=extract_country(location_str),
                        salary=detail["salary"] or basic["salary"],
                        description=detail["description"],
                        url=basic["url"],
                        job_id=basic["job_id"],
                    )
                    all_jobs.append(job)

                    # Go back to results for next card
                    await page.go_back(wait_until="domcontentloaded")
                    await page.wait_for_timeout(800)

                random_delay(2.0, 4.0)  # pause between pages

        finally:
            await browser.close()

    return all_jobs


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