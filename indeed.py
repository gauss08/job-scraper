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
    "Entry Level":  "explvl(ENTRY_LEVEL);",
    "Mid Level":    "explvl(MID_LEVEL);",
    "Senior Level": "explvl(SENIOR_LEVEL);",
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
    experience: str | None = None,
    date_posted: str | None = None,
    sort_by: str | None = None,
    job_type: list[str] | None = None,
    work_type: list[str] | None = None,
) -> str:

    params = {}

    params["q"] = keywords

    params["l"] = location.strip() if client_location.strip() else ""

    if experience:
        params["sc"] = ",".join(EXPERIENCE[w] for w in experience)

    if job_type:
        params["client_hires"] = ",".join(CLIENT_HISTORY[w] for w in client_history)

    if project_length:
        params["duration_v3"] = ",".join(PROJECT_LENGTH[w] for w in project_length)

    if hours_per_week:
        params["workload"] = ",".join(HOURS_PER_WEEK[w] for w in hours_per_week)

    if salary_type:
        params["t"] = ",".join(SALARY_TYPE[w] for w in salary_type)

    params["sort"] = SORT_BY[sort_by] if sort_by else "relevance,desc"


    query = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())

    return f"{BASE_SEARCH_URL}{query}"



async def _dismiss_modal(page: Page) -> None:
    MODAL_DISMISS_SELECTORS = [
        "button.onetrust-close-btn-handler.banner-close-button",
        "button.air3-modal-close.modal-header-close-button",
    ]

    for sel in MODAL_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=TIMEOUT_COOL_DOWN):
                await btn.click()
                await page.wait_for_timeout(TIMEOUT_COOL_DOWN)
                return
        except Exception:
            pass

    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(TIMEOUT_COOL_DOWN)
    except Exception:
        pass






def _prompt_multi(label: str, mapping: dict, single: bool = False) -> list | str | None:
    temp = {}
    W = 70

    print("-" * W)
    print(f"\n{label}:")

    for k, v in enumerate(mapping.keys(), 1):
        temp[k] = v
        print(f"  {k} : {v}")
    while True:
        raw = input(f"{label} (comma-separated, or blank for Any): ").strip()
        if raw=="" or int(raw) in range(1,len(mapping.keys())+1):
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
    client_location: str,
    experience: list[str] | None,
    client_history: list[str] | None,
    project_length: list[str] | None,
    hours_per_week: list[str] | None,
    salary_type: list[str] | None,
    sort_by: str | None,
    max_results: int,
    login: bool,
    user_mail: str,
    password: str,
    output: str | None,
) -> None:
    url = build_search_url(
        keywords=keywords,
        client_location=client_location,
        experience=experience,
        client_history=client_history,
        project_length=project_length,
        hours_per_week=hours_per_week,
        salary_type=salary_type,
        sort_by=sort_by,
    )
 
    print(f" 🔅 URL : {url}")
 
    jobs, private_jobs = await scrape_jobs(
        url,
        max_results=max_results,
        login=login,
        user_mail=user_mail,
        password=password,
    )
 
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = output if output else f"upwork_jobs_{ts}.json"
    
    if len(jobs)>0:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(jobs)} jobs → {filename}")
 
    if not login and len(private_jobs) > 0:
        private_filename = f"private_jobs_{ts}.json"
        with open(private_filename, "w", encoding="utf-8") as f:
            json.dump(private_jobs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(private_jobs)} private job URLs → {private_filename}")
 
 
 
async def _run_interactive() -> None:
    keywords       = input("Keywords: ").strip()
    client_location = input("Client Location (blank = all): ").strip()
 
    experience     = _prompt_multi("Experience Level", EXPERIENCE)
    client_history = _prompt_multi("Client History",   CLIENT_HISTORY)
    project_length = _prompt_multi("Project Length",   PROJECT_LENGTH)
    hours_per_week = _prompt_multi("Hours Per Week",   HOURS_PER_WEEK)
    salary_type    = _prompt_multi("Salary Type",      SALARY_TYPE)
    sort_by        = _prompt_multi("Sort By",          SORT_BY, single=True)
 
    raw_max = input("Max results [25]: ").strip()
    max_results = int(raw_max) if raw_max.isdigit() else 25
 
    login     = input("Login? (y/n): ").strip().lower().startswith("y")

    if login:
        while True:
            pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            user_mail = input("User mail: ").strip() if login else ""
            if not re.fullmatch(pattern, user_mail):
                print(" ⚠️ Enter valit email address")
                continue
            break

        while True:
            password  = getpass.getpass("Password: ")  if login else ""
            if  password:
                break
            print(" ⚠️ Enter password")
    else:
        user_mail = ""
        password = ""
 
    await _run(
        keywords=keywords,
        client_location=client_location,
        experience=experience,
        client_history=client_history,
        project_length=project_length,
        hours_per_week=hours_per_week,
        salary_type=salary_type,
        sort_by=sort_by,
        max_results=max_results,
        login=login,
        user_mail=user_mail,
        password=password,
        output=None,
    )
 
 
 
def _build_parser() -> argparse.ArgumentParser:
    exp_choices    = list(EXPERIENCE.keys())
    hist_choices   = list(CLIENT_HISTORY.keys())
    length_choices = list(PROJECT_LENGTH.keys())
    hours_choices  = list(HOURS_PER_WEEK.keys())
    salary_choices = list(SALARY_TYPE.keys())
    sort_choices   = list(SORT_BY.keys())
 
    parser = argparse.ArgumentParser(
        prog="upwork_scraper",
        description=(
            "Scrape Upwork job listings into a JSON file.\n\n"
            "Run with no arguments to enter the interactive prompt."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        argument_default=argparse.SUPPRESS,
    )
 
    search = parser.add_argument_group("search")
 
    search.add_argument(
        "-k", "--keywords",
        metavar="QUERY",
        required=True,
        help="Free-text job search query (required).",
    )
    search.add_argument(
        "-l", "--location",
        metavar="LOCATION",
        default="",
        help="Client's country or region (default: no filter).",
    )
    search.add_argument(
        "-n", "--max-results",
        metavar="N",
        type=int,
        default=25,
        help="Maximum number of jobs to collect (default: 25).",
    )
    search.add_argument(
        "-o", "--output",
        metavar="FILE",
        default=None,
        help=(
            "Output JSON filename. "
            "Defaults to upwork_jobs_<YYYYMMDD_HHMMSS>.json if omitted."
        ),
    )
    search.add_argument(
        "--sort-by",
        metavar="SORT",
        choices=sort_choices,
        default="Relevance",
        help=f"Sort order. Choices: {sort_choices}. (default: Relevance)",
    )
 
    filters = parser.add_argument_group(
        "filters",
        "All filter flags accept one or more values (space-separated).",
    )
 
    filters.add_argument(
        "--experience",
        nargs="+",
        metavar="LEVEL",
        choices=exp_choices,
        default=None,
        help=f"Freelancer experience level(s). Choices: {exp_choices}.",
    )
    filters.add_argument(
        "--client-history",
        nargs="+",
        metavar="HISTORY",
        choices=hist_choices,
        default=None,
        help=f"Client hiring history. Choices: {hist_choices}.",
    )
    filters.add_argument(
        "--project-length",
        nargs="+",
        metavar="LENGTH",
        choices=length_choices,
        default=None,
        help=f"Expected project duration. Choices: {length_choices}.",
    )
    filters.add_argument(
        "--hours-per-week",
        nargs="+",
        metavar="HOURS",
        choices=hours_choices,
        default=None,
        help=f"Weekly time commitment. Choices: {hours_choices}.",
    )
    filters.add_argument(
        "--salary-type",
        nargs="+",
        metavar="TYPE",
        choices=salary_choices,
        default=None,
        help=f"Contract payment type. Choices: {salary_choices}.",
    )
 
    auth = parser.add_argument_group("authentication")
 
    auth.add_argument(
        "--login",
        action="store_true",
        default=False,
        help=(
            "Authenticate before scraping. Enables private-job data and "
            "Connects cost. Password is read securely from a prompt if "
            "--password is not supplied."
        ),
    )
    auth.add_argument(
        "--email",
        metavar="EMAIL",
        default="",
        help="Upwork account e-mail (used with --login).",
    )
    auth.add_argument(
        "--password",
        metavar="PASSWORD",
        default="",
        help=(
            "Upwork account password (used with --login). "
            "Omit to be prompted securely at runtime — "
            "passing it as a flag exposes the password in shell history."
        ),
    )
 
    return parser
 
 
async def _run_from_args(args: argparse.Namespace) -> None:
    password = getattr(args, "password", "")
 
    if args.login and not password:
        password = getpass.getpass("Password: ")
 
    await _run(
        keywords=args.keywords,
        client_location=args.location,
        experience=args.experience,
        client_history=args.client_history,
        project_length=args.project_length,
        hours_per_week=args.hours_per_week,
        salary_type=args.salary_type,
        sort_by=args.sort_by,
        max_results=args.max_results,
        login=args.login,
        user_mail=args.email,
        password=password,
        output=args.output,
    )
 



 


def main() -> None:
    parser = _build_parser()
 
    if len(sys.argv) == 1:
        asyncio.run(_run_interactive())
    else:
        args = parser.parse_args()
        asyncio.run(_run_from_args(args))



if __name__ == "__main__":
    main()