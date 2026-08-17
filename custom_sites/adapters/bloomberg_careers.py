import re

import requests

# Bloomberg's Avature-hosted careers search, server-rendered (confirmed via
# plain curl -- no JS execution needed). Query params are the site's own
# facet ids, reverse-engineered from the URL the user built with the UI
# filters: 1686 = Experience Level (55479 = Internships, 55478 = Early
# Careers), 2562 = Business Area (219293 = Data, 219290 = Engineering and
# CTO, 219313 = Technology Support). No server-side country/location facet
# maps cleanly to "United States" (the Location dropdown only offers
# individual cities, e.g. "Dayton, NJ, US" -- not a country-level filter),
# so this fetches broad across the above facets and filters to US
# client-side on the rendered location string.
SEARCH_URL = "https://bloomberg.avature.net/careers/SearchJobs/"
QUERY_PARAMS = {
    "1686": "[55479,55478]",       # Experience Level: Internships, Early Careers
    "1686_format": "2312",
    "2562": "[219293,219290,219313]",  # Business Area: Data, Engineering and CTO, Technology Support
    "2562_format": "6594",
    "listFilterMode": "1",
    "jobRecordsPerPage": "200",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

ARTICLE_RE = re.compile(r'<article class="article article--result"[^>]*>.*?</article>', re.DOTALL)
LINK_RE = re.compile(r'<a class="link" href="([^"]+)">\s*(.*?)\s*</a>', re.DOTALL)
LOCATION_RE = re.compile(r'class="list-item-location">([^<]*)<')

# Bloomberg's rendered location strings are "City, ST, US" for US postings
# vs. "City, Country" everywhere else -- the ", US" suffix is the site's own
# country marker, not a keyword guess.
US_SUFFIX_RE = re.compile(r",\s*US$")


def fetch(timeout=30):
    """Returns a list of normalized entries from Bloomberg's Avature careers
    search, filtered (per the Experience Level / Business Area facets above)
    to US-located Internship/Early-Careers postings in Data, Engineering &
    CTO, and Technology Support."""
    resp = requests.get(SEARCH_URL, params=QUERY_PARAMS, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    entries = []
    for block in ARTICLE_RE.findall(html):
        link_m = LINK_RE.search(block)
        if not link_m:
            continue
        url, title = link_m.group(1), link_m.group(2).strip()

        loc_m = LOCATION_RE.search(block)
        location = loc_m.group(1).strip() if loc_m else ""
        if not US_SUFFIX_RE.search(location):
            continue

        job_id = url.rstrip("/").rsplit("/", 1)[-1]
        if not job_id.isdigit() or not title or not url:
            continue

        entries.append({
            "id": f"bloomberg_{job_id}",
            "company": "Bloomberg",
            "title": title,
            "url": url,
            "locations": [location] if location else [],
            "source": "Bloomberg",
        })

    return entries
