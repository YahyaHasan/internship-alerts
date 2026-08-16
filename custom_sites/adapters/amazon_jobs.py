import re

import requests

SEARCH_URL = "https://www.amazon.jobs/en/search.json"

# amazon.jobs's base_query is a fuzzy full-text search over title+description,
# not a title-only match (e.g. it also returns "Program Manager - Site
# Operations" because the description mentions an internship program) -- so a
# title-only regex filter still runs here, same as the ATS-hosted boards.
INTERN_TITLE_RE = re.compile(r"\bintern(ship)?\b", re.IGNORECASE)

PAGE_SIZE = 100
MAX_PAGES = 20  # safety cap: 2000 postings per run


def fetch(timeout=30):
    """Returns normalized US-only internship entries from amazon.jobs.

    country=USA is a real server-side filter (confirmed via direct request),
    so non-US postings are never fetched in the first place -- the
    country_code check below is just defense-in-depth in case a posting is
    tagged with multiple countries.
    """
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        resp = requests.get(
            SEARCH_URL,
            params={
                "base_query": "intern",
                "country": "USA",
                "result_limit": PAGE_SIZE,
                "offset": offset,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])
        if not jobs:
            break

        for job in jobs:
            title = job.get("title", "")
            if not INTERN_TITLE_RE.search(title):
                continue
            if job.get("country_code") != "USA":
                continue
            city = job.get("city") or ""
            state = job.get("state") or ""
            location = ", ".join(p for p in (city, state) if p)
            entries.append({
                "id": f"amzn_{job.get('id_icims') or job.get('id')}",
                "company": "Amazon",
                "title": title,
                "url": "https://www.amazon.jobs" + job.get("job_path", ""),
                "locations": [location] if location else [],
                "source": "Amazon",
            })

        offset += PAGE_SIZE
        if offset >= data.get("hits", 0):
            break

    return entries
