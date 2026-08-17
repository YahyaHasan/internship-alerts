import requests

# Cisco's careers site runs on the Phenom People platform; this is the same
# POST endpoint its search-results page's facet widgets call. Confirmed
# working with no auth/session/CSRF token needed (stateless, no cookies
# required) -- the X-CSRF-TOKEN header seen in the browser's own requests
# turned out to belong to a *different* widget on the page (a personalization
# widget that fails with tokenAvailable:false regardless); the actual job
# search call uses "ddoKey": "refineSearch" and needs no token at all.
WIDGETS_URL = "https://careers.cisco.com/widgets"

# Cisco's own Experience Level / Country facets (raasJobRequisitionType /
# country), not a title-keyword guess. Internship-tier postings are split
# across two distinct facet values that both need to be selected -- "Intern"
# and "Internships, Apprenticeships, and Co-Ops" -- confirmed by inspecting
# the facet aggregation counts returned alongside search results.
INTERN_FACET_VALUES = ["Intern", "Internships, Apprenticeships, and Co-Ops"]
US_COUNTRY = "United States of America"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Content-Type": "application/json",
}

PAGE_SIZE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run


def _base_body(start):
    return {
        "sortBy": "",
        "subsearch": "",
        "from": start,
        "jobs": True,
        "counts": True,
        "all_fields": ["category", "raasJobRequisitionType", "country", "state", "city", "type", "RemoteType"],
        "pageName": "search-results",
        "size": PAGE_SIZE,
        "clearAll": False,
        "jdsource": "facets",
        "isSliderEnable": False,
        "pageId": "page4",
        "siteType": "external",
        "keywords": "",
        "global": True,
        "selected_fields": {
            "raasJobRequisitionType": INTERN_FACET_VALUES,
            "country": [US_COUNTRY],
        },
        "lang": "en_global",
        "deviceType": "desktop",
        "country": "global",
        "refNum": "CISCISGLOBAL",
        "ddoKey": "refineSearch",
    }


def fetch(timeout=30):
    """Returns a list of normalized entries from Cisco's careers site,
    filtered to US-located Intern/Apprenticeship/Co-Op postings."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.post(
            WIDGETS_URL,
            json=_base_body(start),
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("refineSearch", {})
        jobs = result.get("data", {}).get("jobs", [])
        total_hits = result.get("totalHits", 0)

        if not jobs:
            break

        for job in jobs:
            job_id = job.get("jobId")
            title = job.get("title")
            url = job.get("applyUrl")
            if not job_id or not title or not url or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            location = job.get("location") or job.get("cityStateCountry")

            entries.append({
                "id": f"cisco_{job_id}",
                "company": "Cisco",
                "title": title,
                "url": url,
                "locations": [location] if location else [],
                "source": "Cisco",
            })

        if start + PAGE_SIZE >= total_hits:
            break

    return entries
