import json
import re

import requests

SEARCH_URL = "https://jobs.apple.com/en-us/search"

# jobs.apple.com's `search` param is a fuzzy full-text search over the job
# description too (not title-only -- e.g. it returns "Analog System
# Electrical Engineer" because the description happens to mention an
# internship program), so a title-only regex filter still runs here, same as
# the ATS-hosted boards.
INTERN_TITLE_RE = re.compile(r"\bintern(ship)?\b", re.IGNORECASE)

# The page is server-rendered: job data ships as a JSON blob assigned to
# window.__staticRouterHydrationData in the initial HTML (confirmed via
# direct curl -- no JS execution/headless browser needed).
HYDRATION_RE = re.compile(
    r"window\.__staticRouterHydrationData = JSON\.parse\(\"(.*?)\"\);", re.DOTALL
)

PAGE_SIZE = 20
# Results are sorted newest-first (sort=newest) and this poller runs every
# few minutes, so a newly posted role will always land on page 1-2 well
# before this cap is reached -- no need to walk all ~60+ pages of the
# fuzzy-matched "intern" search every run just to reach older postings we
# already have in seen_custom.json.
MAX_PAGES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

US_COUNTRY_NAMES = {"United States", "United States of America"}


def _parse_search_results(html_text):
    m = HYDRATION_RE.search(html_text)
    if not m:
        return None, 0
    # The blob is a JSON-encoded JSON string (JSON.parse("...")) -- decode twice.
    decoded = json.loads('"' + m.group(1) + '"')
    data = json.loads(decoded)
    search = data["loaderData"]["search"]
    return search["searchResults"], search["totalRecords"]


def fetch(timeout=30):
    """Returns normalized US-only internship entries from jobs.apple.com.

    Apple's search API returns one result row per posting-location pair (the
    same positionId repeated once per office it's open in), so rows are
    merged by positionId and their locations combined into one entry.
    """
    by_id = {}

    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(
            SEARCH_URL,
            params={
                "search": "intern",
                "location": "united-states-USA",
                "sort": "newest",
                "page": page,
            },
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        results, total = _parse_search_results(resp.text)
        if results is None:
            raise RuntimeError("could not find hydration data block in response (page layout may have changed)")
        if not results:
            break

        for job in results:
            title = job.get("postingTitle", "")
            if not INTERN_TITLE_RE.search(title):
                continue
            locations = job.get("locations") or []
            if not any(loc.get("countryName") in US_COUNTRY_NAMES for loc in locations):
                continue
            location_names = [loc.get("name") for loc in locations if loc.get("name")]
            position_id = job.get("positionId") or job.get("id")
            slug = job.get("transformedPostingTitle", "")
            entry_id = f"apple_{position_id}"

            existing = by_id.get(entry_id)
            if existing:
                for name in location_names:
                    if name not in existing["locations"]:
                        existing["locations"].append(name)
                continue

            by_id[entry_id] = {
                "id": entry_id,
                "company": "Apple",
                "title": title,
                "url": f"https://jobs.apple.com/en-us/details/{position_id}/{slug}",
                "locations": location_names,
                "source": "Apple",
            }

        if page * PAGE_SIZE >= total:
            break

    return list(by_id.values())
