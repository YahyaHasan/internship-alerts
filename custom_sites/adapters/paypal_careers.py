import re

import requests

# PayPal's careers site runs on Eightfold; this is the same GET endpoint the
# site's own search box calls (confirmed via network capture -- stateless,
# no auth/session needed despite living under /api/pcsx/).
SEARCH_URL = "https://paypal.eightfold.ai/api/pcsx/search"

# No employment-type/experience-level facet is exposed by this endpoint for
# PayPal's instance (checked filterDef.facets on an unfiltered query -- only
# "locations" comes back), so a keyword search is the only option here, per
# the user's own filter choice. IMPORTANT caveat: Eightfold's query matching
# is prefix/substring-based, so query="intern" also matches "Internal" (e.g.
# "Manager, Internal Controls", "Sr Auditor, Internal Audit" are both
# returned) -- these are false positives, not real internship postings. A
# client-side word-boundary regex on the title is required to filter them
# back out; a plain substring check on the title would let them through.
QUERY = "intern"
TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

PAGE_SIZE = 20
MAX_PAGES = 10  # safety cap: 200 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from PayPal's Eightfold careers
    search, filtered to US-located postings whose title actually contains
    the word "intern" (not just a substring match on "Internal")."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.get(
            SEARCH_URL,
            params={
                "domain": "paypal.com",
                "query": QUERY,
                "start": start,
                "num": PAGE_SIZE,
                "sort_by": "relevance",
            },
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        positions = data.get("positions", [])
        total = data.get("count", 0)

        if not positions:
            break

        for pos in positions:
            job_id = pos.get("id")
            title = pos.get("name")
            if not job_id or not title or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            if not TITLE_INTERN_RE.search(title):
                continue

            standardized = pos.get("standardizedLocations") or []
            if not any(loc.endswith(",US") for loc in standardized):
                continue

            url = f"https://paypal.eightfold.ai{pos['positionUrl']}" if pos.get("positionUrl") else None
            if not url:
                continue

            locations = pos.get("locations") or []

            entries.append({
                "id": f"paypal_{job_id}",
                "company": "PayPal",
                "title": title,
                "url": url,
                "locations": locations,
                "source": "PayPal",
            })

        if start + PAGE_SIZE >= total:
            break

    return entries
