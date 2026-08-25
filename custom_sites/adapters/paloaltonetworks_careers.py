import re

import requests

# Palo Alto Networks' careers site (jobs.paloaltonetworks.com) runs on
# TalentBrew, same platform as AT&T (custom_sites/adapters/att_careers.py)
# -- same stateless GET AJAX endpoint shape, no auth/cookies needed
# (confirmed via plain curl). Org id 47263 (from the site's own URLs).
#
# Unlike AT&T, PAN's TalentBrew instance exposes a real Category facet value
# literally named "Intern" (id 9246672, FacetType 1) -- confirmed via the
# response's own filter section, not a keyword guess -- so this filters
# server-side on that facet rather than falling back to a fuzzy keyword
# search. Country "United States" = 6252001 (FacetType 2) is a GeoNames id,
# the same value AT&T's TalentBrew instance uses, since TalentBrew's country
# facet ids are GeoNames ids and thus portable across tenants (confirmed:
# this instance's Canada/Singapore facet ids, 6251999 and 1880251, also
# match their real GeoNames ids).
RESULTS_URL = "https://jobs.paloaltonetworks.com/en/search-jobs/results"
JOB_BASE_URL = "https://jobs.paloaltonetworks.com"

ORG_ID = "47263"
CATEGORY_INTERN_ID = "9246672"
COUNTRY_US_ID = "6252001"

RECORDS_PER_PAGE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run

JOB_ROW_RE = re.compile(
    r'<a href="(/en/job/[^"]+)" data-job-id="\d+">\s*'
    r'<h2>([^<]+)</h2>\s*'
    r'<span class="job-location">([^<]*)</span>',
)


def _params(page):
    return {
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
        "FacetFilters[0].ID": CATEGORY_INTERN_ID,
        "FacetFilters[0].FacetType": 1,
        "FacetFilters[0].IsApplied": "true",
        "FacetFilters[1].ID": COUNTRY_US_ID,
        "FacetFilters[1].FacetType": 2,
        "FacetFilters[1].IsApplied": "true",
        "SearchResultsModuleName": "Search Results",
        "SearchFiltersModuleName": "Search Filters",
        "SortCriteria": 0,
        "SortDirection": 0,
        "SearchType": 1,
        "OrganizationIds": ORG_ID,
        "PostalCode": "",
        "ResultsType": 0,
    }


def fetch(timeout=30):
    """Returns a list of normalized entries from Palo Alto Networks'
    TalentBrew careers site, filtered server-side to Category=Intern,
    Country=United States."""
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
                "id": f"pan_{job_id}",
                "company": "Palo Alto Networks",
                "title": title,
                "url": JOB_BASE_URL + href,
                "locations": [location.strip()] if location.strip() else [],
                "source": "Palo Alto Networks",
            })

        total_pages_match = re.search(r'data-total-pages="(\d+)"', results_html)
        total_pages = int(total_pages_match.group(1)) if total_pages_match else 1
        if page >= total_pages:
            break

    return entries
