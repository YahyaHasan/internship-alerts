import html as html_mod
import re

import requests

# Charles Schwab's careers site (www.schwabjobs.com) runs on the Radancy
# (formerly TMP Worldwide) careers platform. Its search page fetches results
# from this endpoint, which returns JSON whose "results" field is an HTML
# fragment of the results list -- no dedicated JSON job API exists. Confirmed
# stateless: no cookies, session, or CSRF token needed.
RESULTS_URL = "https://www.schwabjobs.com/search-jobs/results"
BASE_URL = "https://www.schwabjobs.com"

# Schwab's own facets (read off the search page's filter checkboxes), not a
# title-keyword guess: Category "Internship" (facet type 1) and Country
# "United States" (facet type 2).
INTERNSHIP_CATEGORY_ID = "8230432"
US_COUNTRY_ID = "6252001"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

PAGE_SIZE = 100
MAX_PAGES = 5  # safety cap: 500 postings per query

# Schwab's Category facet is assigned per-posting by its recruiters, so an
# internship filed under e.g. "Engineering & Software Development" instead of
# "Internship" would be missed by the facet alone. A second keyword=intern
# pass catches those; its hits are then narrowed by this word-boundary title
# regex, since the keyword search also matches job *descriptions* (it returns
# non-intern roles like "Java Software Engineer" whose text mentions interns).
TITLE_RE = re.compile(r"\b(intern|internship|co-?op)\b", re.I)

JOB_RE = re.compile(
    r'<a href="(?P<url>[^"]+)" data-job-id="(?P<id>[^"]+)">'
    r'\s*<h2>(?P<title>.*?)</h2>'
    r'(?:\s*<span class="job-location">(?P<loc>.*?)</span>)?',
    re.S,
)


def _params(page, keywords="", facets=()):
    params = {
        "ActiveFacetID": facets[0][0] if facets else "0",
        "CurrentPage": page,
        "RecordsPerPage": PAGE_SIZE,
        "Distance": 50,
        "RadiusUnitType": 0,
        "Keywords": keywords,
        "Location": "",
        "ShowRadius": "False",
        "IsPagination": "False",
        "CustomFacetName": "",
        "FacetTerm": "",
        "FacetType": 0,
        "SearchResultsModuleName": "Search Results",
        "SearchFiltersModuleName": "Search Filters",
        "SortCriteria": 0,
        "SortDirection": 0,
        "SearchType": 5,
    }
    for i, (facet_id, facet_type, display) in enumerate(facets):
        params[f"FacetFilters[{i}].ID"] = facet_id
        params[f"FacetFilters[{i}].FacetType"] = facet_type
        params[f"FacetFilters[{i}].Display"] = display
        params[f"FacetFilters[{i}].IsApplied"] = "true"
    return params


def _search(keywords, facets, timeout):
    """Yields (job_id, title, url, locations) tuples for one facet/keyword query."""
    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(
            RESULTS_URL,
            params=_params(page, keywords, facets),
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or ""
        if not results:
            break

        for m in JOB_RE.finditer(results):
            title = html_mod.unescape(re.sub(r"<[^>]+>", "", m.group("title"))).strip()
            url = m.group("url")
            if not title or not url:
                continue
            if url.startswith("/"):
                url = BASE_URL + url
            raw_loc = m.group("loc") or ""
            # Multi-site postings list locations semicolon-separated.
            locations = [
                loc.strip()
                for loc in html_mod.unescape(raw_loc).split(";")
                if loc.strip()
            ]
            yield m.group("id"), title, url, locations

        total_pages = re.search(r'data-total-pages="(\d+)"', results)
        if not total_pages or page >= int(total_pages.group(1)):
            break


def fetch(timeout=30):
    """Returns a list of normalized entries from Charles Schwab's careers
    site, filtered to US-located internship postings."""
    entries = []
    seen_ids = set()

    queries = [
        # Category=Internship + Country=United States.
        ("", (
            (INTERNSHIP_CATEGORY_ID, 1, "Internship"),
            (US_COUNTRY_ID, 2, "United States"),
        ), False),
        # Safety net: keyword search, US only, narrowed by title regex.
        ("intern", ((US_COUNTRY_ID, 2, "United States"),), True),
    ]

    for keywords, facets, title_filter in queries:
        for job_id, title, url, locations in _search(keywords, facets, timeout):
            if job_id in seen_ids:
                continue
            if title_filter and not TITLE_RE.search(title):
                continue
            seen_ids.add(job_id)
            entries.append({
                "id": f"schwab_{job_id}",
                "company": "Charles Schwab",
                "title": title,
                "url": url,
                "locations": locations,
                "source": "Charles Schwab",
            })

    return entries
