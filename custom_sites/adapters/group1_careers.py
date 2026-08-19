import html
import re

import requests

# 1 Automotive (Group 1 Automotive) runs its careers site on AutoFusion, which
# server-renders the results table -- no JS execution needed, plain requests
# works. The site's own keyword=intern query param does a substring match
# (matches "Internet Salesperson" too), so this narrows further with a
# word-boundary regex on the title, matching the user's explicit request for
# an intern-keyword-only filter (no location/category filter requested).
RESULTS_URL = "https://www.group1careers.com/results.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

ROW_RE = re.compile(
    r'af-job-id">([^<]+)</td>\s*'
    r'<td class="af-job-title"><a href="([^"]+)">([^<]+)</a></td>\s*'
    r'<td class="af-job-location">([^<]*)</td>',
)

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def fetch(timeout=30):
    """Returns a list of normalized entries from 1 Automotive's careers
    site, filtered to titles containing the word "intern" (word-boundary
    match -- the site's own keyword search also matches "Internet")."""
    entries = []
    resp = requests.get(
        RESULTS_URL,
        params={"keyword": "intern", "sort": "distance_asc"},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()

    for job_id, href, title, location in ROW_RE.findall(resp.text):
        title = html.unescape(title.strip())
        if not TITLE_INTERN_RE.search(title):
            continue
        entries.append({
            "id": f"g1auto_{job_id.strip()}",
            "company": "1 Automotive",
            "title": title,
            "url": f"https://www.group1careers.com{href}",
            "locations": [location.strip()] if location.strip() else [],
            "source": "1 Automotive",
        })

    return entries
