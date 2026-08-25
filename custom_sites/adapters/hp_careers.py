import requests

# HP's careers site (apply.hp.com) is Eightfold-hosted, but on a shared
# generic host (app.eightfold.ai) rather than a company-specific subdomain
# like Qualcomm/Applied Materials/Lam Research/Boston Scientific above --
# found via the site's own embedded links to app.eightfold.ai (the
# guessed "hp.eightfold.ai" / "hp-sandbox.eightfold.ai" hosts seen in
# earlier digging don't serve the real search API; this one does, no auth
# needed). Matches the user's example URL: a real `filter_seniority`
# facet, value "internship" (lowercase, unlike Qualcomm/Applied Materials'
# "Intern") -- confirmed accurate server-side, all 33 global results
# genuine internship postings, no false positives. Filtered to
# Seniority=internship + Location=United States.
API_URL = "https://app.eightfold.ai/api/pcsx/search"
JOB_BASE_URL = "https://apply.hp.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

PAGE_SIZE = 20
MAX_PAGES = 10  # safety cap: 200 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from HP's Eightfold careers
    search, filtered server-side to Seniority=internship,
    Location=United States."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.get(
            API_URL,
            params={
                "domain": "hp.com",
                "start": start,
                "num": PAGE_SIZE,
                "location": "United States",
                "filter_seniority": "internship",
                "sort_by": "timestamp",
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

            url = f"{JOB_BASE_URL}{pos['positionUrl']}" if pos.get("positionUrl") else None
            if not url:
                continue

            entries.append({
                "id": f"hp_{job_id}",
                "company": "HP",
                "title": title,
                "url": url,
                "locations": pos.get("locations") or [],
                "source": "HP",
            })

        if start + PAGE_SIZE >= total:
            break

    return entries
