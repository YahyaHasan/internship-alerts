import re

import requests

# AT&T's careers site (www.att.jobs) runs on TalentBrew. The search page is
# server-rendered and its filter checkboxes (Category / Country) POST/GET to
# a stateless AJAX endpoint returning an HTML fragment -- no auth or session
# cookie required (confirmed via a plain curl with no cookies attached, after
# capturing the exact request shape by hooking fetch/XHR in the browser while
# clicking the "Technology" and "United States" facet checkboxes on
# https://www.att.jobs/search-jobs/intern/117/1).
#
# Facet ids (from the page's filter checkboxes, data-id attrs):
#   Category "Technology" = 36864 (FacetType 1)
#   Country "United States" = 6252001 (FacetType 2)
# These are AT&T-specific taxonomy ids from this TalentBrew instance --
# they are not standard/portable to another company's TalentBrew site.
RESULTS_URL = "https://www.att.jobs/search-jobs/results"
JOB_BASE_URL = "https://www.att.jobs"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

CATEGORY_TECHNOLOGY_ID = "36864"
COUNTRY_US_ID = "6252001"

RECORDS_PER_PAGE = 12
MAX_PAGES = 20  # safety cap: 240 postings per run

JOB_ROW_RE = re.compile(
    r'<a href="(/job/[^"]+)" data-job-id="\d+">([^<]+)</a></h2>\s*'
    r'<span class="job-location">([^<]*)</span>',
)

# AT&T's own Keywords=intern search is relevance/fuzzy-based, not a literal
# substring match -- it returns clearly non-intern titles too (e.g. "Lead
# Cybersecurity - Insider Risk Engineer"). custom_poll.py deliberately
# doesn't apply a shared intern-title filter (each adapter owns its own
# intern-level filtering), so this narrows further with a word-boundary
# regex to actually implement the "intern keyword" filter as intended.
TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def _params(page):
    return {
        "ActiveFacetID": CATEGORY_TECHNOLOGY_ID,
        "CurrentPage": page,
        "RecordsPerPage": RECORDS_PER_PAGE,
        "TotalContentResults": "",
        "Distance": 50,
        "RadiusUnitType": 0,
        "Keywords": "intern",
        "Location": "",
        "ShowRadius": "False",
        "IsPagination": "True" if page > 1 else "False",
        "CustomFacetName": "",
        "FacetTerm": "",
        "FacetType": 0,
        "FacetFilters[0].ID": CATEGORY_TECHNOLOGY_ID,
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
        "OrganizationIds": 117,
        "PostalCode": "",
        "ResultsType": 0,
    }


def fetch(timeout=30):
    """Returns a list of normalized entries from AT&T's careers site,
    filtered server-side to Keyword=intern, Category=Technology,
    Country=United States (per the user's explicit filter choice)."""
    entries = []

    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(RESULTS_URL, params=_params(page), headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        results_html = data.get("results") or ""

        rows = JOB_ROW_RE.findall(results_html)
        if not rows:
            break

        for href, title, location in rows:
            title = title.strip()
            if not TITLE_INTERN_RE.search(title):
                continue
            job_id_match = re.search(r"/(\d+)$", href)
            job_id = job_id_match.group(1) if job_id_match else href
            entries.append({
                "id": f"att_{job_id}",
                "company": "AT&T",
                "title": title.strip(),
                "url": JOB_BASE_URL + href,
                "locations": [location.strip()] if location.strip() else [],
                "source": "AT&T",
            })

        total_pages_match = re.search(r'data-total-pages="(\d+)"', results_html)
        total_pages = int(total_pages_match.group(1)) if total_pages_match else 1
        if page >= total_pages:
            break

    return entries
