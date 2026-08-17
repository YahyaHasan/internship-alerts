import re

import requests

# Sandisk's careers site runs on SmartRecruiters; this hits the platform's
# public postings API directly (same one every SmartRecruiters-hosted
# company exposes at api.smartrecruiters.com/v1/companies/{id}/postings --
# no auth needed).
POSTINGS_URL = "https://api.smartrecruiters.com/v1/companies/Sandisk/postings"

# No employment-type/experience-level facet in the API response reliably
# tags postings as "Intern" (typeOfEmployment is just Full-time/Part-time;
# experienceLevel doesn't have an Internship value either), so per the
# user's own filter choice this is a keyword search on the title, scoped
# with a word-boundary regex to avoid false positives on words containing
# "intern" as a substring. The user only wants roles based in Milpitas, CA
# (Sandisk's HQ) -- filtered on the API's own location.city field.
TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)
TARGET_CITY = "milpitas"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

PAGE_SIZE = 100
MAX_PAGES = 10  # safety cap: 1000 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Sandisk's SmartRecruiters
    postings, filtered to Milpitas, CA and titles containing the word
    "intern" (word-boundary match, not a substring guess)."""
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

            city = (job.get("location", {}) or {}).get("city", "")
            if city.strip().lower() != TARGET_CITY:
                continue

            entries.append({
                "id": f"sandisk_{job_id}",
                "company": "Sandisk",
                "title": title,
                "url": f"https://jobs.smartrecruiters.com/Sandisk/{job_id}",
                "locations": [job["location"].get("fullLocation")] if job.get("location", {}).get("fullLocation") else [],
                "source": "Sandisk",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
