import requests

# Oracle runs on Oracle Fusion Recruiting Cloud (ORC), same platform as ABM
# Industries (eiqg.fa.us2.oraclecloud.com) but a different site instance:
# eeho.fa.us2.oraclecloud.com, siteNumber CX_45001 (host/site id found via
# the "eeho.fa.us2.oraclecloud.com" and "CX_45001" strings embedded in
# careers.oracle.com's server-rendered HTML). Client-side rendered page,
# backed by the same public recruitingCEJobRequisitions REST API, no auth.
#
# The user's own example URL used selectedCategoriesFacet +
# selectedPostingDatesFacet=7, but that category id turned out to be
# "Technology Operations" (a data-center-ops job family, not internships --
# verified by inspecting the live UI, results were all Director/Manager
# titles) and keyword=Intern doesn't filter server-side at all on this ORC
# instance (confirmed: identical TotalJobsCount with and without it).
# Oracle's real internship signal is a separate "Job Type" facet with value
# "Student/Intern" (flex field AttributeChar4), confirmed via the UI to
# return exactly Intern/Fellowship-titled postings (verified count matched
# the API response exactly). Filtered server-side to that facet plus
# Location = United States (300000000149325).
API_URL = "https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
JOB_BASE_URL = "https://careers.oracle.com/en/sites/jobsearch/jobs/preview"

SITE_NUMBER = "CX_45001"
LOCATIONS_FACET_USA = "300000000149325"
STUDENT_INTERN_FACET = '"AttributeChar4|Student/Intern"'

HEADERS = {"User-Agent": "Mozilla/5.0"}

PAGE_SIZE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Oracle's Fusion careers
    site, filtered server-side to Job Type = Student/Intern and
    Location = United States."""
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        finder = (
            f"findReqs;siteNumber={SITE_NUMBER},"
            f"limit={PAGE_SIZE},offset={offset},"
            f"selectedFlexFieldsFacets={STUDENT_INTERN_FACET},"
            f"selectedLocationsFacet={LOCATIONS_FACET_USA}"
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

        for job in postings:
            title = job.get("Title", "")
            job_id = job.get("Id")
            if not job_id or not title:
                continue
            entries.append({
                "id": f"oracle_{job_id}",
                "company": "Oracle",
                "title": title,
                "url": f"{JOB_BASE_URL}/{job_id}/",
                "locations": [job["PrimaryLocation"]] if job.get("PrimaryLocation") else [],
                "source": "Oracle",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
