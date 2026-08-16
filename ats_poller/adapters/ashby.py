import requests

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

# Ashby blocks requests with no User-Agent header (403), unlike Greenhouse/Lever.
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(company_name, slug, timeout=30):
    """Returns a list of normalized entries for one Ashby-hosted job board."""
    entries = []
    url = BOARD_URL.format(slug=slug)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    for job in data.get("jobs", []):
        if not job.get("isListed", True):
            continue
        locations = [job["location"]] if job.get("location") else []
        locations += [
            loc["location"] for loc in (job.get("secondaryLocations") or []) if loc.get("location")
        ]
        entries.append({
            "id": f"ashby_{slug}_{job['id']}",
            "company": company_name,
            "title": job.get("title", ""),
            "url": job.get("jobUrl", ""),
            "locations": locations,
            "source": f"Ashby:{company_name}",
        })
    return entries
