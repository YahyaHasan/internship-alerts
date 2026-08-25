import re

import requests

# Honeywell runs on Oracle Fusion Recruiting Cloud (ORC), same platform as
# Oracle/Dell/TI above -- own host: ibqbjb.fa.ocs.oraclecloud.com (note the
# ".ocs." region, unlike the ".us2." hosts seen elsewhere), siteNumber
# "CX_1" (found embedded in careers.honeywell.com's server-rendered HTML).
# Client-side rendered, backed by the same public
# recruitingCEJobRequisitions REST API, no auth.
#
# The user's example URL used keyword="Intern (Bachelor's)". This ORC
# instance's keyword search is relevance-ranked, not a strict filter --
# the first page or two of results are genuinely Intern-titled, but by
# offset ~150+ relevance degrades and non-intern titles (e.g. "Sr Account
# Manager") flood in, same pagination-degradation behavior already noted
# for Workday's searchText in ats_poller/adapters/workday.py. So this
# stops paginating once a page's postings no longer mention "intern" in
# the title, rather than trusting TotalJobsCount. Filtered client-side to
# PrimaryLocationCountry == "US" (a real structured country-code field,
# same approach as Dell/TI), since no verified numeric "United States"
# locations-facet id was found for this site.
TITLE_INTERN_RE = re.compile(r"\bintern\b", re.IGNORECASE)
API_URL = "https://ibqbjb.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
JOB_BASE_URL = "https://careers.honeywell.com/en/sites/Honeywell/jobs/preview"

SITE_NUMBER = "CX_1"
KEYWORD = "Intern"

HEADERS = {"User-Agent": "Mozilla/5.0"}

PAGE_SIZE = 50
MAX_PAGES = 20  # safety cap: 1000 postings per run (Honeywell posts globally at high volume)


def fetch(timeout=30):
    """Returns a list of normalized entries from Honeywell's Fusion careers
    site, filtered server-side to keyword="Intern (Bachelor's)" (matching
    the user's own chosen search), and client-side to
    PrimaryLocationCountry == "US"."""
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        finder = (
            f"findReqs;siteNumber={SITE_NUMBER},"
            f"limit={PAGE_SIZE},offset={offset},"
            f"keyword={KEYWORD}"
        )
        resp = requests.get(
            API_URL,
            params={"onlyData": "true", "expand": "requisitionList", "finder": finder},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        item = data.get("items", [{}])[0]
        postings = item.get("requisitionList", [])
        total = item.get("TotalJobsCount", 0)

        if not postings:
            break

        if not any(TITLE_INTERN_RE.search(job.get("Title", "")) for job in postings):
            break

        for job in postings:
            title = job.get("Title", "")
            if not TITLE_INTERN_RE.search(title):
                continue
            if job.get("PrimaryLocationCountry") != "US":
                continue
            job_id = job.get("Id")
            if not job_id or not title:
                continue
            entries.append({
                "id": f"honeywell_{job_id}",
                "company": "Honeywell",
                "title": title,
                "url": f"{JOB_BASE_URL}/{job_id}/",
                "locations": [job["PrimaryLocation"]] if job.get("PrimaryLocation") else [],
                "source": "Honeywell",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
