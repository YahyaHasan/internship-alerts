import requests

# Static JSON feed backing careers.salesforce.com's job search widget --
# same data the site's employeeTypes/country filters query against
# client-side. jobs_1.json and jobs_2.json are duplicate exports of the same
# requisitions (verified: identical Job_Requisition_Ref_ID sets); jobs_1 is
# ~4x smaller so that's the one to fetch.
JOBS_URL = "https://a.sfdcstatic.com/digital/xsf/careers/prod/jobs_1.json"

# Salesforce's own Employee_Type/Countries fields, not a title-keyword
# guess -- matches the site's "Employee Type: Intern" / "Country" filters.
INTERN_TYPE = "Intern"
US_COUNTRY = "United States of America"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}


def fetch(timeout=30):
    """Returns a list of normalized entries from Salesforce's careers feed,
    filtered to US-located Intern-type postings."""
    resp = requests.get(JOBS_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    entries = []
    for job in data.get("Report_Entry", []):
        if job.get("Employee_Type") != INTERN_TYPE:
            continue
        if US_COUNTRY not in (job.get("Countries") or []):
            continue

        title = job.get("Job_Posting_Title")
        req_id = job.get("Job_Requisition_Ref_ID")
        url = job.get("Futureforce_-_Internships_site_URL") or job.get("External_Job_Posting_Site")
        if not title or not req_id or not url:
            continue

        location = job.get("Job_Requisition_Primary_Location")

        entries.append({
            "id": f"salesforce_{req_id}",
            "company": "Salesforce",
            "title": title,
            "url": url,
            "locations": [location] if location else [],
            "source": "Salesforce",
        })

    return entries
