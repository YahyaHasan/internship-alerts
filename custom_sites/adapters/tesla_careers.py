import re

import requests

STATE_URL = "https://www.tesla.com/cua-api/apps/careers/state"

# Tesla's own "Intern/Apprentice" job-type facet (value "3" in the state
# dump's type lookup) -- not a title-keyword guess.
INTERN_TYPE = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

# The state dump's location strings are bare "City, Region" (e.g. "Palo
# Alto, California", "Toronto, Ontario", "Laval, Quebec") with no country
# field anywhere in the payload. A US-state allowlist is the only reliable
# way to scope to US postings.
US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
}

# Every listing title Tesla posts ends with an explicit "(Season Year)" or
# "(Season/Season Year)" tag, e.g. "(Winter/Spring 2027)", "(Fall 2026)".
# Rather than rely on the shared pipeline's generic stale-year filter, scope
# this adapter itself to only the three upcoming terms the user is actually
# recruiting-season-eligible for: Winter, Summer, and Fall 2027. A handful
# of non-recruiting titles (e.g. plain "Electrical Engineering Internship -
# Modeling Validation") carry no season/year tag at all -- those are kept
# rather than dropped, since absence of a year isn't evidence of staleness.
YEAR_RE = re.compile(r"\b(20\d{2})\b")
TARGET_YEAR = "2027"


def _term_ok(title):
    years = YEAR_RE.findall(title)
    if not years:
        return True
    return TARGET_YEAR in years


def _slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug


def _is_us_location(location):
    if not location:
        return False
    region = location.rsplit(",", 1)[-1].strip()
    return region in US_STATES


def fetch(timeout=30):
    """Returns a list of normalized entries from Tesla's careers site,
    filtered to US-located Intern/Apprentice postings for Winter/Summer/Fall
    2027 (titles with no season/year tag are kept)."""
    resp = requests.get(STATE_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    locations_lookup = data["lookup"]["locations"]
    listings = data["listings"]

    entries = []
    for job in listings:
        if job.get("y") != INTERN_TYPE:
            continue

        title = job.get("t")
        job_id = job.get("id")
        if not title or not job_id:
            continue

        location = locations_lookup.get(str(job.get("l")))
        if not _is_us_location(location):
            continue

        if not _term_ok(title):
            continue

        slug = _slugify(title)
        url = f"https://www.tesla.com/careers/search/job/{slug}-{job_id}"

        entries.append({
            "id": f"tesla_{job_id}",
            "company": "Tesla",
            "title": title,
            "url": url,
            "locations": [location] if location else [],
            "source": "Tesla",
        })

    return entries
