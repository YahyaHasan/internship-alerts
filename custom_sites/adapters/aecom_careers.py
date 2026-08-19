import re

import requests

# AECOM's careers site (aecom.jobs) is a Nuxt app whose search page calls a
# shared multi-tenant search API at prod-search-api.jobsyn.org (the Jobsyn
# job-board network, used by many corporate careers sites). Confirmed via
# the browser's own network requests: the real search call is a plain GET,
# but requires a custom "x-origin: aecom.jobs" header (not a standard CORS
# Origin header -- both plain curl with no Origin and curl with a normal
# `Origin: https://aecom.jobs` header were rejected as "Mismatched origin";
# the actual required header name/value was only found by pulling AECOM's
# page's embedded `window.__NUXT__.config` blob, which has
# `"x-origin":"aecom.jobs"` in its public config). Once that header is set,
# no other auth/session is needed. Filtered server-side to the user's chosen
# location ("usa") and careerarea ("digital-engineering-technology") facets,
# matching the user's example URL exactly.
API_URL = "https://prod-search-api.jobsyn.org/api/v1/solr/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "x-origin": "aecom.jobs",
}

PAGE_SIZE = 10
MAX_PAGES = 10  # safety cap: 100 postings per run

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def _job_url(job):
    city = (job.get("city_exact") or "").strip()
    state = (job.get("state_short_exact") or "").strip()
    guid = job.get("guid")
    slug = job.get("title_slug")
    if not (city and state and guid and slug):
        return None
    city_state_slug = re.sub(r"[^a-z0-9]+", "-", f"{city}-{state}".lower()).strip("-")
    return f"https://aecom.jobs/{city_state_slug}/{slug}/{guid}/job/"


def fetch(timeout=30):
    """Returns a list of normalized entries from AECOM's Jobsyn-hosted
    careers search, filtered to location=usa + careerarea=digital &
    engineering technology + title containing the word "intern"."""
    entries = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        params = {
            "q": "intern",
            "page": page,
            "location": "usa",
            "careerarea": "digital-engineering-technology",
            "num_items": PAGE_SIZE,
        }
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        jobs = data.get("jobs") or []
        if not jobs:
            break

        for job in jobs:
            job_id = job.get("guid")
            title = job.get("title_exact")
            url = _job_url(job)
            if not job_id or not title or not url or job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            if not TITLE_INTERN_RE.search(title):
                continue

            locations = [job["location_exact"]] if job.get("location_exact") else []

            entries.append({
                "id": f"aecom_{job_id}",
                "company": "AECOM",
                "title": title,
                "url": url,
                "locations": locations,
                "source": "AECOM",
            })

        pagination = data.get("pagination") or {}
        if not pagination.get("has_more_pages"):
            break

    return entries
