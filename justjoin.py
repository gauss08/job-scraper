import asyncio
import json
import random
import re
import argparse
from datetime import datetime
from urllib.parse import quote_plus

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)




WORK_MODE={
    1:'remote',
    2:'hybrid',
    3:'office'
}

WORK_TYPE={
    1:'full-time',
    2:'part-time',
    3:'practice-internship',
    4:'freelance'
}

EXPERIENCE={
    1:'junior',
    2:'mid',
    3:'senior',
    4:'c-level'
}

EMPLOYMENT_TYPE={
    1:'b2b',
    2:'permanent',
    3:'internship',
    4:'mandate-contract',
    5:'specific-task-contract'
}

SORT_BY={
    1:'published',
    2:'newest',
    3:'salary desc',
    4:'salary asc'
}

def build_linkedin_url(
        keywords: str,
        location: str,
        experience: list = None,
        work_type: list = None,
        work_mode: str = None,
        employment_type: list =None,
        salary : str = None,
        sort_by: str = None,
        radius : str = None
    ) -> str:

    params={}

    params['keyword']=keywords
    #params['location']=location
    location=location if location else 'all-locations'

    # Experience (multi-select — comma separated)
    if experience:
        ex=[EXPERIENCE[int(exp)] for exp in experience ]
        params['experience-level']=','.join(ex)

    # Work type (multi-select)
    if work_type:
        wt=[WORK_TYPE[int(w)] for w in work_type]
        params['working-hours']=','.join(wt)
    
    # Work mode (multi-select)
    if work_mode:
        wm=[WORK_MODE[int(w)] for w in work_mode]
        params['workplace']=','.join(wm)    
    
    # Employment type (multi-select)
    if employment_type:
        ey=[EMPLOYMENT_TYPE[int(e)] for e in employment_type]
        params['employment-type']=','.join(ey)
    
    # With Salary (bool)
    if salary:
        params['with-salary']=salary

    # Radius (km)
    if radius:
        params['radius']=str(radius)

    base='https://justjoin.it/job-offers/'

    parts=[]
    for k,v in params.items():
        print(k,v)
        parts.append(f"{k}={quote_plus(str(v))}")
    
    if sort_by:
        orderBy='ASC' if int(sort_by) == 4 else 'DESC'
        sortBy='salary' if int(sort_by) in [3,4] else SORT_BY[int(sort_by)]
        full_sort=f'orderBy={orderBy}&sortBy={sortBy}'

        return base+location+'?'+'&'.join(parts)+'&'+full_sort


    return base+location+'?'+'&'.join(parts)


if __name__=="__main__":

    keywords=input('keywords : ')
    location=input('location : ')

    for k,v in EXPERIENCE.items():
        print(f"{k} : {v}")
    experience=input('experience : ')

    for k,v in WORK_TYPE.items():
        print(f"{k} : {v}")
    work_type=input('work_type : ')

    for k,v in EMPLOYMENT_TYPE.items():
        print(f"{k} : {v}")
    employment_type=input('employment_type : ')

    for k,v in WORK_MODE.items():
        print(f"{k} : {v}")
    work_mode=input('work_mode : ')


    salary=input('salary : ')
    radius=input('radius : ')

    for k,v in SORT_BY.items():
        print(f"{k} : {v}")
    sort_by=input('sort_by : ')

    url=build_linkedin_url(
        keywords=keywords,
        location=location,
        experience=experience,
        work_type=work_type,
        employment_type=employment_type,
        work_mode=work_mode,
        salary=salary,
        radius=radius,
        sort_by=sort_by
    )
    print(url)