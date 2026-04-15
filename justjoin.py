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

SKILL_LEVELS={
    'Nice To Have':1,
    'Junior':2,
    'Regular':3,
    'Advanced':4,
    'Master':5
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

async def _dismiss_modal(page):
    """Close any sign-in / cookie modal."""

    MODAL_DISMISS_SELECTORS = [
    # Generic dismiss buttons
    '.cookiescript_pre_header'
    ]

    for sel in MODAL_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first

            if await btn.is_visible(timeout=1000):
                await btn.click()
                await page.wait_for_timeout(300)
                return
        except Exception:
            pass

        try:
            #await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass

async def _read_details(page) -> dict:
    info={}
    skills={}

    info["link"]=await page.locator("link[rel='canonical']").get_attribute('href')
    info["job_name"]=await page.locator('h1.mui-1w3djua').inner_text()
    info["company"]=await page.locator('h2.MuiBox-root').inner_text()
    info["location"]=await page.locator('div.MuiBox-root.mui-1lgfpg4').first.inner_text()
    info["work_type"]=await page.locator('div.MuiStack-root.mui-9ffzmz').first.inner_text()
    info["employment_type"]=await page.locator('div.MuiStack-root.mui-9ffzmz').nth(1).inner_text()
    info["experience"]=await page.locator('div.MuiStack-root.mui-9ffzmz').nth(2).inner_text()
    info["work_mode"]=await page.locator('div.MuiStack-root.mui-9ffzmz').nth(3).inner_text()

    salary_finder=page.locator('div.MuiTypography-root.mui-1f21jp8')
    if await salary_finder.count()>0:
        info["salary"]=await page.locator('div.MuiTypography-root.mui-1f21jp8').inner_text()
    else:
        info["salary"]=None

    #info['description']=await page.locator('div.MuiStack-root.mui-qd57u1').inner_text()

    tech_stack=await page.locator('div.MuiStack-root.mui-j7qwjs').inner_text()
    sp=tech_stack.split('\n')

    for i in range(1,len(sp),2):
        skills[f'{sp[i]}']=sp[i+1]
    

    expires=await page.locator('div.MuiStack-root.mui-1uqbqus').inner_text()
    days_left=expires[:expires.find('(')-1]
    last_date=re.findall(r"\((.*?)\)", expires)[0]

    info['days_left']=days_left
    info['last_date']=last_date
    print(info)



async def scrape_jobs(url : str, max_results: int = 100, headless: bool = False, fetch_descriptions: bool = True,) -> list:
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
            await page.goto(url,wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3500)
            await _dismiss_modal(page)

            
            # Scroll to load cards
            print(" 🔃 Loading results...")
            for i in range(18): # should be changed    while True 
                await page.wait_for_timeout(2500)
                await _dismiss_modal(page)
                cards=await page.locator("ul.MuiStack-root li").all()
                link=await cards[i].locator("a").first.get_attribute("href")
                full_link='https://justjoin.it'+link
                await page.goto(full_link)
                await _dismiss_modal(page)
                info=await _read_details(page)
                #print(info)
                await page.wait_for_timeout(3500)
                await page.go_back(wait_until="domcontentloaded", timeout=5000)
                print(f"{i+1} : {full_link}")

            
            

            print(f" ❇️  Found {len(cards)} raw cards, extracting up to {max_results}...")
        
        except Exception as e:
            print(e)



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
    asyncio.run(scrape_jobs(url,500))
