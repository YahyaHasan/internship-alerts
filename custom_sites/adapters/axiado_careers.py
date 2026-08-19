import re

import requests

# Axiado's careers site runs on SmartRecruiters; this hits the platform's
# public postings API directly (same one every SmartRecruiters-hosted
# company exposes at api.smartrecruiters.com/v1/companies/{id}/postings --
# no auth needed), same pattern as sandisk_careers.py. The API's own `q`
# search param does full-text matching (not title-only -- confirmed results
# like "Head of Legal" and "Vice President, Finance" turning up for
# q=intern), so per the user's explicit "intern keyword" filter request this
# fetches all postings and narrows client-side with a word-boundary regex on
# the title. No location filter requested by the user for this company.
POSTINGS_URL = "https://api.smartrecruiters.com/v1/companies/Axiado/postings"

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

PAGE_SIZE = 100
MAX_PAGES = 10  # safety cap: 1000 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Axiado's SmartRecruiters
    postings, filtered to titles containing the word "intern" (word-boundary
    match, not a substring guess)."""
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        resp = requests.get(
            POSTINGS_URL,
            params={"limit": PAGE_SIZE, "offset": offset},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("content", [])
        total = data.get("totalFound", 0)

        if not postings:
            break

        for job in postings:
            title = job.get("name")
            job_id = job.get("id")
            if not title or not job_id:
                continue

            if not TITLE_INTERN_RE.search(title):
                continue

            location = job.get("location") or {}

            entries.append({
                "id": f"axiado_{job_id}",
                "company": "Axiado",
                "title": title,
                "url": f"https://jobs.smartrecruiters.com/Axiado/{job_id}",
                "locations": [location["fullLocation"]] if location.get("fullLocation") else [],
                "source": "Axiado",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
