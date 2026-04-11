import asyncio
import json
import random
import re
import argparse
from datetime import datetime
from urllib.parse import urlencode, urljoin

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
BASE_URL       = "https://www.indeed.com/jobs"
JOBS_PER_PAGE  = 15
NAV_TIMEOUT    = 35_000      # ms
MAX_RETRIES    = 3
RETRY_BASE_SEC = 3.0         # doubled per retry

DATE_POSTED_MAP = {
    "any":   "",
    "today": "1",
    "3days": "3",
    "week":  "7",
    "month": "14",
}
JOB_TYPE_MAP = {
    "fulltime":   "fulltime",
    "parttime":   "parttime",
    "contract":   "contract",
    "internship": "internship",
    "temporary":  "temporary",
}
EXPERIENCE_MAP = {
    "entry":     "entry_level",
    "mid":       "mid_level",
    "senior":    "senior_level",
    "director":  "director",
    "executive": "executive",
}
REMOTE_MAP = {
    "remote": "telecommute",
    "hybrid": "hybrid",
    "onsite": "",
}
EDUCATION_MAP = {
    "associate":   "associate",
    "bachelor":    "bachelor",
    "master":      "master",
    "doctorate":   "doctorate",
    "high_school": "high_school",
}

# Selector fallback lists (Indeed A/B-tests layouts constantly)
CARD_SELECTORS    = [".job_seen_beacon", '[data-testid="slider_item"]',
                     ".tapItem", ".jobsearch-SerpJobCard", "li.css-1ac2h1w"]
TITLE_SELECTORS   = ['[data-testid="jobTitle"] span', ".jcs-JobTitle span",
                     "h2.jobTitle span", "h2 a span"]
COMPANY_SELECTORS = ['[data-testid="company-name"]', ".companyName", ".company"]
LOC_SELECTORS     = ['[data-testid="text-location"]', ".companyLocation",
                     '[data-testid="jobsearch-JobInfoHeader-text"]']
SALARY_SELECTORS  = ['[data-testid="attribute_snippet_testid"]',
                     ".salary-snippet-container", ".estimated-salary",
                     '[data-testid="salary-snippet"]']
DATE_SELECTORS    = ['[data-testid="myJobsStateDate"]', ".date", "span.date",
                     '[data-testid="job-age"]']
SNIPPET_SELECTORS = [".job-snippet", ".summary", '[data-testid="job-snippet"]',
                     ".underShelfFooter .heading6"]
LINK_SELECTORS    = ["h2.jobTitle a", "a.jcs-JobTitle", "a[id^='job_']",
                     "h2 a[data-jk]"]
DESC_SELECTORS    = ["#jobDescriptionText", ".jobsearch-jobDescriptionText",
                     '[data-testid="jobDescriptionText"]',
                     ".jobsearch-JobComponent-description"]
RATING_SELECTORS  = ['[data-testid="company-rating"]', ".ratingNumber",
                     ".companyRating"]


# ──────────────────────────────────────────────────────────────────────────────
# Search parameters
# ──────────────────────────────────────────────────────────────────────────────

def build_indeed_url(
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

    query:             str       = ""
    location:          str       = ""
    # ── Indeed URL filters ───────────────────────────────────────────────────
    date_posted:       str       = "any"
    job_type:          str       = ""
    experience:        str       = ""
    remote:            str       = ""
    education:         str       = ""
    radius:            int       = 0
    salary_min_raw:    int       = 0
    exclude_kw:        list[str] = []
    exact_phrase:      str       = ""
    company:           str       = ""



    params: dict[str,str] = {}
    q_parts = []

    if exact_phrase:
        q_parts.append(f'"{exact_phrase}"')
    q_parts.append(query)

    for kw in exclude_kw:
        q_parts.append(f"-{kw.strip()}")
    params["q"] = " ".join(q_parts).strip()

    if location:
        params["l"] = location
    
    dp = DATE_POSTED_MAP.get(date_posted, "")
    if dp:
        params["fromage"] = dp

    jt = JOB_TYPE_MAP.get(job_type, "")
    if jt:
        params["jt"] = jt

    ex = EXPERIENCE_MAP.get(experience, "")
    if ex:
        params["explvl"] = ex

    rm = REMOTE_MAP.get(remote, "")
    if rm:
        params["remotejob"] = rm

    ed = EDUCATION_MAP.get(education, "")
    if ed:
        params["edlvl"] = ed

    if radius > 0:
        params["radius"] = radius

    if salary_min_raw > 0:
        params["salaryType"] = "yearly"
        params["salary"] = salary_min_raw

    if company:
        params["rbc"]  = company
        params["rbcb"] = "company"

    return BASE_URL + "?" + urlencode(params)