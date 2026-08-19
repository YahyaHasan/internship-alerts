import re

import requests

# ABM Industries runs on Oracle Fusion Recruiting Cloud (ORC). The search
# page (eiqg.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/.../CX_1001)
# is client-side rendered, but it's backed by Oracle's standard public
# recruitingCEJobRequisitions REST API -- same endpoint shape used by every
# ORC-hosted career site, no auth needed.
#
# Facet ids/values are ABM-specific to this Oracle site instance (siteNumber
# CX_1001), not portable to another company's ORC site:
#   Job Category (AttributeChar8) "Engineering", "Information Technology"
#   Location facet "United States" = 300000000289738
API_URL = "https://eiqg.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
JOB_BASE_URL = "https://eiqg.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job"

SITE_NUMBER = "CX_1001"
LOCATIONS_FACET_USA = "300000000289738"
FLEX_FIELDS_FACET = '"AttributeChar8|Engineering;Information Technology"'

HEADERS = {"User-Agent": "Mozilla/5.0"}

PAGE_SIZE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run

# Oracle's own search here has no employment-type/internship facet exposed,
# so per the same pattern as the other custom adapters, this narrows further
# with a word-boundary regex on the title.
TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def fetch(timeout=30):
    """Returns a list of normalized entries from ABM Industries' Oracle
    Fusion careers site, filtered server-side to Job Category (Engineering,
    Information Technology) and Location (United States), and client-side to
    titles containing the word "intern"."""
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        finder = (
            f"findReqs;siteNumber={SITE_NUMBER},"
            f"limit={PAGE_SIZE},offset={offset},"
            f"selectedFlexFieldsFacets={FLEX_FIELDS_FACET},"
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
            if not TITLE_INTERN_RE.search(title):
                continue
            job_id = job.get("Id")
            if not job_id:
                continue
            entries.append({
                "id": f"abm_{job_id}",
                "company": "ABM Industries",
                "title": title,
                "url": f"{JOB_BASE_URL}/{job_id}",
                "locations": [job["PrimaryLocation"]] if job.get("PrimaryLocation") else [],
                "source": "ABM Industries",
            })

        offset += PAGE_SIZE
        if offset >= total:
            break

    return entries
