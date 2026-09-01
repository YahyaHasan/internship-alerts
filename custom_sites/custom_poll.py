#!/usr/bin/env python3
"""
Standalone-career-site poller: for companies whose job board isn't on a
standard ATS (Greenhouse/Lever/Workday) and needs a bespoke adapter per site
-- e.g. Google, Amazon, Apple, and Microsoft, each of which runs its own
in-house careers site with its own data format.

Kept separate from ats_poller/ats_poll.py deliberately: these adapters do
their own site-specific filtering to the intern/apprentice level server-side
(each site's search facets differ, so there's no shared "title contains
intern" heuristic that works across all of them, unlike the ATS-hosted
boards which list every open role and need that filter client-side). Own
state files, own workflow, own seen-id space -- never touches ats_poller's
state.
"""
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from adapters import (  # noqa: E402
    abbott_careers,
    abm_careers,
    aecom_careers,
    amazon_jobs,
    appliedmaterials_careers,
    apple_careers,
    atlassian_careers,
    att_careers,
    axiado_careers,
    bloomberg_careers,
    bostonscientific_careers,
    cisco_careers,
    cloudera_careers,
    dell_careers,
    github_careers,
    google_careers,
    group1_careers,
    honeywell_careers,
    hp_careers,
    intuit_careers,
    lamresearch_careers,
    microsoft_careers,
    netflix_careers,
    oracle_careers,
    paloaltonetworks_careers,
    paypal_careers,
    qualcomm_careers,
    salesforce_careers,
    sandisk_careers,
    schwab_careers,
    ti_careers,
)

# tesla_careers is NOT wired in below: Tesla's cua-api is behind Akamai
# bot-protection that blocks plain `requests` at the CDN edge (confirmed
# with a full browser header set -- looks like a TLS-fingerprint gate, not
# a header check), so it works from a real browser but not from this
# poller's HTTP client. The adapter/schema logic is believed correct; it
# needs to be verified from an actual GitHub Actions run before wiring it
# into fetch_all() below -- Actions' network path/IP may not be blocked the
# same way. See custom_sites/PLAYBOOK.md "Companies eliminated" section.

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"

BASE_DIR = Path(__file__).parent
SEEN_FILE = BASE_DIR / "seen_custom.json"
SKIPPED_LOG_FILE = BASE_DIR / "skipped_log_custom.json"

# Explicit stale-year postings (leftover listings from a prior cycle) get
# dropped; anything with no year mentioned, or a 2027/2028 mention, passes.
STALE_YEAR_RE = re.compile(r"\b(2023|2024|2025|2026)\b")
CURRENT_YEAR_RE = re.compile(r"\b(2027|2028)\b")

# Titles clearly outside our interest area get dropped before the LLM step.
# Same list as ats_poller/ats_poll.py.
DENY_TITLE_RE = re.compile(
    r"\b(Sales|Marketing|Recruiting|Recruiter|Manufacturing|CAD|Mechanical|Electrical|Cyber|Mobile|"
    r"Quant|Analog|Trader|Trading|Robotics?|Supply Chain|Help Desk|Service Desk|Facilities|"
    r"Human Resources|Accounting|Actuarial|Legal|Purchasing|Executive Assistant|Real Estate|"
    r"SkillBridge|Avionics|Propulsion|Structures|Biologics|Chemical|Materials|"
    r"Hardware|Data Scien(ce|tist)s?)\b",
    re.IGNORECASE,
)

def log(msg):
    print(msg, flush=True)


def term_filter_ok(title):
    if STALE_YEAR_RE.search(title) and not CURRENT_YEAR_RE.search(title):
        return False
    return True


# We can't reliably enumerate every valid "US" location string (bare city
# names, "Remote", full state names, "Bay Area", etc. all vary by adapter),
# so an allowlist would silently drop legitimate US roles that don't happen
# to match. Instead, blocklist locations that are unambiguously non-US; any
# entry not matching this (including ones with no location data, or an
# unrecognized location) is kept. Same list as ats_poller/ats_poll.py.
NON_US_LOCATION_RE = re.compile(
    r"\b(Singapore|India|China|Taiwan|Japan|Korea|Malaysia|Vietnam|Philippines|Thailand|Indonesia|"
    r"Israel|United Kingdom|UK|England|Scotland|Ireland|Germany|France|Spain|Italy|Netherlands|"
    r"Poland|Switzerland|Sweden|Norway|Denmark|Finland|Belgium|Austria|Portugal|"
    r"Canada|Mexico|Brazil|Argentina|Chile|Colombia|"
    r"Australia|New Zealand|"
    r"Egypt|South Africa|Nigeria|Kenya|"
    r"Hong Kong|Costa Rica|Romania|Czech(ia)?|Hungary|Ukraine|Russia)\b",
    re.IGNORECASE,
)


def location_filter_ok(locations):
    """A posting's 'locations' field is a list (a role can span multiple
    offices). Reject only if EVERY listed location is unambiguously non-US --
    a multi-location posting that includes a US site should survive even if
    it also lists a foreign one. Applied uniformly across every adapter,
    including Bloomberg and PayPal (previously deliberately left unfiltered
    by country -- superseded by this change)."""
    if not locations:
        return True
    return any(not NON_US_LOCATION_RE.search(loc) for loc in locations)


def fetch_all():
    entries = []

    try:
        got = google_careers.fetch()
        log(f"[GoogleCareers] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[GoogleCareers] fetch failed: {e}")

    try:
        got = amazon_jobs.fetch()
        log(f"[Amazon] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Amazon] fetch failed: {e}")

    try:
        got = apple_careers.fetch()
        log(f"[Apple] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Apple] fetch failed: {e}")

    try:
        got = microsoft_careers.fetch()
        log(f"[Microsoft] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Microsoft] fetch failed: {e}")

    try:
        got = salesforce_careers.fetch()
        log(f"[Salesforce] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Salesforce] fetch failed: {e}")

    try:
        got = cisco_careers.fetch()
        log(f"[Cisco] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Cisco] fetch failed: {e}")

    try:
        got = bloomberg_careers.fetch()
        log(f"[Bloomberg] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Bloomberg] fetch failed: {e}")

    try:
        got = paypal_careers.fetch()
        log(f"[PayPal] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[PayPal] fetch failed: {e}")

    try:
        got = sandisk_careers.fetch()
        log(f"[Sandisk] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Sandisk] fetch failed: {e}")

    try:
        got = group1_careers.fetch()
        log(f"[1 Automotive] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[1 Automotive] fetch failed: {e}")

    try:
        got = cloudera_careers.fetch()
        log(f"[Cloudera] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Cloudera] fetch failed: {e}")

    try:
        got = att_careers.fetch()
        log(f"[AT&T] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[AT&T] fetch failed: {e}")

    try:
        got = netflix_careers.fetch()
        log(f"[Netflix] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Netflix] fetch failed: {e}")

    try:
        got = abbott_careers.fetch()
        log(f"[Abbott] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Abbott] fetch failed: {e}")

    try:
        got = abm_careers.fetch()
        log(f"[ABM Industries] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[ABM Industries] fetch failed: {e}")

    try:
        got = aecom_careers.fetch()
        log(f"[AECOM] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[AECOM] fetch failed: {e}")

    try:
        got = axiado_careers.fetch()
        log(f"[Axiado] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Axiado] fetch failed: {e}")

    try:
        got = oracle_careers.fetch()
        log(f"[Oracle] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Oracle] fetch failed: {e}")

    try:
        got = dell_careers.fetch()
        log(f"[Dell Technologies] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Dell Technologies] fetch failed: {e}")

    try:
        got = paloaltonetworks_careers.fetch()
        log(f"[Palo Alto Networks] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Palo Alto Networks] fetch failed: {e}")

    try:
        got = qualcomm_careers.fetch()
        log(f"[Qualcomm] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Qualcomm] fetch failed: {e}")

    try:
        got = ti_careers.fetch()
        log(f"[Texas Instruments] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Texas Instruments] fetch failed: {e}")

    try:
        got = appliedmaterials_careers.fetch()
        log(f"[Applied Materials] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Applied Materials] fetch failed: {e}")

    try:
        got = lamresearch_careers.fetch()
        log(f"[Lam Research] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Lam Research] fetch failed: {e}")

    try:
        got = intuit_careers.fetch()
        log(f"[Intuit] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Intuit] fetch failed: {e}")

    try:
        got = bostonscientific_careers.fetch()
        log(f"[Boston Scientific] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Boston Scientific] fetch failed: {e}")

    try:
        got = hp_careers.fetch()
        log(f"[HP] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[HP] fetch failed: {e}")

    try:
        got = honeywell_careers.fetch()
        log(f"[Honeywell] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Honeywell] fetch failed: {e}")

    try:
        got = github_careers.fetch()
        log(f"[GitHub] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[GitHub] fetch failed: {e}")

    try:
        got = atlassian_careers.fetch()
        log(f"[Atlassian] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Atlassian] fetch failed: {e}")

    try:
        got = schwab_careers.fetch()
        log(f"[Charles Schwab] fetched {len(got)} jobs")
        entries.extend(got)
    except Exception as e:
        log(f"[Charles Schwab] fetch failed: {e}")

    return entries


def keyword_filter(entries):
    kept = []
    for e in entries:
        title = e["title"]
        if DENY_TITLE_RE.search(title):
            continue
        if not term_filter_ok(title):
            continue
        if not location_filter_ok(e.get("locations")):
            continue
        kept.append(e)
    log(f"[KeywordFilter] {len(entries)} -> {len(kept)} after deny/term/location filter")
    return kept


def llm_filter(entries, groq_api_key):
    if not entries:
        return entries, [], {"failed": False}

    jobs_payload = [{"id": e["id"], "company": e["company"], "title": e["title"]} for e in entries]

    system_prompt = (
        "You are a job relevance classifier for a UC Berkeley EECS sophomore applying to "
        "internships. Reply ONLY with a JSON array, no other text, no markdown fences."
    )
    user_prompt = (
        "Classify each internship posting as keep or skip based on interest alignment. "
        "Only a title and company are available, no full description.\n\n"
        "KEEP if the role involves: agentic AI, NLP, LLMs, ML/AI applications, backend systems, "
        "computer architecture, quantum computing, full-stack engineering, embedded systems, "
        "compilers, distributed systems, robotics, general software engineering.\n\n"
        "SKIP ONLY if clearly: pure frontend/UI dev with no backend, pure CRM/Salesforce admin, "
        "pure digital marketing or ads tech, pure media streaming infrastructure with no ML, "
        "non-technical roles, or a non-engineering internship (sales, HR, finance, legal).\n\n"
        "When in doubt, KEEP.\n\n"
        f"Jobs: {json.dumps(jobs_payload)}\n\n"
        'Reply with: [{"id": "...", "keep": true/false}]'
    )

    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
        results = json.loads(content)
        keep_map = {r["id"]: bool(r.get("keep", True)) for r in results}

        kept = [e for e in entries if keep_map.get(e["id"], True)]
        skipped = [e for e in entries if not keep_map.get(e["id"], True)]
        log(f"[LLM] {len(kept)} kept / {len(skipped)} skipped")
        return kept, skipped, {"failed": False}
    except Exception as e:
        log(f"[LLM] filter failed: {e}")
        return entries, [], {"failed": True, "reason": str(e)}


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
    log(f"[Fetch] total {len(all_entries)} raw entries across all standalone sites")

    new_entries_raw = [e for e in all_entries if e["id"] not in seen_ids]
    log(f"[Fetch] {len(new_entries_raw)} entries not in seen_custom.json")

    prefiltered = keyword_filter(new_entries_raw)

    llm_skipped = []
    if not prefiltered:
        final_jobs = []
    elif groq_api_key:
        final_jobs, llm_skipped, llm_info = llm_filter(prefiltered, groq_api_key)
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
    # seen here -- unlike ats_poll.py, these adapters already do their own
    # intern-level filtering server-side, so there's no cheap client-side
    # re-check to defer to on a future run.
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

    # No run-summary message: stay silent on runs that find nothing to send.


if __name__ == "__main__":
    main()
