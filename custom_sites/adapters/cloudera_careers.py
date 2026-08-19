import re

import requests

# Cloudera is Workday-hosted (cloudera.wd5.myworkdayjobs.com/External_Career),
# but per the user's explicit request this needs Business_Area and Country
# facets applied server-side, which ats_poller's generic WORKDAY_COMPANIES /
# adapters/workday.py doesn't support (searchText only) -- so this lives here
# as a bespoke adapter instead, same as the note in PLAYBOOK.md about
# Broadcom needing per-company facet handling.
#
# Facet ids captured directly from a POST to the jobs endpoint with an empty
# appliedFacets (see the facets[] array in that response) -- these are
# Cloudera-specific instance ids, not portable to another Workday tenant:
#   Business_Area "Engineering-Team"              = f853f08c27131022d118a7c835002927
#   Business_Area "Info Systems/Technology-Team"   = f853f08c27131022d14c52ed753f292b
#   locationCountry "United States of America"     = bc33aa3152ec42d4995f4791a106ed09
#
# NOTE: the user also asked for "Engineering Operations Team", but that value
# does not currently appear in the Business_Area facet list at all -- Workday
# only surfaces facet values that have at least one open posting right now
# (same limitation flagged for Broadcom in PLAYBOOK.md), so there's no id to
# capture for it yet. Flagged to the user; not included below.
JOBS_URL = "https://cloudera.wd5.myworkdayjobs.com/wday/cxs/cloudera/External_Career/jobs"
JOB_BASE_URL = "https://cloudera.wd5.myworkdayjobs.com/External_Career"

BUSINESS_AREA_ENGINEERING = "f853f08c27131022d118a7c835002927"
BUSINESS_AREA_INFO_SYSTEMS_TECH = "f853f08c27131022d14c52ed753f292b"
COUNTRY_USA = "bc33aa3152ec42d4995f4791a106ed09"

APPLIED_FACETS = {
    "Business_Area": [BUSINESS_AREA_ENGINEERING, BUSINESS_AREA_INFO_SYSTEMS_TECH],
    "locationCountry": [COUNTRY_USA],
}

PAGE_SIZE = 20
MAX_PAGES = 20  # safety cap: 400 postings per run

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def fetch(timeout=30):
    """Returns a list of normalized entries from Cloudera's Workday board,
    filtered server-side to Business Area (Engineering-Team, Info
    Systems/Technology-Team) and Country (United States), and client-side to
    titles containing the word "intern" (Workday's searchText is
    relevance-based, not a literal substring match, so this narrows further
    the same way ats_poller's Workday adapter does)."""
    entries = []
    offset = 0

    for _ in range(MAX_PAGES):
        resp = requests.post(
            JOBS_URL,
            json={
                "appliedFacets": APPLIED_FACETS,
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": "intern",
            },
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break

        for job in postings:
            title = job.get("title", "")
            if not TITLE_INTERN_RE.search(title):
                continue
            path = job.get("externalPath", "")
            entries.append({
                "id": f"cloudera_{path}",
                "company": "Cloudera",
                "title": title,
                "url": JOB_BASE_URL + path,
                "locations": [job.get("locationsText", "")] if job.get("locationsText") else [],
                "source": "Cloudera",
            })

        offset += PAGE_SIZE
        if offset >= data.get("total", 0):
            break

    return entries
