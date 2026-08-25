import requests

# Applied Materials' careers site (careers.appliedmaterials.com) is
# Eightfold-hosted, same pattern as Qualcomm/PayPal/Netflix -- the site's own
# domain 403s ("Not authorized for PCSX") on the apply/v2 API, but the real
# search endpoint lives on appliedmaterials.eightfold.ai, no auth needed.
#
# Matches the user's example URL: a real "Seniority" facet
# (filter_seniority=Intern) filters accurately server-side -- confirmed 10
# global results, every title a genuine internship/early-career program, no
# false positives -- combined with Location=United States per the user's
# request. No keyword search or client-side regex needed.
API_URL = "https://appliedmaterials.eightfold.ai/api/pcsx/search"
JOB_BASE_URL = "https://careers.appliedmaterials.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

PAGE_SIZE = 20
MAX_PAGES = 10  # safety cap: 200 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Applied Materials'
    Eightfold careers search, filtered server-side to Seniority=Intern,
    Location=United States."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.get(
            API_URL,
            params={
                "domain": "appliedmaterials.com",
                "start": start,
                "num": PAGE_SIZE,
                "location": "United States",
                "filter_seniority": "Intern",
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

            url = f"{JOB_BASE_URL}{pos['positionUrl']}" if pos.get("positionUrl") else None
            if not url:
                continue

            entries.append({
                "id": f"amat_{job_id}",
                "company": "Applied Materials",
                "title": title,
                "url": url,
                "locations": pos.get("locations") or [],
                "source": "Applied Materials",
            })

        if start + PAGE_SIZE >= total:
            break

    return entries
