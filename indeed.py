import asyncio
import json
import os
import sys
import argparse
import getpass
import re
from datetime import datetime
from urllib.parse import quote_plus

from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from playwright.async_api import (
    async_playwright,
    Page,
)



EXPERIENCE = {
    "Any":          "",
    "Entry Level":  "explvl(ENTRY_LEVEL)",
    "Mid Level":    "explvl(MID_LEVEL)",
    "Senior Level": "explvl(SENIOR_LEVEL)",
}

SORT_BY = {
    "Relevance": "",
    "Date":    "date",
}

JOB_TYPE = {
    "Any"       :      "",
    "Full Time" : "CF3CP", #attr
    "Part Time" : "75GKK",
    "Contract"  : "NJXCK",
    "Freelance" : "ZG59D",
    "Internship": "VDTG7",
}

WORK_TYPE = {
    "Any":    "",
    "Remote": "DSQF7",
    "Hybrid": "PAXZC",
}

DATE_POSTED = {
    "All"          : "",
    "Last 24 hours": "1",
    "Last 3 days"  : "3",
    "Last 7 days"  : "7",
    "Last 14 days" : "14",
}

BASE_SEARCH_URL = "https://www.indeed.com/jobs?"
TIMEOUT_PAGE_LOAD = 15000
TIMEOUT_COOL_DOWN = 3500


def build_search_url(
    keywords: str,
    location: str = "",
    date_posted: str | None = None,
    sort_by: str | None = None,
    experience: str | None = None,
    job_type: list[str] | None = None,
    work_type: list[str] | None = None,
) -> str:

    params = {}
    params["q"] = keywords

    if location:
        params["l"] = location.strip()

    if date_posted:
        params["fromage"] = DATE_POSTED[date_posted.strip()]
    
    if sort_by:
        params['sort'] = SORT_BY[sort_by.strip()]

    query_1 = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())


    query_2 = ""
    if job_type:
        jt = '|'.join(JOB_TYPE[j] for j in job_type)
        query_2+=f"attr({jt}%2COR)" if len(job_type)>1 else f"attr({jt}"+")"
    
    if work_type:
        wt = '|'.join(WORK_TYPE[w] for w in work_type)
        query_2+=f"attr({wt}%2COR)" if len(work_type)>1 else f"attr({wt}"+")"

    if experience:
        query_2+=EXPERIENCE[experience]

    if query_2:
        return f"{BASE_SEARCH_URL}{query_1}&sc=0kf:{quote_plus(query_2)}%3B&from=searchOnDesktopSerp"
    return f"{BASE_SEARCH_URL}{query_1}%3B&from=searchOnDesktopSerp"




async def _dismiss_modal(page: Page) -> None:
    pass

async def _read_details(page: Page) -> dict | None:
    pass





def _prompt_multi(label: str, mapping: dict, single: bool = True) -> list | str | None:
    temp = {}
    W = 70

    print("-" * W)
    print(f"\n{label}:")

    for k, v in enumerate(mapping.keys(), 1):
        temp[k] = v
        print(f"  {k} : {v}")
    while True:
        multiple_answers = "Comma-separated" if not single else "Single answer"
        raw = input(f"{label} ({multiple_answers}, or blank for Any): ").strip()
        correct_choices = all(int(i) in range(1,len(mapping.keys())+1) for i in raw.split(','))
        if raw=="" or correct_choices:
            break
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




 
async def _run(
    keywords: str,
    location: str,
    experience: str | None,
    job_type: list[str] | None,
    work_type: list[str] | None,
    date_posted: str | None,
    sort_by: str | None,
    max_results: int,
) -> None:

    url = build_search_url(
        keywords=keywords,
        location=location,
        experience=experience,
        job_type=job_type,
        work_type=work_type,
        date_posted=date_posted,
        sort_by=sort_by,
    )
 
    print(f" 🔅 URL : {url}")
    
    '''
    jobs = await scrape_jobs(
        url,
        max_results=max_results,
    )
 
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = output if output else f"indeed_jobs_{ts}.json"
    
    if len(jobs)>0:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(jobs)} jobs → {filename}")
    '''
 
 
 
async def _run_interactive() -> None:
    keywords       = input("Keywords: ").strip()
    location       = input("Location (blank = all): ").strip()
 
    experience     = _prompt_multi("Experience Level", EXPERIENCE)
    date_posted    = _prompt_multi("Client History",   DATE_POSTED)
    job_type       = _prompt_multi("Project Length",   JOB_TYPE, single=False)
    work_type      = _prompt_multi("Hours Per Week",   WORK_TYPE, single=False)
    sort_by        = _prompt_multi("Sort By",          SORT_BY,)
 
    raw_max = input("Max results [25]: ").strip()
    max_results = int(raw_max) if raw_max.isdigit() else 25

 
    await _run(
        keywords = keywords,
        location = location,
        experience = experience,
        date_posted = date_posted,
        job_type = job_type,
        work_type = work_type,
        sort_by=sort_by,
        max_results=max_results,
    )
 
    

 


def main() -> None:

    asyncio.run(_run_interactive())



if __name__ == "__main__":
    main()