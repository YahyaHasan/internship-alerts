import re

import requests

# Atlassian's careers site runs on iCIMS (tenant "globalcareers-atlassian",
# same platform as GitHub above), but its own domain exposes a plain JSON
# GET endpoint returning every open posting in one response -- no auth, no
# pagination needed (confirmed: 249 postings, one flat array).
#
# The user's example URL used team=Interns, but the API's own "category"
# field (the closest match to a "team" filter) has no "Interns" value at
# all right now -- Atlassian has zero open internship postings currently,
# so there's no live posting to confirm a stable category value against.
# Falls back to a client-side word-boundary "intern" title regex, and a
# client-side substring check for "United States" in the (free-text, not
# structured) locations field -- same pattern as other sites with no
# verified facet for the current zero-posting state.
LISTINGS_URL = "https://www.atlassian.com/endpoint/careers/listings"

HEADERS = {"User-Agent": "Mozilla/5.0"}

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def fetch(timeout=30):
    """Returns a list of normalized entries from Atlassian's careers site,
    filtered client-side to a word-boundary "intern" title match and a
    "United States" location substring match."""
    entries = []

    resp = requests.get(LISTINGS_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    jobs = resp.json()

    for job in jobs:
        title = job.get("title", "")
        if not TITLE_INTERN_RE.search(title):
            continue

        locations = job.get("locations") or []
        if not any("United States" in loc for loc in locations):
            continue

        job_id = job.get("id")
        url = (job.get("portalJobPost") or {}).get("portalUrl")
        if not job_id or not title or not url:
            continue

        entries.append({
            "id": f"atlassian_{job_id}",
            "company": "Atlassian",
            "title": title,
            "url": url,
            "locations": locations,
            "source": "Atlassian",
        })

    return entries
