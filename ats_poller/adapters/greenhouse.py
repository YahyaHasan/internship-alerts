import requests

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"


def fetch(company_name, slug, timeout=30):
    """Returns a list of normalized entries for one Greenhouse-hosted board."""
    entries = []
    url = BOARD_URL.format(slug=slug)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    for job in data.get("jobs", []):
        location = (job.get("location") or {}).get("name", "")
        entries.append({
            "id": f"gh_{slug}_{job['id']}",
            "company": company_name,
            "title": job.get("title", ""),
            "url": job.get("absolute_url", ""),
            "locations": [location] if location else [],
            "source": f"Greenhouse:{company_name}",
        })
    return entries
