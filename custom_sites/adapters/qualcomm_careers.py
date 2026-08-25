import requests

# Qualcomm's careers site (careers.qualcomm.com) is Eightfold-hosted; the
# real search API lives on qualcomm.eightfold.ai (same split as PayPal --
# see paypal_careers.py), confirmed via curl (careers.qualcomm.com's own
# /api/apply/v2/jobs returned 403 "Not authorized for PCSX", but the
# eightfold.ai host's /api/pcsx/search works with no auth).
#
# Unlike PayPal/Netflix, this instance exposes a real "Seniority" facet
# (filter_seniority=Intern, matching the user's example URL) that filters
# accurately server-side -- confirmed: 53 global results, every title a
# genuine internship, no "Internal Auditor"-style false positives, so no
# keyword search or client-side title regex is needed at all here. Combined
# with location=United States per the user's request.
API_URL = "https://qualcomm.eightfold.ai/api/pcsx/search"
JOB_BASE_URL = "https://careers.qualcomm.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

PAGE_SIZE = 20
MAX_PAGES = 10  # safety cap: 200 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Qualcomm's Eightfold
    careers search, filtered server-side to Seniority=Intern,
    Location=United States."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.get(
            API_URL,
            params={
                "domain": "qualcomm.com",
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
                "id": f"qcom_{job_id}",
                "company": "Qualcomm",
                "title": title,
                "url": url,
                "locations": pos.get("locations") or [],
                "source": "Qualcomm",
            })

        if start + PAGE_SIZE >= total:
            break

    return entries
