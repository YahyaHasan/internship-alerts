#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from rapidfuzz import fuzz

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
SPEEDYAPPLY_URLS = [
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/INTERN_INTL.md",
]
JOBRIGHT_URL = "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-Internship/master/README.md"

GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
SEEN_FILE = "seen.json"
SKIPPED_LOG_FILE = "skipped_log.json"
AUDIT_HOUR_UTC = 9
AUDIT_WINDOW_HOURS = 24

LINK_RE = re.compile(r"\[.*?\]\((https?://[^)]+)\)")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
HTML_A_RE = re.compile(r'<a\s+[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
HTML_TAG_RE = re.compile(r"<[^>]+>")
IMG_APPLY_RE = re.compile(r'alt="Apply"', re.IGNORECASE)
BOLD_RE = re.compile(r"\*\*")

EXCLUDE_PATTERNS = [
    re.compile(r"\bfrontend\b|\bfront-end\b", re.IGNORECASE),
    re.compile(r"\bUI\b.*\bengineer\b|\bUI/UX\b", re.IGNORECASE),
    re.compile(r"\bSalesforce\b|\bCRM\b", re.IGNORECASE),
    re.compile(r"\bdigital marketing\b|\bSEO\b|\bads?\b.*\btech\b", re.IGNORECASE),
    re.compile(r"\bstreaming\b.*\binfrastructure\b|\bmedia.*platform\b", re.IGNORECASE),
]
DATA_ENGINEER_RE = re.compile(r"\bdata.*engineer\b", re.IGNORECASE)
ML_EXCEPTION_RE = re.compile(r"\bML\b|machine learning|\bAI\b|pipeline for ML", re.IGNORECASE)

YEAR_RE = re.compile(r"20\d{2}")
SEASON_WORDS_RE = re.compile(r"\b(summer|fall|spring|winter|intern|internship)\b", re.IGNORECASE)
NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
WHITESPACE_RE = re.compile(r"\s+")


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=30):
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_simplify():
    entries = []
    try:
        text = fetch(SIMPLIFY_URL)
        data = json.loads(text)
        for item in data:
            if item.get("active") and item.get("is_visible"):
                entries.append({
                    "id": item.get("id"),
                    "company": item.get("company_name", ""),
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "locations": item.get("locations", []) or [],
                    "source": "Simplify",
                })
    except Exception as e:
        log(f"[Simplify] fetch failed: {e}")
    log(f"[Simplify] fetched {len(entries)} entries")
    return entries


def extract_links(cell):
    """Return list of (text, url) for both markdown and HTML anchor links in a cell."""
    links = []
    for m in MD_LINK_RE.finditer(cell):
        links.append((m.group(1), m.group(2)))
    for m in HTML_A_RE.finditer(cell):
        inner_text = HTML_TAG_RE.sub("", m.group(2)).strip()
        links.append((inner_text, m.group(1)))
    return links


def cell_plain_text(cell):
    """Strip markdown/HTML markup from a cell to get plain display text."""
    links = extract_links(cell)
    text = cell
    for m in MD_LINK_RE.finditer(cell):
        text = text.replace(m.group(0), m.group(1))
    text = HTML_TAG_RE.sub("", text)
    text = BOLD_RE.sub("", text)
    text = text.strip()
    if not text and links:
        text = links[0][0]
    return text


def parse_markdown_table(text, source_name):
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "---" in line or "----" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0].strip().lower() == "company":
            continue

        company = cell_plain_text(cells[0])
        title = cell_plain_text(cells[1])
        if not company or not title:
            continue

        # Prefer an apply link found within the title cell (jobright style)
        url = None
        title_links = extract_links(cells[1])
        if title_links:
            url = title_links[-1][1]
        else:
            # Look for an anchor wrapping an "Apply" image across the row
            apply_url = None
            for cell in cells:
                for m in HTML_A_RE.finditer(cell):
                    if IMG_APPLY_RE.search(m.group(2)):
                        apply_url = m.group(1)
                        break
                if apply_url:
                    break
            if apply_url:
                url = apply_url
            else:
                # Fallback: last link anywhere in the row (first is usually company site)
                row_links = extract_links(line)
                if row_links:
                    url = row_links[-1][1]

        if not url:
            continue

        uid = hashlib.sha256((company + title + url).encode("utf-8")).hexdigest()[:16]
        entries.append({
            "id": uid,
            "company": company,
            "title": title,
            "url": url,
            "locations": [],
            "source": source_name,
        })
    return entries


def fetch_speedyapply():
    entries = []
    for url in SPEEDYAPPLY_URLS:
        try:
            text = fetch(url)
            parsed = parse_markdown_table(text, "SpeedyApply")
            entries.extend(parsed)
        except Exception as e:
            log(f"[SpeedyApply] fetch failed for {url}: {e}")
    log(f"[SpeedyApply] fetched {len(entries)} entries")
    return entries


def fetch_jobright():
    entries = []
    try:
        text = fetch(JOBRIGHT_URL)
        entries = parse_markdown_table(text, "Jobright")
    except Exception as e:
        log(f"[Jobright] fetch failed: {e}")
    log(f"[Jobright] fetched {len(entries)} entries")
    return entries


def normalize_url(url):
    try:
        p = urlparse(url.lower())
        path = p.path.rstrip("/")
        return p.netloc + path
    except Exception:
        return url.lower()


def normalize_text(s):
    s = s.lower()
    s = YEAR_RE.sub("", s)
    s = SEASON_WORDS_RE.sub("", s)
    s = NON_ALNUM_RE.sub("", s)
    s = WHITESPACE_RE.sub(" ", s).strip()
    return s


def dedup(entries):
    # Step 1: URL dedup
    url_map = {}
    url_deduped = []
    for e in entries:
        nurl = normalize_url(e["url"])
        if nurl in url_map:
            kept = url_map[nurl]
            kept.setdefault("sources", [kept["source"]])
            if e["source"] not in kept["sources"]:
                kept["sources"].append(e["source"])
            kept.setdefault("_dup_ids", []).append(e["id"])
        else:
            e["_norm_url"] = nurl
            url_map[nurl] = e
            url_deduped.append(e)

    log(f"[Dedup] {len(entries)} -> {len(url_deduped)} after URL dedup")

    # Step 2: fuzzy dedup on (company, title)
    final = []
    norm_cache = []
    for e in url_deduped:
        ncompany = normalize_text(e["company"])
        ntitle = normalize_text(e["title"])
        is_dup = False
        for f_entry, f_company, f_title in norm_cache:
            if ncompany == f_company and fuzz.ratio(ntitle, f_title) >= 88:
                f_entry.setdefault("sources", [f_entry["source"]])
                if e["source"] not in f_entry["sources"]:
                    f_entry["sources"].append(e["source"])
                f_entry.setdefault("_dup_ids", []).append(e["id"])
                is_dup = True
                break
        if not is_dup:
            norm_cache.append((e, ncompany, ntitle))
            final.append(e)

    log(f"[Dedup] {len(url_deduped)} -> {len(final)} after fuzzy dedup")
    return final


def keyword_filter(entries):
    kept = []
    for e in entries:
        title = e["title"]
        excluded = False
        for pat in EXCLUDE_PATTERNS:
            if pat.search(title):
                excluded = True
                break
        if not excluded and DATA_ENGINEER_RE.search(title) and not ML_EXCEPTION_RE.search(title):
            excluded = True
        if not excluded:
            kept.append(e)
    log(f"[KeywordFilter] {len(entries)} -> {len(kept)} after keyword pre-filter")
    return kept


def llm_filter(entries, github_token):
    if not entries:
        return entries, [], False

    jobs_payload = [{"id": e["id"], "company": e["company"], "title": e["title"]} for e in entries]

    system_prompt = (
        "You are a job relevance classifier for a UC Berkeley EECS sophomore applying to "
        "internships. Reply ONLY with a JSON array, no other text, no markdown fences."
    )
    user_prompt = (
        "Classify each job as keep or skip based on interest alignment.\n\n"
        "KEEP if the role involves: agentic AI, NLP, LLMs, ML/AI applications (not just theory), "
        "backend systems, computer architecture, quantum computing, full-stack engineering, "
        "embedded systems, compilers, distributed systems, robotics, general software engineering "
        "(SWE roles at any company are usually fine).\n\n"
        "SKIP ONLY if clearly: pure frontend/UI dev with no backend, pure CRM/Salesforce admin, "
        "pure digital marketing or ads tech, pure media streaming infrastructure with no ML, "
        "non-technical roles.\n\n"
        "When in doubt, KEEP.\n\n"
        f"Jobs: {json.dumps(jobs_payload)}\n\n"
        'Reply with: [{"id": "...", "keep": true/false}]'
    )

    try:
        resp = requests.post(
            GITHUB_MODELS_URL,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
        results = json.loads(content)
        keep_map = {r["id"]: bool(r.get("keep", True)) for r in results}

        kept = [e for e in entries if keep_map.get(e["id"], True)]
        skipped = [e for e in entries if not keep_map.get(e["id"], True)]
        log(f"[LLM] {len(kept)} kept / {len(skipped)} skipped")
        return kept, skipped, False
    except Exception as e:
        log(f"[LLM] filter failed: {e}")
        return entries, [], True


def format_locations(locations):
    if not locations:
        return None
    shown = locations[:3]
    text = " | ".join(shown)
    if len(locations) > 3:
        text += f" + {len(locations) - 3} more"
    return text


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


def build_job_message(e):
    lines = [f"🆕 <b>{e['company']}</b> — {e['title']}"]
    loc_str = format_locations(e.get("locations") or [])
    if loc_str:
        lines.append(f"📍 {loc_str}")
    lines.append(f"🏷 {e['source']}")
    lines.append(f'🔗 <a href="{e["url"]}">Apply</a>')
    return "\n".join(lines)


def load_seen():
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
            return set(str(x) for x in data)
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


def prune_skipped_log(records, now, window_hours=AUDIT_WINDOW_HOURS):
    cutoff = now - timedelta(hours=window_hours)
    kept = []
    for r in records:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except Exception:
            continue
        if ts >= cutoff:
            kept.append(r)
    return kept


def main():
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    github_token = os.environ.get("GITHUB_TOKEN")

    if not telegram_token:
        print("ERROR: TELEGRAM_TOKEN environment variable is required", file=sys.stderr)
        sys.exit(1)
    if not telegram_chat_id:
        print("ERROR: TELEGRAM_CHAT_ID environment variable is required", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)

    seen_ids = load_seen()
    log(f"[Seen] loaded {len(seen_ids)} previously seen ids")

    all_entries = []
    all_entries.extend(fetch_simplify())
    all_entries.extend(fetch_speedyapply())
    all_entries.extend(fetch_jobright())
    log(f"[Fetch] total {len(all_entries)} entries across all sources")

    new_entries = [e for e in all_entries if e["id"] not in seen_ids]
    new_count = len(new_entries)
    log(f"[Fetch] {new_count} entries not in seen.json")

    deduped = dedup(new_entries)
    deduped_away_count = new_count - len(deduped)

    prefiltered = keyword_filter(deduped)
    keyword_filtered_count = len(deduped) - len(prefiltered)

    llm_filter_failed = False
    llm_skipped = []
    if github_token:
        final_jobs, llm_skipped, llm_filter_failed = llm_filter(prefiltered, github_token)
    else:
        log("[LLM] GITHUB_TOKEN not set, skipping LLM stage")
        final_jobs = prefiltered
        llm_filter_failed = True

    if llm_filter_failed:
        send_telegram_message(
            telegram_token,
            telegram_chat_id,
            f"⚠️ LLM filter unavailable this run (GitHub Models limit or error). "
            f"Sending all {len(prefiltered)} unfiltered roles.",
        )

    sent_count = 0
    for e in final_jobs:
        msg = build_job_message(e)
        if send_telegram_message(telegram_token, telegram_chat_id, msg):
            sent_count += 1
    log(f"[Telegram] sent {sent_count} messages")

    # mark all new entries (including deduped-away ones) as seen
    new_seen_ids = set(seen_ids)
    for e in new_entries:
        new_seen_ids.add(e["id"])
        for dup_id in e.get("_dup_ids", []):
            new_seen_ids.add(dup_id)

    save_seen(new_seen_ids)
    log(f"[Seen] wrote {len(new_seen_ids)} total seen ids")

    # persist LLM-skipped roles (with timestamps) for the daily audit
    skipped_records = load_skipped_log()
    for e in llm_skipped:
        skipped_records.append({
            "company": e["company"],
            "title": e["title"],
            "source": e["source"],
            "timestamp": now.isoformat(),
        })
    skipped_records = prune_skipped_log(skipped_records, now)
    save_skipped_log(skipped_records)

    # per-run summary message (only if at least one role was processed this run)
    if new_count > 0:
        llm_skipped_count = len(llm_skipped) if not llm_filter_failed else 0
        summary = (
            "📊 <b>Poll run summary</b>\n"
            f"New roles found: {new_count}\n"
            f"Deduplicated away: {deduped_away_count}\n"
            f"Filtered by keyword pre-filter: {keyword_filtered_count}\n"
            f"Filtered by LLM: {llm_skipped_count}\n"
            f"Sent to Telegram: {sent_count}"
        )
        send_telegram_message(telegram_token, telegram_chat_id, summary)

    # daily audit of LLM-skipped roles, sent once at AUDIT_HOUR_UTC
    if now.hour == AUDIT_HOUR_UTC:
        cutoff = now - timedelta(hours=AUDIT_WINDOW_HOURS)
        recent_skips = []
        for r in skipped_records:
            try:
                ts = datetime.fromisoformat(r["timestamp"])
            except Exception:
                continue
            if ts >= cutoff:
                recent_skips.append(r)

        if recent_skips:
            lines = ["🔍 <b>LLM-skipped roles — last 24h</b>"]
            for r in recent_skips:
                lines.append(f"• {r['company']} — {r['title']} ({r['source']})")
            audit_message = "\n".join(lines)

            # Telegram messages are capped at 4096 chars; chunk if needed
            MAX_LEN = 4000
            if len(audit_message) <= MAX_LEN:
                send_telegram_message(telegram_token, telegram_chat_id, audit_message)
            else:
                chunk_lines = [lines[0]]
                for line in lines[1:]:
                    if sum(len(l) + 1 for l in chunk_lines) + len(line) > MAX_LEN:
                        send_telegram_message(telegram_token, telegram_chat_id, "\n".join(chunk_lines))
                        chunk_lines = [lines[0]]
                    chunk_lines.append(line)
                if len(chunk_lines) > 1:
                    send_telegram_message(telegram_token, telegram_chat_id, "\n".join(chunk_lines))
            log(f"[Audit] sent {len(recent_skips)} skipped roles for daily audit")
        else:
            log("[Audit] no LLM-skipped roles in the last 24h")


if __name__ == "__main__":
    main()
