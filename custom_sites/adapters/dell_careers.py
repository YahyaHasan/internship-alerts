import requests

# Dell runs Oracle Fusion Recruiting Cloud (ORC) directly on its own domain
# (enterpriseplatform.dell.com), unlike ABM/Oracle above which are hosted on
# an *.fa.oraclecloud.com subdomain -- same public recruitingCEJobRequisitions
# REST API either way, no auth, siteNumber CX_1001 (found embedded in the
# careers page HTML; coincidentally the same site number as ABM's instance,
# but that's just an ORC-tenant-local id, not globally unique).
#
# Dell's own "Interns" Job Function facet (SelectedTitlesFacet=INTERNS,
# confirmed via the site's own filter checkbox -- UI count matched the API
# response exactly) is the accurate signal, unlike keyword=intern which does
# fuzzy/relevance matching here too (confirmed: returns non-intern titles
# like "Legal Director, Regulatory and Trade Compliance"). There's no
# separate numeric "United States" locations-facet id verified live (the
# Interns facet currently returns zero US postings, all international, so
# there was nothing to confirm a US-specific facet id against) -- instead
# this filters client-side on the response's own PrimaryLocationCountry
# field (a real structured country-code field, e.g. "US"/"MX"/"SG"), which
# needs no unverified id guess and is robust regardless of posting volume.
API_URL = "https://enterpriseplatform.dell.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
JOB_BASE_URL = "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers/job"

SITE_NUMBER = "CX_1001"
TITLES_FACET_INTERNS = "INTERNS"

HEADERS = {"User-Agent": "Mozilla/5.0"}

PAGE_SIZE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Dell's Fusion careers site,
    filtered server-side to Job Function = Interns, and client-side to
    PrimaryLocationCountry == "US"."""
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        finder = (
            f"findReqs;siteNumber={SITE_NUMBER},"
            f"limit={PAGE_SIZE},offset={offset},"
            f"selectedTitlesFacet={TITLES_FACET_INTERNS}"
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
            if job.get("PrimaryLocationCountry") != "US":
                continue
            title = job.get("Title", "")
            job_id = job.get("Id")
            if not job_id or not title:
                continue
            entries.append({
                "id": f"dell_{job_id}",
                "company": "Dell Technologies",
                "title": title,
                "url": f"{JOB_BASE_URL}/{job_id}",
                "locations": [job["PrimaryLocation"]] if job.get("PrimaryLocation") else [],
                "source": "Dell Technologies",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
