#!/usr/bin/env python3
"""
ATS poller: polls Greenhouse/Lever/Workday-hosted career boards for a fixed
list of companies (see companies.py) and alerts on new internship postings
via Telegram.

Deliberately separate from custom_sites/custom_poll.py -- different data
sources, different state files, different filtering needs (these boards
list every open role, not just internships, so a title-based internship +
term-year filter runs before anything else).

IMPORTANT: adding a company to companies.py? Run
`python3 ats_poller/backfill_seen.py` first -- see companies.py's docstring
and backfill_seen.py's docstring for why.
"""
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from adapters import ashby, greenhouse, lever, smartrecruiters, workable, workday  # noqa: E402
from companies import (  # noqa: E402
    ASHBY_COMPANIES,
    GREENHOUSE_COMPANIES,
    LEVER_COMPANIES,
    SMARTRECRUITERS_COMPANIES,
    WORKABLE_COMPANIES,
    WORKDAY_COMPANIES,
)
from shared_filters import (  # noqa: E402
    ALLOW_TITLE_RE,
    DEGREE_GATE_RE,
    DENY_TITLE_RE,
    llm_filter,
    location_filter_ok,
    term_filter_ok,
)

BASE_DIR = Path(__file__).parent
SEEN_FILE = BASE_DIR / "seen_ats.json"
SKIPPED_LOG_FILE = BASE_DIR / "skipped_log_ats.json"

# These boards list every open role at the company, not just internships, so
# this filter (required) runs before anything else to cut the volume down.
INTERN_TITLE_RE = re.compile(r"\bintern(ship)?s?\b", re.IGNORECASE)

# Deny/allow keyword lists and stale-year/location filters live in
# shared_filters.py -- kept in sync with custom_sites/custom_poll.py there.


def log(msg):
    print(msg, flush=True)


FETCH_MAX_WORKERS = 20


def fetch_all():
    tasks = []
    for name, slug in GREENHOUSE_COMPANIES:
        tasks.append((f"Greenhouse:{name}", greenhouse.fetch, (name, slug)))
    for name, slug in LEVER_COMPANIES:
        tasks.append((f"Lever:{name}", lever.fetch, (name, slug)))
    for name, tenant, wd_host, site in WORKDAY_COMPANIES:
        tasks.append((f"Workday:{name}", workday.fetch, (name, tenant, wd_host, site)))
    for name, slug in ASHBY_COMPANIES:
        tasks.append((f"Ashby:{name}", ashby.fetch, (name, slug)))
    for name, slug in SMARTRECRUITERS_COMPANIES:
        tasks.append((f"SmartRecruiters:{name}", smartrecruiters.fetch, (name, slug)))
    for name, account in WORKABLE_COMPANIES:
        tasks.append((f"Workable:{name}", workable.fetch, (name, account)))

    entries = []
    with ThreadPoolExecutor(max_workers=FETCH_MAX_WORKERS) as pool:
        future_to_label = {
            pool.submit(fetch_fn, *args): label for label, fetch_fn, args in tasks
        }
        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                got = future.result()
                log(f"[{label}] fetched {len(got)} jobs")
                entries.extend(got)
            except Exception as e:
                log(f"[{label}] fetch failed: {e}")

    return entries


def keyword_filter(entries):
    kept = []
    for e in entries:
        title = e["title"]
        if not INTERN_TITLE_RE.search(title):
            continue
        if DEGREE_GATE_RE.search(title):
            continue
        if DENY_TITLE_RE.search(title) and not ALLOW_TITLE_RE.search(title):
            continue
        if not term_filter_ok(title):
            continue
        if not location_filter_ok(e.get("locations")):
            continue
        kept.append(e)
    log(f"[KeywordFilter] {len(entries)} -> {len(kept)} after intern/deny(+allow)/term/location filter")
    return kept


WORKDAY_COUNTRY_MAX_WORKERS = 20


def workday_country_filter(entries):
    """Backstops location_filter_ok() for Workday entries specifically:
    Workday's search-result 'locationsText' is often a bare city/region with
    no country name (e.g. "Waterford City", "Dunboyne", "2 Locations"), which
    NON_US_LOCATION_RE can't catch by pattern-matching alone. This does one
    detail-page lookup per surviving entry (workday.country_alpha2) to get
    Workday's structured country code instead. Only runs post-keyword_filter,
    so the extra requests are bounded to the small surviving set, not every
    posting fetched.
    """
    workday_entries = [e for e in entries if e.get("_wd_detail_url")]
    if not workday_entries:
        return entries

    reject_ids = set()
    with ThreadPoolExecutor(max_workers=WORKDAY_COUNTRY_MAX_WORKERS) as pool:
        future_to_entry = {
            pool.submit(workday.country_alpha2, e): e for e in workday_entries
        }
        for future in as_completed(future_to_entry):
            e = future_to_entry[future]
            try:
                code = future.result()
            except Exception:
                code = None  # fail open, same policy as the free-text filter
            if code and code != "US":
                reject_ids.add(e["id"])

    kept = [e for e in entries if e["id"] not in reject_ids]
    if reject_ids:
        log(f"[WorkdayCountryFilter] {len(entries)} -> {len(kept)} after country lookup")
    return kept


def format_locations(locations):
    if not locations:
        return None
    shown = locations[:3]
    text = " | ".join(shown)
    if len(locations) > 3:
        text += f" + {len(locations) - 3} more"
    return text


def build_job_message(e):
    company = html.escape(e["company"])
    title = html.escape(e["title"])
    source = html.escape(e["source"])
    url = html.escape(e["url"], quote=True)
    lines = [f"🆕 <b>{company}</b> — {title}"]
    loc_str = format_locations(e.get("locations") or [])
    if loc_str:
        lines.append(f"📍 {html.escape(loc_str)}")
    lines.append(f"🏷 {source}")
    lines.append(f'🔗 <a href="{url}">Apply</a>')
    return "\n".join(lines)


def send_telegram_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=30)
    if not resp.ok:
        log(f"[Telegram] error sending message: {resp.status_code} {resp.text}")
        return False
    return True


def load_seen():
    try:
        with open(SEEN_FILE, "r") as f:
            return set(str(x) for x in json.load(f))
    except Exception:
        return set()


def save_seen(seen_ids):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen_ids), f, indent=2)
        f.write("\n")


def load_skipped_log():
    try:
        with open(SKIPPED_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_skipped_log(records):
    with open(SKIPPED_LOG_FILE, "w") as f:
        json.dump(records, f, indent=2)
        f.write("\n")


def main():
    dry_run = "--dry-run" in sys.argv

    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    groq_api_key = os.environ.get("GROQ_API_KEY")

    if not dry_run:
        if not telegram_token or not telegram_chat_id:
            print("ERROR: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID environment variables are required", file=sys.stderr)
            sys.exit(1)
    else:
        log("[DryRun] --dry-run set: Telegram credentials not required, no messages will be sent")

    def notify(text):
        if dry_run:
            log(f"[DryRun] would send Telegram message:\n{text}")
            return True
        return send_telegram_message(telegram_token, telegram_chat_id, text)

    now = datetime.now(timezone.utc)

    seen_ids = load_seen()
    log(f"[Seen] loaded {len(seen_ids)} previously seen ids")

    all_entries = fetch_all()
    before = len(all_entries)
    all_entries = [e for e in all_entries if e.get("title") and e.get("url")]
    if len(all_entries) != before:
        log(f"[Fetch] dropped {before - len(all_entries)} entries missing title/url")
    log(f"[Fetch] total {len(all_entries)} raw entries across all companies")

    # Belt-and-braces against an adapter handing back the same posting twice
    # (a paginated board replaying a page, or two configured boards on the
    # same tenant sharing a requisition). Without this, one posting turns into
    # one Telegram message per copy.
    deduped = []
    seen_this_run = set()
    for e in all_entries:
        if e["id"] in seen_this_run:
            continue
        seen_this_run.add(e["id"])
        deduped.append(e)
    if len(deduped) != len(all_entries):
        log(f"[Fetch] dropped {len(all_entries) - len(deduped)} duplicate ids within this run")
    all_entries = deduped

    new_entries_raw = [e for e in all_entries if e["id"] not in seen_ids]
    log(f"[Fetch] {len(new_entries_raw)} entries not in seen_ats.json")

    intern_titled = [e for e in new_entries_raw if INTERN_TITLE_RE.search(e["title"])]
    log(f"[InternFilter] {len(new_entries_raw)} -> {len(intern_titled)} mention intern/internship")
    prefiltered = keyword_filter(intern_titled)
    prefiltered = workday_country_filter(prefiltered)

    llm_skipped = []
    if not prefiltered:
        final_jobs = []
    elif groq_api_key:
        final_jobs, llm_skipped, llm_info = llm_filter(prefiltered, groq_api_key, log=log)
        if llm_info["failed"]:
            log(f"[LLM] failed ({llm_info.get('reason')}), sending all {len(prefiltered)} unfiltered")
            final_jobs = prefiltered
    else:
        log("[LLM] GROQ_API_KEY not set, skipping LLM stage")
        final_jobs = prefiltered

    sent_count = 0
    for e in final_jobs:
        if notify(build_job_message(e)):
            sent_count += 1
    log(f"[Telegram] sent {sent_count} messages" + (" (dry run)" if dry_run else ""))

    # Every fetched id (not just ones that survived filtering) gets marked
    # seen here, same as custom_poll.py -- this trades away catching a role
    # that gets retitled to add "intern" after its first appearance (rare)
    # for not re-running the intern/term/exclude regex over the full
    # multi-thousand-entry raw fetch on every single run.
    new_seen_ids = set(seen_ids)
    for e in new_entries_raw:
        new_seen_ids.add(e["id"])
    save_seen(new_seen_ids)
    log(f"[Seen] wrote {len(new_seen_ids)} total seen ids")

    skipped_records = load_skipped_log()
    for e in llm_skipped:
        skipped_records.append({
            "company": e["company"],
            "title": e["title"],
            "source": e["source"],
            "timestamp": now.isoformat(),
        })
    save_skipped_log(skipped_records[-500:])  # keep log bounded

    # No run-summary message: the poller should stay silent on runs that find
    # nothing to send, and each found role already gets its own message above.


if __name__ == "__main__":
    main()
