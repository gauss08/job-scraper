import pandas as pd
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
