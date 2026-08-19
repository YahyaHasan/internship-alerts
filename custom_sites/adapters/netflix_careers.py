import re

import requests

# Netflix's careers site (jobs.netflix.com) is Eightfold-hosted, same
# platform as PayPal (see paypal_careers.py). The real search API lives on
# explore.jobs.netflix.net -- confirmed by curling jobs.netflix.com/ and
# finding it references explore.jobs.netflix.net/api/apply/v2/jobs, a
# stateless GET endpoint, no auth needed. Filtered server-side to the user's
# requested Region facet (ucan) and Teams facet (Engineering, Engineering
# Operations) -- both discovered via the response's own "facets" block,
# matching the user's explicit filter choices. Like Eightfold's PayPal
# instance, query=intern does substring matching ("Internal Communications"
# matches), so this narrows further with a word-boundary regex on the title.
API_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

TEAMS = ["Engineering", "Engineering Operations"]
PAGE_SIZE = 10
MAX_PAGES = 20

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def fetch(timeout=30):
    """Returns a list of normalized entries from Netflix's Eightfold-hosted
    careers search, filtered to Region=ucan + Teams in (Engineering,
    Engineering Operations) + title containing the word "intern"."""
    entries = []
    seen_ids = set()
    start = 0

    for _ in range(MAX_PAGES):
        params = [
            ("domain", "netflix.com"),
            ("start", start),
            ("num", PAGE_SIZE),
            ("query", "intern"),
            ("region", "ucan"),
        ]
        for team in TEAMS:
            params.append(("department", team))

        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        positions = data.get("positions") or []
        if not positions:
            break

        for p in positions:
            job_id = p.get("id")
            title = (p.get("name") or "").strip()
            url = p.get("canonicalPositionUrl")
            if not job_id or not title or not url:
                continue
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            if not TITLE_INTERN_RE.search(title):
                continue
            entries.append({
                "id": f"netflix_{job_id}",
                "company": "Netflix",
                "title": title,
                "url": url,
                "locations": p.get("locations") or ([p["location"]] if p.get("location") else []),
                "source": "Netflix",
            })

        start += PAGE_SIZE
        if start >= (data.get("count") or 0):
            break

    return entries
