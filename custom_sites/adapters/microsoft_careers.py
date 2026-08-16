import re

import requests

# apply.careers.microsoft.com is Microsoft's Eightfold-hosted careers site;
# this is the JSON API its own search UI calls under the hood (confirmed via
# browser network inspection -- there is no server-rendered fallback here,
# unlike Google/Apple).
SEARCH_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
BASE_URL = "https://apply.careers.microsoft.com"

# query="intern" is a fuzzy full-text search over the full posting, not
# title-only, so a title-only regex filter still runs here, same as the
# ATS-hosted boards.
INTERN_TITLE_RE = re.compile(r"\bintern(ship)?\b", re.IGNORECASE)

PAGE_SIZE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}


def fetch(timeout=30):
    """Returns normalized US-only internship entries from Microsoft's careers site."""
    entries = []
    start = 0

    for _ in range(MAX_PAGES):
        resp = requests.get(
            SEARCH_URL,
            params={
                "domain": "microsoft.com",
                "query": "intern",
                "location": "united states",
                "start": start,
                "num": PAGE_SIZE,
                "sort_by": "relevance",
            },
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        positions = data.get("positions", [])
        if not positions:
            break

        for job in positions:
            title = job.get("name", "")
            if not INTERN_TITLE_RE.search(title):
                continue
            locations = job.get("locations") or []
            # Location strings are "Country, State, City" (e.g. "United
            # States, Washington, Redmond").
            if not any(loc.startswith("United States") for loc in locations):
                continue
            position_url = job.get("positionUrl", "")
            entries.append({
                "id": f"msft_{job.get('id')}",
                "company": "Microsoft",
                "title": title,
                "url": BASE_URL + position_url if position_url else BASE_URL,
                "locations": locations,
                "source": "Microsoft",
            })

        start += PAGE_SIZE
        if start >= data.get("count", 0):
            break

    return entries
