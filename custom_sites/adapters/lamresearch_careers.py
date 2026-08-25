import requests

# Lam Research's careers site (careers.lamresearch.com) is Eightfold-hosted,
# same platform as Applied Materials/Qualcomm above -- real search endpoint
# on lamresearch.eightfold.ai, no auth needed.
#
# Matches the user's example URL's facets exactly: filter_paygrade with
# value "intern/apprentice" (confirmed: 8 global results, all genuine
# Intern-titled postings, no false positives) and filter_rmk_country with
# value "united states". Combined they return exactly 1 posting matching the
# user's own example URL's job id (1099554542790), confirming both facet
# names/values are correct. No keyword search or client-side regex needed.
API_URL = "https://lamresearch.eightfold.ai/api/pcsx/search"
JOB_BASE_URL = "https://careers.lamresearch.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

PAGE_SIZE = 20
MAX_PAGES = 10  # safety cap: 200 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Lam Research's Eightfold
    careers search, filtered server-side to Paygrade=intern/apprentice,
    Country=United States."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.get(
            API_URL,
            params={
                "domain": "lamresearch.com",
                "start": start,
                "num": PAGE_SIZE,
                "filter_paygrade": "intern/apprentice",
                "filter_rmk_country": "united states",
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
                "id": f"lam_{job_id}",
                "company": "Lam Research",
                "title": title,
                "url": url,
                "locations": pos.get("locations") or [],
                "source": "Lam Research",
            })

        if start + PAGE_SIZE >= total:
            break

    return entries
