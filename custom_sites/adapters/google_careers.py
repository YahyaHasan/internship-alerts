import json
import re

import requests

RESULTS_URL = "https://www.google.com/about/careers/applications/jobs/results"

# Google's own experience-level facet, not a title keyword: internship-tier
# roles at Google are titled things like "Student Researcher, PhD" or
# "Apprenticeship in ...", rarely containing the word "intern" itself, so a
# title-keyword filter (used for the ATS-hosted companies) would miss most
# of them. This param is what the site's own "Intern & Apprentice" filter
# checkbox sets.
TARGET_LEVEL = "INTERN_AND_APPRENTICE"

PAGE_SIZE = 20
MAX_PAGES = 15  # safety cap: 300 postings per run

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

# The page embeds its job data as a plain JS array literal via
# AF_initDataCallback({key: 'ds:1', ..., data: [...], sideChannel: {...}}),
# server-rendered into the initial HTML (confirmed via direct curl -- no JS
# execution/headless browser needed). It happens to be valid JSON as-is.
DATA_RE = re.compile(r"AF_initDataCallback\(\{key:\s*'ds:1'.*?data:(\[.*?\]), sideChannel:", re.DOTALL)


def _parse_jobs(html):
    m = DATA_RE.search(html)
    if not m:
        return None, 0
    data = json.loads(m.group(1))
    jobs = data[0] or []
    total = data[2] if len(data) > 2 else len(jobs)
    return jobs, total


def fetch(timeout=30):
    """Returns a list of normalized entries from Google's careers site,
    filtered to the Intern & Apprentice experience level."""
    entries = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(
            RESULTS_URL,
            params={"target_level": TARGET_LEVEL, "page": page},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        jobs, total = _parse_jobs(resp.text)
        if jobs is None:
            raise RuntimeError("could not find ds:1 data block in response (page layout may have changed)")
        if not jobs:
            break

        for job in jobs:
            job_id = job[0]
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            if not job[2]:
                # A handful of listings (e.g. "Open Engineering Career
                # Opportunities, CapitalG Portfolio Companies") are collection
                # pages with no direct apply link -- not real postings.
                continue
            company = job[7] if len(job) > 7 and job[7] else "Google"
            locations = [loc[0] for loc in (job[9] or [])] if len(job) > 9 else []
            entries.append({
                "id": f"google_{job_id}",
                "company": company,
                "title": job[1],
                "url": job[2],
                "locations": locations,
                "source": "GoogleCareers",
            })

        if page * PAGE_SIZE >= total:
            break

    return entries
