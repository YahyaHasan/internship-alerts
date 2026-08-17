import requests

# IBM's careers search (www.ibm.com/careers/search) is a Next.js app that
# proxies to an internal Elasticsearch cluster via this endpoint -- same
# call the site's own facet widgets make (captured via a browser XHR hook
# while toggling a filter checkbox; the query is real Elasticsearch query
# DSL, not a simple params object -- confirmed working with plain requests,
# no auth/session needed).
SEARCH_URL = "https://www-api.ibm.com/search/api/v2"

# Facet field ids reverse-engineered from the captured request body, mapped
# to the user's exact filter selections (same three as the URL they built
# with the site's own UI): field_keyword_08 = Career Area (OR'd together),
# field_keyword_18 = Experience level, field_keyword_05 = Location/Country.
CAREER_AREAS = ["Infrastructure & Technology", "Data & Analytics", "Software Engineering"]
EXPERIENCE_LEVEL = "Internship"
COUNTRY = "United States"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Content-Type": "application/json",
}

PAGE_SIZE = 50
MAX_PAGES = 10  # safety cap: 500 postings per run


def _body(start):
    return {
        "appId": "careers",
        "scopes": ["careers2"],
        "query": {"bool": {"must": []}},
        "post_filter": {
            "bool": {
                "must": [
                    {"bool": {"should": [{"term": {"field_keyword_08": area}} for area in CAREER_AREAS]}},
                    {"term": {"field_keyword_18": EXPERIENCE_LEVEL}},
                    {"term": {"field_keyword_05": COUNTRY}},
                ]
            }
        },
        "size": PAGE_SIZE,
        "from": start,
        "sort": [{"_score": "desc"}, {"pageviews": "desc"}],
        "lang": "zz",
        "localeSelector": {},
        "sm": {"query": "", "lang": "zz"},
        "_source": [
            "_id", "title", "url", "description", "language", "entitled",
            "field_keyword_17", "field_keyword_08", "field_keyword_18",
            "field_keyword_19", "field_keyword_05",
        ],
    }


def fetch(timeout=30):
    """Returns a list of normalized entries from IBM's careers search,
    filtered to the user's chosen Career Areas (Software Engineering, Data &
    Analytics, Infrastructure & Technology), Internship experience level,
    and United States location -- same filters as the site's own UI."""
    entries = []
    seen_ids = set()

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = requests.post(SEARCH_URL, json=_body(start), headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)

        if not hits:
            break

        for hit in hits:
            src = hit.get("_source", {})
            job_id = hit.get("_id")
            title = src.get("title")
            url = src.get("url")
            if not job_id or not title or not url or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            location = src.get("field_keyword_19")

            entries.append({
                "id": f"ibm_{job_id}",
                "company": "IBM",
                "title": title,
                "url": url,
                "locations": [location] if location else [],
                "source": "IBM",
            })

        if start + PAGE_SIZE >= total:
            break

    return entries
