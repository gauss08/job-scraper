# job-scraper

Playwright-based job scrapers for several platforms. The scripts build search
URLs, open Chromium, collect job cards/details, and save results as JSON.

## Supported Scrapers

| Platform | Script | Status |
| --- | --- | --- |
| ZipRecruiter | `ziprecruiter.py` | Interactive and CLI-driven scraper |
| JustJoin.it | `justjoin.py` | Interactive and CLI-driven scraper |
| LinkedIn | `linkedin.py` | CLI-driven scraper with advanced filters |
| Upwork | `upwork.py` | CLI-driven scraper |
| Indeed | `indeed.py` | Prototype/incomplete scraper |

## Setup

Create and activate a virtual environment, then install the browser dependency:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install playwright rich
playwright install chromium
```

Some scripts import Playwright at module import time, so even `--help` may fail
until Playwright is installed.

## ZipRecruiter

Run interactively:

```bash
python ziprecruiter.py
```

Run with CLI options:

```bash
python ziprecruiter.py \
  --keywords "python developer" \
  --location "United States" \
  --date-filter "Within 5 days" \
  --work-mode Remote \
  --experience Junior Middle \
  --employment-type "Full Time" \
  --max 25 \
  --output ziprecruiter_jobs.json
```

Useful options:

- `--keywords`, `--location`
- `--date-filter`
- `--apply-type`
- `--experience`
- `--work-mode`
- `--employment-type`
- `--salary-floor`, `--salary-ceil`
- `--radius`
- `--chrome-profile`
- `--max`, `--output`

## JustJoin.it

Run interactively:

```bash
python justjoin.py
```

Run with CLI options:

```bash
python justjoin.py \
  --keywords "python backend" \
  --location warsaw \
  --experience 1 2 \
  --work-mode 1 \
  --salary \
  --max-results 30 \
  --output justjoin_jobs.json
```

Filter codes:

- Experience: `1=junior`, `2=mid`, `3=senior`, `4=c-level`
- Work type: `1=full-time`, `2=part-time`, `3=practice-internship`, `4=freelance`
- Work mode: `1=remote`, `2=hybrid`, `3=office`
- Employment type: `1=b2b`, `2=permanent`, `3=internship`, `4=mandate-contract`, `5=specific-task-contract`
- Sort: `1=published`, `2=newest`, `3=salary desc`, `4=salary asc`

Note: the current non-interactive JustJoin path prints `args.output`, but the
file-writing code may still use a timestamped filename depending on the local
version of `justjoin.py`. Check the terminal output and generated files after a
run.

## LinkedIn

Example:

```bash
python linkedin.py \
  --keywords "software engineer" \
  --location "United States" \
  --date any \
  --experience mid senior \
  --job-type full-time \
  --work-type remote \
  --easy-apply \
  --sort recent \
  --max 25
```

Common options:

- `--keywords`, `--location`
- `--date` or `--seconds`
- `--experience`
- `--job-type`
- `--work-type`
- `--easy-apply`
- `--active`
- `--sort recent|relevant`
- `--distance`
- `--max`
- `--no-headless`
- `--no-descriptions`
- `--json-only`

## Upwork

The Upwork scraper has its own CLI parser and authentication-related options.
Check its help after installing dependencies:

```bash
python upwork.py --help
```

## Output

Scrapers save JSON records such as:

```json
[
  {
    "job_title": "Software Engineer",
    "company_name": "Example Company",
    "location": "Remote",
    "job_url": "https://..."
  }
]
```

Field names differ by platform. For example, JustJoin.it uses keys such as
`job_name`, `company`, `salary`, `skills`, and `last_date`.

## Notes

- These scrapers depend on live website markup. CSS selectors can break after
  a site redesign.
- Use reasonable limits and delays. Scraping too aggressively can trigger bot
  detection or rate limits.
- Some platforms may require an existing browser profile or manual login.
- Generated result JSON files are not currently ignored by `.gitignore`; add
  patterns such as `*_jobs*.json` if you do not want scrape outputs in Git.
