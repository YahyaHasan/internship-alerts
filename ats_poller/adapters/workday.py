import requests

JOBS_URL = "https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
JOB_BASE_URL = "https://{tenant}.{wd_host}.myworkdayjobs.com/{site}"

PAGE_SIZE = 20
MAX_PAGES = 60  # safety cap: 1200 postings per company per run


def fetch(company_name, tenant, wd_host, site, timeout=30):
    """Returns a list of normalized entries for one Workday-hosted board.

    Workday's search endpoint is paginated and has no stable per-job numeric
    id in the list response, so we key on externalPath (e.g.
    ".../Some-Title_JR2021695"), which is stable per requisition.
    """
    entries = []
    url = JOBS_URL.format(tenant=tenant, wd_host=wd_host, site=site)
    base = JOB_BASE_URL.format(tenant=tenant, wd_host=wd_host, site=site)

    offset = 0
    for _ in range(MAX_PAGES):
        resp = requests.post(
            url,
            json={"limit": PAGE_SIZE, "offset": offset, "searchText": "intern"},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break

        for job in postings:
            path = job.get("externalPath", "")
            entries.append({
                "id": f"wd_{tenant}_{path}",
                "company": company_name,
                "title": job.get("title", ""),
                "url": base + path,
                "locations": [job.get("locationsText", "")] if job.get("locationsText") else [],
                "source": f"Workday:{company_name}",
            })

        offset += PAGE_SIZE
        # Workday's searchText relevance ranking degrades after the first
        # couple pages (irrelevant results start appearing, and `total`
        # becomes unreliable), so we stop once a page's postings no longer
        # mention "intern" rather than trusting `total` for the cutoff.
        if not any("intern" in (job.get("title") or "").lower() for job in postings):
            break

    return entries
