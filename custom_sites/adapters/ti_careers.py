import requests

# Texas Instruments runs on Oracle Fusion Recruiting Cloud (ORC), same
# platform as ABM/Oracle/Dell above, own host: edbz.fa.us2.oraclecloud.com,
# siteNumber "CX" (found embedded in careers.ti.com's server-rendered HTML).
# Client-side rendered, backed by the standard public
# recruitingCEJobRequisitions REST API, no auth.
#
# The user's example URL pointed at a static "Engineering" landing page
# (pages/engineering), not an actual job search results page, so this
# instead uses the real search endpoint. TI exposes a genuine Experience
# Level flex facet, value "Interns" (AttributeChar8|Interns) -- confirmed
# via the live UI filter (count matched the API response closely: 48 in the
# UI vs 50 from the API without also applying the UI's default
# keyword=Intern, likely just relevance-search narrowing a couple of
# borderline titles out; every returned title here is a genuine
# internship/Praktikant/Werkstudent posting either way, no false
# positives). Filtered client-side to PrimaryLocationCountry == "US" (a
# real structured country-code field on each posting, same approach as
# dell_careers.py), since no separate numeric "United States"
# locations-facet id was found -- TI's Work Locations facet only exposes
# city-level values, not a country-level one.
API_URL = "https://edbz.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
JOB_BASE_URL = "https://careers.ti.com/en/sites/CX/jobs/preview"

SITE_NUMBER = "CX"
INTERNS_FACET = '"AttributeChar8|Interns"'

HEADERS = {"User-Agent": "Mozilla/5.0"}

PAGE_SIZE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run


def fetch(timeout=30):
    """Returns a list of normalized entries from Texas Instruments' Fusion
    careers site, filtered server-side to Experience Level = Interns, and
    client-side to PrimaryLocationCountry == "US"."""
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        finder = (
            f"findReqs;siteNumber={SITE_NUMBER},"
            f"limit={PAGE_SIZE},offset={offset},"
            f"selectedFlexFieldsFacets={INTERNS_FACET}"
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
                "id": f"ti_{job_id}",
                "company": "Texas Instruments",
                "title": title,
                "url": f"{JOB_BASE_URL}/{job_id}/",
                "locations": [job["PrimaryLocation"]] if job.get("PrimaryLocation") else [],
                "source": "Texas Instruments",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
