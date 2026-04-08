import pandas as pd
import asyncio
import json
import re
import argparse
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
        date_filter:str = "any",
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
    elif date_filter and date_filter !="any":
        tpr=DATE_FILTERS.get(date_filter,"")
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
#  Helpers
# ─────────────────────────────────────────────
 
async def _dismiss_modal(page):
    """Close any sign-in / cookie modal."""

    MODAL_DISMISS_SELECTORS = [
    # Generic dismiss buttons
    "button[aria-label='Dismiss']",
    "button[aria-label='dismiss']",
    # Sign-in modal X button
    ".sign-in-modal__outlet-btn",
    ".contextual-sign-in-modal__modal-dismiss-btn",
    # Tracking-name based (works across many modal variants)
    "[data-tracking-control-name*='dismiss']",
    "[data-tracking-control-name*='modal_dismiss']",
    # Cookie consent (EU)
    "#artdeco-global-alert-container button",
    # Generic modal close
    ".modal__dismiss",
    "button[data-modal-dismiss]",
    ".artdeco-modal__dismiss",
    "[aria-label='Close']",
    "[aria-label='close']",
    ]

    for sel in MODAL_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first

            if await btn.is_visible(timeout=500):
                await btn.click()
                await page.wait_for_timeout(300)
                return
        except Exception:
            pass

        # Escape key dismisses many overlays (even hard ones remove the CSS block)
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass


async def _get_text(locator, *selectors, timeout=500) -> str:
    """Try multiple CSS selectors, return first non-empty text."""
    for sel in selectors:
        try:
            t = await locator.locator(sel).first.inner_text(timeout=timeout)
            if t.strip():
                return t.strip()
        except Exception:
            pass
    return ""

async def _fetch_description(page) -> str:
    print('Starting Description Extraction')
    # 1. Wait for the detail panel to render (any of these anchors is enough)
    panel_anchors = [
        ".show-more-less-html",
        ".jobs-description",
        ".description__text",
        ".jobs-description-content__text",
    ]
    for anchor in panel_anchors:
        try:
            await page.wait_for_selector(anchor, timeout=2000, state="attached")
            break
        except Exception:
            pass
 
    # 2. Expand "Show more" — class selector first, text-based fallback
    expanded = False
    for expand_sel in [
        "button.show-more-less-html__button",
        "button.show-more-less-html__button--more",
        "button.jobs-description__footer-button",
        "footer.show-more-less-html button",
    ]:
        try:
            btn = page.locator(expand_sel).first
            if await btn.is_visible(timeout=700):
                await btn.click()
                await page.wait_for_timeout(400)
                expanded = True
                break
        except Exception:
            pass
 
    if not expanded:
        try:
            btn = page.locator("button", has_text="Show more").first
            if await btn.is_visible(timeout=600):
                await btn.click()
                await page.wait_for_timeout(400)
        except Exception:
            pass
 
    # 3. Try description selectors from most-specific to least-specific.
    #    (selector, minimum_char_length_to_accept)
    desc_candidates = [
        (".show-more-less-html__markup",                        100),  # logged-out classic
        (".jobs-description-content__text",                     100),  # newer logged-out
        (".jobs-description__content .jobs-box__html-content",  100),  # logged-in
        (".jobs-description__content",                          100),  # logged-in fallback
        (".description__text--rich",                            100),  # old layout
        (".description__text",                                  100),  # old layout fallback
        ("[class*='jobs-description']",                         100),  # broad, higher bar
    ]
 
    for sel, min_len in desc_candidates:
        try:
            els = page.locator(sel)
            count = await els.count()
            if count == 0:
                continue
            best = ""
            for i in range(min(count, 4)):
                try:
                    text = (await els.nth(i).inner_text(timeout=500)).strip()
                    if len(text) > len(best):
                        best = text
                except Exception:
                    continue
            if len(best) >= min_len:
                return best
        except Exception:
            continue
 
    return ""


# ─────────────────────────────────────────────
#  Scraper
# ─────────────────────────────────────────────


async def scrape_jobs(url : str, max_results: int = 25, headless: bool = True, fetch_descriptions: bool = True,) -> list:
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
            await page.goto(url,wait_until="domcontentloaded", timeout=3000)
            await page.wait_for_timeout(3500)
            await _dismiss_modal(page)

            
            # Scroll to load cards
            print(" 🔃 Loading results...")
            prev_count=0
            for _ in range(7):
                await page.keyboard.press("End")
                await page.wait_for_timeout(1100)
                cards=await page.locator("ul.jobs-search__results-list li").all()
                if len(cards) >= max_results or len(cards) == prev_count:
                    break
                prev_count=len(cards)
            
            # Parse job cards
            cards = await page.locator("ul.jobs-search__results-list li").all()
            if not cards:
                # Fallback selector
                cards=await page.locator("[data-entity-urn]").all()
            
            print(f" ❇️  Found {len(cards)} raw cards, extracting up to {max_results}...")

            for idx,card in enumerate(cards[:max_results],1):
                job={}
                try:
                    #── Card-level fields (no click needed) ──────────
                    #Title
                    job["title"]    = await _get_text(card,".top-card-layout__title","h3.base-search-card__title", ".job-search-card__title", "h3")
                    
                    #Company
<<<<<<< HEAD
                    for sel in ["h4.base-search-card__subtitle", ".job-search-card__company-name", "h4",]:
                        try:
                            t=await card.locator(sel).first.inner_text(timeout=800)
                            if t.strip():
                                job["company"] = t.strip()
                                break
                        except Exception:
                            pass
                    
                    #Location
                    for sel in [".base-search-card__metadata",]:
                        try:
                            t=await card.locator(sel).first.inner_text(timeout=800)
                            if t.strip():
                                job["location"] = t.strip()
                                break
                        except Exception:
                            pass

=======
                    job["company"]  = await _get_text(card,".topcard__org-name-link","h4.base-search-card__subtitle", ".job-search-card__company-name", "h4")

                    # Location
                    job["location"] = await _get_text(card,".topcard__flavor",".job-search-card__location", "span.job-search-card__location")
>>>>>>> location2

                    #Date posted
                    try:
                        time_el=card.locator("time").first
                        job["date_posted"]=(await time_el.inner_text(timeout=50)).strip()
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
 
                    if not job.get("title"):
                        continue

                    # ── Click card → load detail panel → grab description ──
                    if fetch_descriptions:
                        try:
                            print(f"   [{idx:02d}/{len(cards)}] {job['title'][:55]} — fetching description...")
                            # Scroll card into view & click its heading link
                            link = card.locator("a").first
                            await link.scroll_into_view_if_needed()
                            await link.click()
                            await _dismiss_modal(page)   # modal may reappear
                            job["description"] = await _fetch_description(page)
                            num_text = await page.locator("figcaption.num-applicants__caption").inner_text()
                            job["num_applicants"] = re.findall(r'\d+', num_text)[0]
                            additional_info=await page.locator("ul.description__job-criteria-list").all_inner_texts()
                            job["additional_info"] = additional_info[0]


                        except Exception as e:
                            job["description"] = ""
                    else:
                        print(f"   [{idx:02d}/{len(cards)}] {job['title'][:60]}")
 
                    jobs.append(job)
                    await page.go_back(wait_until="load")

                except Exception:
                    continue


        except PlaywrightTimeoutError:
            print("  ✗ Timeout – LinkedIn took too long to respond.")
        except Exception as e:
            print(f"  ✗ Error: {e}")
        finally:
            await browser.close()

    return jobs


def display_results(jobs: list, config:dict):
    W=70
    print()
    print("="*W)
    print(f"  {'LINKEDIN JOB SEARCH RESULTS':^{W-4}}")
    print("═"*W)
    kv=[
        ("Keywords",     config.get("keywords", "—")),
        ("Location",     config.get("location", "—")),
        ("Date filter",  config.get("date_filter", "any")),
        ("Experience",   ", ".join(config.get("experience", [])) or "all"),
        ("Job type",     ", ".join(config.get("job_type", [])) or "all"),
        ("Work type",    ", ".join(config.get("work_type", [])) or "all"),
        ("Easy Apply",   "✓" if config.get("easy_apply") else "—"),
        ("Active hiring","✓" if config.get("actively_hiring") else "—"),
        ("Sort by",      config.get("sort_by", "recent")),
        ("Distance",     f"{config['distance']} mi" if config.get("distance") else "—"),
        ("Results",      str(len(jobs))),
    ]

    for k,v in kv:
        print(f" {k:<17} {v}")
    print("-"*W)

    if not jobs:
        print(" ❌ No results found.")
        return

    for i , job in enumerate(jobs,1):
        ea = " ⚡ Easy Apply" if job.get("easy_apply") else ""
        print()
        print(f"  [{i:02d}] {job.get('title', 'N/A')}{ea}")
        print(f"       🏢  {job.get('company', 'N/A')}")
        print(f"       📍  {job.get('location', 'N/A')}")
        print(f"       📅  {job.get('date_posted', 'N/A')}")
        print(f"       🆘  {job.get('num_applicants', 'N/A')}")
        #print(f"       ℹ️  {job.get('description', 'N/A')}")
        if job.get("url"):
            print(f"       🔗  {job['url']}")

        desc = job.get("description", "")
        if desc:
            preview_lines = [l.strip() for l in desc.splitlines() if l.strip()][:3]
            preview = " • ".join(preview_lines)
            if len(preview) > 300:
                preview = preview[:200] + "..."
            print(f"       📝  {preview}")
        
    print()
    print("-"*W)

    #Save to JSON
    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"linkedin_jobs_{ts}.json"
    output= {
        "meta": {**config, "scraped_at": datetime.now().isoformat(), "total": len(jobs)},
        "jobs": jobs,
    }
    
    with open(filename,"w",encoding="utf-8") as f:
        json.dump(output,f,indent=2,ensure_ascii=False)
    
    print(f"  💾  Saved {len(jobs)} jobs → {filename}")
    print("═" * W)
    print()


# ─────────────────────────────────────────────
#  Interactive mode
# ─────────────────────────────────────────────
 
def _pick(prompt: str, options: dict, multi: bool = False, required: bool = True) -> list | str | None:
    """Generic menu picker."""
    keys = list(options.keys())
    print(f"\n  {prompt}")
    for i, (k, label) in enumerate(options.items(), 1):
        print(f"    {i:2}. {label:<25}  [{k}]")
    if multi:
        print("    Enter numbers separated by commas, or press Enter to skip.")
        raw = input("  ➤ ").strip()
        if not raw:
            return []
        chosen = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(keys):
                chosen.append(keys[int(part) - 1])
        return chosen
    else:
        if not required:
            print("    Press Enter to skip.")
        raw = input("  ➤ ").strip()
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        # allow typing the key directly
        if raw in keys:
            return raw
        return None


async def interactive_mode():
    W = 70
    print()
    print("╔" + "═" * (W - 2) + "╗")
    print("║" + " ADVANCED LINKEDIN JOB SEARCHER ".center(W - 2) + "║")
    print("╚" + "═" * (W - 2) + "╝")
    print()

    # ── Required ──────────────────────────────
    keywords=input("  🔍  Job title / keywords: ").strip()
    if not keywords:
        print(" ❌ Keywords are required. Exiting.")
        return

    location=input(" 📍  Location (city, country, or 'Remote'): ").strip() or "Worldwide"

    # ── Date filter ───────────────────────────
    date_options={
        "1h":    "Last 1 hour  (ultra-fresh, fewest results)",
        "6h":    "Last 6 hours",
        "24h":   "Last 24 hours",
        "3d":    "Last 3 days",
        "week":  "Last week",
        "month": "Last month",
        "any":   "Any time (default)",
    }
    date_filter = _pick("📅  Date posted:", date_options, multi=False, required=False) or "any"

    # Custom seconds override
    print("\n  ⏱   Custom time window in seconds? (e.g. 3600 = 1h) Press Enter to skip.")
    raw_sec = input("  ➤ ").strip()
    custom_seconds = int(raw_sec) if raw_sec.isdigit() else None

    # ── Experience ────────────────────────────
    exp_options = {
        "internship": "Internship",
        "entry":      "Entry level",
        "associate":  "Associate",
        "mid":        "Mid-Senior level",
        "director":   "Director",
        "executive":  "Executive",
    }
    experience = _pick("🎓  Experience level (multi-select):", exp_options, multi=True) or []

    # ── Job type ──────────────────────────────
    jtype_options = {
        "fulltime":   "Full-time",
        "parttime":   "Part-time",
        "contract":   "Contract",
        "temporary":  "Temporary",
        "internship": "Internship",
        "volunteer":  "Volunteer",
    }
    job_type = _pick("💼  Job type (multi-select):", jtype_options, multi=True) or []

    # ── Work type ─────────────────────────────
    wtype_options = {
        "onsite": "On-site",
        "remote": "Remote",
        "hybrid": "Hybrid",
    }
    work_type = _pick("🏠  Work type (multi-select):", wtype_options, multi=True) or []

    # ── Sort ──────────────────────────────────
    sort_options = {"recent": "Most recent", "relevant": "Most relevant"}
    sort_by = _pick("🔢  Sort by:", sort_options, multi=False, required=False) or "recent"

    # ── Toggles ───────────────────────────────
    print("\n  ⚡  Easy Apply only? [y/N]: ", end="")
    easy_apply = input().strip().lower() == "y"
 
    print("  🟢  Actively Hiring companies only? [y/N]: ", end="")
    actively_hiring = input().strip().lower() == "y"  

    # ── Distance ─────────────────────────────
    print("\n  📏  Search radius in miles? (e.g. 25, 50) Press Enter to skip.")
    raw_dist = input("  ➤ ").strip()
    distance = int(raw_dist) if raw_dist.isdigit() else None

    # ── Max results ───────────────────────────
    print(f"\n  📊  Max results [default: 25]: ", end="")
    raw_max = input().strip()
    max_results = int(raw_max) if raw_max.isdigit() else 25

    # ── Descriptions ─────────────────────────
    print("\n  📝  Fetch full job descriptions? [Y/n]: ", end="")
    fetch_descriptions = input().strip().lower() != "n"

    # ── Headless ─────────────────────────────
    print("  🖥   Run headless (no browser window)? [Y/n]: ", end="")
    headless = input().strip().lower() != "n"

    # ── Build & run ───────────────────────────
    config = dict(
        keywords=keywords, location=location, date_filter=date_filter,
        experience=experience, job_type=job_type, work_type=work_type,
        easy_apply=easy_apply, actively_hiring=actively_hiring,
        sort_by=sort_by, distance=distance, custom_seconds=custom_seconds,
    )

    url=build_linkedin_url(**config)

    print()
    print("─" * W)
    print(f"  Searching LinkedIn jobs...")
    jobs = await scrape_jobs(url, max_results=max_results, headless=headless, fetch_descriptions=fetch_descriptions)
    display_results(jobs, config)

# ─────────────────────────────────────────────
#  CLI mode
# ─────────────────────────────────────────────
 
def parse_args():
    parser = argparse.ArgumentParser(
        description="Advanced LinkedIn Job Searcher (Playwright)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  # Basic
  python linkedin_jobs_advanced.py -k "Python Developer" -l "London"
 
  # With filters
  python linkedin_jobs_advanced.py -k "Data Scientist" -l "Remote" \\
    --date 24h --experience mid director --job-type fulltime contract \\
    --work-type remote hybrid --easy-apply --sort recent --max 30
 
  # Custom time window (last 2 hours)
  python linkedin_jobs_advanced.py -k "DevOps Engineer" -l "Berlin" --seconds 7200
 
DATE OPTIONS
  1h / 2h / 3h / 6h / 12h / 24h / 3d / week / month / any
 
EXPERIENCE OPTIONS
  internship / entry / associate / mid / director / executive
 
JOB TYPE OPTIONS
  fulltime / parttime / contract / temporary / internship / volunteer
 
WORK TYPE OPTIONS
  onsite / remote / hybrid
""",
    )
    parser.add_argument("-k", "--keywords", help="Job title or keywords")
    parser.add_argument("-l", "--location", help="Location", default="")
    parser.add_argument("--date", default="any", help="Date filter (default: any)")
    parser.add_argument("--seconds", type=int, help="Custom time window in seconds (overrides --date)")
    parser.add_argument("--experience", nargs="*", help="Experience levels (multi)")
    parser.add_argument("--job-type", nargs="*", dest="job_type", help="Job types (multi)")
    parser.add_argument("--work-type", nargs="*", dest="work_type", help="Work types (multi)")
    parser.add_argument("--easy-apply", action="store_true", help="Easy Apply only")
    parser.add_argument("--active", action="store_true", dest="actively_hiring", help="Actively Hiring only")
    parser.add_argument("--sort", default="recent", choices=["recent", "relevant"], help="Sort order")
    parser.add_argument("--distance", type=int, help="Search radius in miles")
    parser.add_argument("--max", type=int, default=25, help="Max results (default: 25)")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window")
    parser.add_argument("--no-descriptions", action="store_true", help="Skip fetching descriptions (faster)")
    parser.add_argument("--json-only", action="store_true", help="Output raw JSON only")
    return parser.parse_args()
 
 
async def cli_mode(args):
    config = dict(
        keywords=args.keywords,
        location=args.location,
        date_filter=args.date,
        custom_seconds=args.seconds,
        experience=args.experience or [],
        job_type=args.job_type or [],
        work_type=args.work_type or [],
        easy_apply=args.easy_apply,
        actively_hiring=args.actively_hiring,
        sort_by=args.sort,
        distance=args.distance,
    )
    url = build_linkedin_url(**config)
    jobs = await scrape_jobs(url, max_results=args.max, headless=not args.no_headless, fetch_descriptions=not args.no_descriptions)
 
    if args.json_only:
        print(json.dumps(jobs, indent=2, ensure_ascii=False))
    else:
        display_results(jobs, config)


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────
 
def main():
    args = parse_args()
    if args.keywords:
        asyncio.run(cli_mode(args))
    else:
        asyncio.run(interactive_mode())
 
 
if __name__ == "__main__":
    main()