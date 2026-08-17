#!/usr/bin/env python3
"""
ATS poller: polls Greenhouse/Lever/Workday-hosted career boards for a fixed
list of companies (see companies.py) and alerts on new internship postings
via Telegram.

Deliberately separate from the top-level poll.py (Simplify aggregator) --
different data sources, different state files, different filtering needs
(these boards list every open role, not just internships, so a title-based
internship + term-year filter runs before anything else).
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
from adapters import ashby, greenhouse, lever, workday  # noqa: E402
from companies import ASHBY_COMPANIES, GREENHOUSE_COMPANIES, LEVER_COMPANIES, WORKDAY_COMPANIES  # noqa: E402

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

BASE_DIR = Path(__file__).parent
SEEN_FILE = BASE_DIR / "seen_ats.json"
SKIPPED_LOG_FILE = BASE_DIR / "skipped_log_ats.json"

# These boards list every open role at the company, not just internships, so
# this filter (required) runs before anything else to cut the volume down.
INTERN_TITLE_RE = re.compile(r"\bintern(ship)?\b", re.IGNORECASE)

# Explicit stale-year postings (leftover listings from a prior cycle) get
# dropped; anything with no year mentioned, or a 2027/2028 mention, passes.
STALE_YEAR_RE = re.compile(r"\b(2023|2024|2025|2026)\b")
CURRENT_YEAR_RE = re.compile(r"\b(2027|2028)\b")

def log(msg):
    print(msg, flush=True)


def term_filter_ok(title):
    if STALE_YEAR_RE.search(title) and not CURRENT_YEAR_RE.search(title):
        return False
    return True


def fetch_all():
    entries = []
    for name, slug in GREENHOUSE_COMPANIES:
        try:
            got = greenhouse.fetch(name, slug)
            log(f"[Greenhouse:{name}] fetched {len(got)} jobs")
            entries.extend(got)
        except Exception as e:
            log(f"[Greenhouse:{name}] fetch failed: {e}")

    for name, slug in LEVER_COMPANIES:
        try:
            got = lever.fetch(name, slug)
            log(f"[Lever:{name}] fetched {len(got)} jobs")
            entries.extend(got)
        except Exception as e:
            log(f"[Lever:{name}] fetch failed: {e}")

    for name, tenant, wd_host, site in WORKDAY_COMPANIES:
        try:
            got = workday.fetch(name, tenant, wd_host, site)
            log(f"[Workday:{name}] fetched {len(got)} jobs")
            entries.extend(got)
        except Exception as e:
            log(f"[Workday:{name}] fetch failed: {e}")

    for name, slug in ASHBY_COMPANIES:
        try:
            got = ashby.fetch(name, slug)
            log(f"[Ashby:{name}] fetched {len(got)} jobs")
            entries.extend(got)
        except Exception as e:
            log(f"[Ashby:{name}] fetch failed: {e}")

    return entries


def keyword_filter(entries):
    kept = []
    for e in entries:
        title = e["title"]
        if not INTERN_TITLE_RE.search(title):
            continue
        if not term_filter_ok(title):
            continue
        kept.append(e)
    log(f"[KeywordFilter] {len(entries)} -> {len(kept)} after intern/term filter")
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
    log(f"[Fetch] total {len(all_entries)} raw entries across all companies")

    new_entries_raw = [e for e in all_entries if e["id"] not in seen_ids]
    log(f"[Fetch] {len(new_entries_raw)} entries not in seen_ats.json")

    intern_titled = [e for e in new_entries_raw if INTERN_TITLE_RE.search(e["title"])]
    log(f"[InternFilter] {len(new_entries_raw)} -> {len(intern_titled)} mention intern/internship")
    prefiltered = keyword_filter(intern_titled)

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
