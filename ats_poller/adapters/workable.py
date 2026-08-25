import requests

# Workable's public "widget" API returns every open posting for a company
# in one unpaginated response -- confirmed via Hugging Face's board
# (apply.workable.com/huggingface): the same 7 jobs come back regardless
# of query params, no auth needed.
BOARD_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}"

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(company_name, account, timeout=30):
    """Returns a list of normalized entries for one Workable-hosted job
    board."""
    entries = []
    url = BOARD_URL.format(account=account)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    for job in data.get("jobs", []):
        title = job.get("title", "")
        job_url = job.get("application_url") or job.get("url")
        shortcode = job.get("shortcode")
        if not title or not job_url or not shortcode:
            continue

        location = ", ".join(
            part for part in [job.get("city"), job.get("state"), job.get("country")] if part
        )

        entries.append({
            "id": f"workable_{account}_{shortcode}",
            "company": company_name,
            "title": title,
            "url": job_url,
            "locations": [location] if location else [],
            "source": f"Workable:{company_name}",
        })

    return entries
