import requests

POSTINGS_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"

HEADERS = {"User-Agent": "Mozilla/5.0"}

PAGE_SIZE = 100
MAX_PAGES = 20  # safety cap: 2000 postings per run


def fetch(company_name, slug, timeout=30):
    """Returns a list of normalized entries for one SmartRecruiters-hosted
    company's public postings API (no auth needed)."""
    entries = []
    offset = 0
    url = POSTINGS_URL.format(slug=slug)

    for _ in range(MAX_PAGES):
        resp = requests.get(
            url,
            params={"limit": PAGE_SIZE, "offset": offset},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("content", [])
        total = data.get("totalFound", 0)

        if not postings:
            break

        for job in postings:
            title = job.get("name", "")
            job_id = job.get("id")
            if not job_id:
                continue
            location = (job.get("location") or {}).get("fullLocation")
            entries.append({
                "id": f"sr_{slug}_{job_id}",
                "company": company_name,
                "title": title,
                "url": f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
                "locations": [location] if location else [],
                "source": f"SmartRecruiters:{company_name}",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
