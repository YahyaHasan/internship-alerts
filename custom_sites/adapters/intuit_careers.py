import re

import requests

# Intuit's careers site (jobs.intuit.com) runs on TalentBrew, same platform
# as AT&T/Palo Alto Networks. Org id 27595. Matches the user's example URL's
# intent: the URL's "acm" param (advanced category multi-select) lists three
# custom Job Category facet ids for Intuit's student programs -- 9205024,
# 9205760, 9205744. Only 9205760 ("New College Grad") currently has any open
# postings (the other two -- presumably "Internship" and a PhD/Grad program
# -- have zero right now, so they don't even appear in the site's own facet
# list, same "Workday/Oracle only lists facet values with an open posting"
# situation noted elsewhere in this file for Cloudera/Broadcom). Confirmed
# via the site's own /search-jobs/results AJAX endpoint that passing all
# three facet ids as FacetFilters (FacetType=1, i.e. Category) reproduces
# the same result set the user's own URL's "acm" param targets. Combined
# with Country = United States (6252001, a GeoNames id, same as the other
# TalentBrew adapters).
RESULTS_URL = "https://jobs.intuit.com/search-jobs/results"
JOB_BASE_URL = "https://jobs.intuit.com"

ORG_ID = "27595"
STUDENT_PROGRAM_CATEGORY_IDS = ["9205024", "9205760", "9205744"]
COUNTRY_US_ID = "6252001"

RECORDS_PER_PAGE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run

JOB_ROW_RE = re.compile(
    r'<a href="(/job/[^"]+)" data-job-id="\d+"[^>]*>\s*'
    r'<h2>([^<]+)</h2>\s*'
    r'<span class="job-location">([^<]*)</span>',
)


def _params(page):
    params = {
        "CurrentPage": page,
        "RecordsPerPage": RECORDS_PER_PAGE,
        "TotalContentResults": "",
        "Distance": 50,
        "RadiusUnitType": 0,
        "Keywords": "",
        "Location": "",
        "ShowRadius": "False",
        "IsPagination": "True" if page > 1 else "False",
        "CustomFacetName": "",
        "FacetTerm": "",
        "FacetType": 0,
        "SearchResultsModuleName": "Search Results",
        "SearchFiltersModuleName": "Search Filters",
        "SortCriteria": 0,
        "SortDirection": 0,
        "SearchType": 1,
        "OrganizationIds": ORG_ID,
        "PostalCode": "",
        "ResultsType": 0,
    }
    idx = 0
    for cat_id in STUDENT_PROGRAM_CATEGORY_IDS:
        params[f"FacetFilters[{idx}].ID"] = cat_id
        params[f"FacetFilters[{idx}].FacetType"] = 1
        params[f"FacetFilters[{idx}].IsApplied"] = "true"
        idx += 1
    params[f"FacetFilters[{idx}].ID"] = COUNTRY_US_ID
    params[f"FacetFilters[{idx}].FacetType"] = 2
    params[f"FacetFilters[{idx}].IsApplied"] = "true"
    return params


def fetch(timeout=30):
    """Returns a list of normalized entries from Intuit's TalentBrew careers
    site, filtered server-side to Category in (student program facet ids)
    + Country = United States."""
    entries = []

    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(RESULTS_URL, params=_params(page), headers={
            "User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest",
        }, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        results_html = data.get("results") or ""

        rows = JOB_ROW_RE.findall(results_html)
        if not rows:
            break

        for href, title, location in rows:
            title = title.strip()
            job_id_match = re.search(r"/(\d+)$", href)
            job_id = job_id_match.group(1) if job_id_match else href
            entries.append({
                "id": f"intuit_{job_id}",
                "company": "Intuit",
                "title": title,
                "url": JOB_BASE_URL + href,
                "locations": [location.strip()] if location.strip() else [],
                "source": "Intuit",
            })

        total_pages_match = re.search(r'data-total-pages="(\d+)"', results_html)
        total_pages = int(total_pages_match.group(1)) if total_pages_match else 1
        if page >= total_pages:
            break

    return entries
