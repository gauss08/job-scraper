import asyncio
import json
import sys
from datetime import datetime
from urllib.parse import urlencode, quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ─────────────────────────────────────────────
#  Filter value maps  (LinkedIn URL params)
# ─────────────────────────────────────────────


DATE_FILTERS = {
    "1h":       "r3600",
    "2h":       "r7200",
    "3h":       "r10800",
    "6h":       "r21600",
    "12h":      "r43200",
    "24h":      "r86400",
    "3d":       "r259200",
    "week":     "r604800",
    "month":    "r2592000",
    "any":      "",
}

EXPERIENCE_LEVELS = {
    "internship":  "1",
    "entry":       "2",
    "associate":   "3",
    "mid":         "4",
    "director":    "5",
    "executive":   "6",
}

JOB_TYPES = {
    "fulltime":   "F",
    "parttime":   "P",
    "contract":   "C",
    "temporary":  "T",
    "internship": "I",
}

WORK_TYPES = {
    "onsite":  "1",
    "remote":  "2",
    "hybrid":  "3",
}

SORT_BY = {
    "relevant": "R",
    "recent":   "DD",
}

# ─────────────────────────────────────────────
#  URL builder
# ─────────────────────────────────────────────

def build_linkedin_url(
        keywords: str,
        location: str,
        data_filter:str = "any",
        experience: list = None,
        job_type: list = None,
        work_type: list = None,
        easy_apply: bool = False,
        actively_hiring: bool = False,
        sort_by: str = "recent",
        distance: int = None,
        custom_seconds: int = None,
    ) -> str:
    params={}

    params["keywords"]=keywords
    if location:
        params["location"]=location

    #Time
    if custom_seconds:
        params["f_TPR"]=f"r{custom_seconds}"
    elif data_filter and data_filter !="any":
        tpr=DATE_FILTERS.get(data_filter,"")
        if tpr:
            params["f_TPR"]= tpr
    
    # Experience (multi-select — comma separated)
    if experience:
        codes=[EXPERIENCE_LEVELS[e] for e in experience if e in EXPERIENCE_LEVELS]
        if codes:
            params["f_E"]= "%2C".join(codes)
    
    # Job type (multi-select)
    if job_type:
        codes=[JOB_TYPES[j] for j in job_type if j in  JOB_TYPES]
        if codes:
            params["f_JT"]= "%2C".join(codes)
    
    # Work type (multi-select)
    if work_type:
        codes=[WORK_TYPES[w] for w in work_type if w in WORK_TYPES]
        if codes:
            params["f_WT"] = "%2C".join(codes)

    # Toggles
    if easy_apply:
        params["f_EA"]="true"
    if actively_hiring:
        params["f_AL"]="true"
    
    # Sort
    params["sortBy"]=SORT_BY.get(sort_by,"DD")

    # Distance (miles)
    if distance:
        params["distance"]=str(distance)
    
    base = "https://www.linkedin.com/jobs/search/?"

    # Build manually to preserve %2C for multi-values
    parts=[]
    for k,v in params.items():
        print(k,v)
        parts.append(f"{k}={quote_plus(str(v))}") # if k not in ('f_E','f_JT','f_WT') else v
    
    return base+"&".join(parts)


# ─────────────────────────────────────────────
#  Scraper
# ─────────────────────────────────────────────


async def scrape_jobs(url : str, max_results: int = 25, headless: bool = True) -> list:
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

        page=await context.new_page()
        
        try:
            print(f" 🔅 Opening : {url[:90]}...")
            await page.goto(url,wait_until="documentloaded", timeout=30000)
            await page.wait_for_timeout(3500)

                        # Dismiss sign-in modal
            for sel in [
                "button[aria-label='Dismiss']",
                ".modal__dismiss",
                "button.sign-in-modal__outlet-btn",
                "[data-tracking-control-name*='dismiss']",
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        await page.wait_for_timeout(800)
                        break
                except Exception:
                    pass
            
            # Scroll to trigger lazy loading
            print(" 🔃 Loading results...")
            prev_count=0
            for _ in range(6):
                await page.keyboard.press("End")
                await page.wait_for_timeout(1200)
                cards=await page.locator("ul.jobs-search__results-list li").all()
                if len(cards) >= max_results or len(cards) == prev_count:
                    break
                prev_count=len(cards)
            
            # Parse job cards
            cards = await page.locator("ul.jobs-search__results-list li").all()
            if not cards:
                # Fallback selector
                cards=await page.locator("[data-entity-urn]").all()
            
            print(f" ❇️ Found {len(cards)} raw cards, extracting up to {max_results}...")

            for card in cards[:max_results]:
                job={}
                try:
                    #Title
                    for sel in ["h3.base-search-card__title", ".job-search-card__title", "h3"]:
                        try:
                            t=await card.locator(sel).first.inner_text(timeout=800)
                            if t.strip():
                                job["title"]=t.strip()
                                break
                        except Exception:
                            pass
                    
                    #Company
                    for sel in ["h4.base-search-card__subtitle", ".job-search-card__company-name", "h4"]:
                        try:
                            t=await card.locator(sel).firrst.innner_text(timeout=800)
                            if t.strip():
                                job["locator"] = t.strip()
                                break
                        except Exception:
                            pass
                    
                    #Date posted
                    try:
                        time_el=card.locator("time").first
                        job["date_posted"]=(await time_el.inner_text(timeout=800)).strip()
                        dt_attr=await time_el.get_attribute("datetime")
                        if dt_attr:
                            job["date_iso"]=dt_attr
                    except Exception:
                        pass
                        
                    #"Easy Apply" badge
                    try:
                        badges = await card.locator(".job-search-card__easy-apply-label, .result-benefits").all_inner_texts()
                        job["easy_apply"] = any("easy apply" in b.lower() for b in badges)
                    except Exception:
                        job["easy_apply"] = False
                    
                    # URL
                    try:
                        href = await card.locator("a").first.get_attribute("href")
                        if href:
                            job["url"] = href.split("?")[0]
                    except Exception:
                        pass
 
                    if job.get("title"):
                        jobs.append(job)

                except Exception:
                    continue

        except PlaywrightTimeoutError:
            print("  ✗ Timeout – LinkedIn took too long to respond.")
        except Exception as e:
            print(f"  ✗ Error: {e}")
        finally:
            await browser.close()

    return jobs