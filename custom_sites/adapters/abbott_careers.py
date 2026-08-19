import re

import requests

# Abbott's careers site (jobs.abbott) runs on the Phenom People platform,
# same as Cisco -- this is the same /widgets POST endpoint Cisco's adapter
# uses, confirmed working directly with plain requests (no auth/session
# needed). Filtered server-side to the user's requested country facet
# ("United States", confirmed via the response's own aggregations block --
# matches the "country:united states" filter in the user's example URL).
# Phenom's own `keywords` search does full-text matching against the whole
# job description, not just the title (confirmed: with keywords="intern",
# results included titles like "Test Technician I" and "Senior Manager,
# Human Resources" with no "intern" substring anywhere in the title), so
# this narrows further client-side with a word-boundary regex on the title
# itself, matching the user's explicit "intern keyword" filter request.
WIDGETS_URL = "https://www.jobs.abbott/widgets"

US_COUNTRY = "United States"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Content-Type": "application/json",
}

PAGE_SIZE = 100
MAX_PAGES = 10  # safety cap: 1000 postings per run

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def _base_body(start):
    return {
        "sortBy": "",
        "subsearch": "",
        "from": start,
        "jobs": True,
        "counts": False,
        "all_fields": ["country", "category", "state", "city"],
        "pageName": "search-results",
        "size": PAGE_SIZE,
        "clearAll": False,
        "jdsource": "facets",
        "isSliderEnable": False,
        "pageId": "page1",
        "siteType": "external",
        "keywords": "intern",
        "global": True,
        "selected_fields": {"country": [US_COUNTRY]},
        "lang": "en",
        "deviceType": "desktop",
        "country": "us",
        "ddoKey": "refineSearch",
    }


def fetch(timeout=30):
    """Returns a list of normalized entries from Abbott's careers site,
    filtered to US-located postings with "intern" as a whole word in the
    title."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.post(
            WIDGETS_URL,
            json=_base_body(start),
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("refineSearch", {})
        jobs = result.get("data", {}).get("jobs", [])
        total_hits = result.get("totalHits", 0)

        if not jobs:
            break

        for job in jobs:
            job_id = job.get("reqId")
            title = job.get("title")
            url = job.get("applyUrl")
            if not job_id or not title or not url or job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            if not TITLE_INTERN_RE.search(title):
                continue

            locations = job.get("multi_location") or []

            entries.append({
                "id": f"abbott_{job_id}",
                "company": "Abbott",
                "title": title,
                "url": url,
                "locations": locations,
                "source": "Abbott",
            })

        if start + PAGE_SIZE >= total_hits:
            break

    return entries
