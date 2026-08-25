import re

import requests

# GitHub's careers site (www.github.careers) runs on iCIMS's newer "Jibe"
# platform (tenant "githubinc" -- careers-githubinc.icims.com). The search
# page is a client-rendered Angular app, but it calls a plain JSON GET
# endpoint on GitHub's own domain, no auth/session needed (confirmed via a
# stateless curl with no cookies).
#
# The user's example URL pointed at the "/early-in-profession" page, but
# that's just a routing path -- it applies no server-side filter of its
# own (confirmed: same totalCount with or without that path segment). The
# Career Level filter dropdown (Director / Individual Contributor / Senior
# Director / Senior Manager, at the time this was checked) has no "Intern"
# value at all right now -- GitHub has zero open internship postings
# currently, so there's no live posting to confirm a stable facet id
# against. Falls back to keyword=intern (server-side, narrows the result
# set) plus a client-side word-boundary regex on the title (the same
# "narrow further, don't trust keyword alone" pattern used elsewhere in
# this repo), and a client-side US country-code check.
API_URL = "https://www.github.careers/api/jobs"
JOB_BASE_URL = "https://careers-githubinc.icims.com/jobs"

HEADERS = {"User-Agent": "Mozilla/5.0"}

PAGE_SIZE = 10  # fixed by the API, not configurable
MAX_PAGES = 20  # safety cap: 200 postings per run

TITLE_INTERN_RE = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def fetch(timeout=30):
    """Returns a list of normalized entries from GitHub's careers site,
    filtered server-side to keyword=intern, and client-side to a
    word-boundary "intern" title match + US country code."""
    entries = []

    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(
            API_URL,
            params={
                "keywords": "intern",
                "page": page,
                "sortBy": "relevance",
                "descending": "false",
                "internal": "false",
            },
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])
        total = data.get("totalCount", 0)

        if not jobs:
            break

        for wrapper in jobs:
            job = wrapper.get("data", {})
            title = job.get("title", "")
            if not TITLE_INTERN_RE.search(title):
                continue
            if job.get("country_code") != "US":
                continue
            job_id = job.get("req_id") or job.get("slug")
            url = job.get("apply_url")
            if not job_id or not title or not url:
                continue
            entries.append({
                "id": f"github_{job_id}",
                "company": "GitHub",
                "title": title,
                "url": url,
                "locations": [job["location_name"]] if job.get("location_name") else [],
                "source": "GitHub",
            })

        if page * PAGE_SIZE >= total:
            break

    return entries
