import requests

BOARD_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


def fetch(company_name, slug, timeout=30):
    """Returns a list of normalized entries for one Lever-hosted board."""
    entries = []
    url = BOARD_URL.format(slug=slug)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    for job in data:
        categories = job.get("categories") or {}
        location = categories.get("location", "")
        entries.append({
            "id": f"lv_{slug}_{job['id']}",
            "company": company_name,
            "title": job.get("text", ""),
            "url": job.get("hostedUrl", ""),
            "locations": [location] if location else [],
            "source": f"Lever:{company_name}",
        })
    return entries
