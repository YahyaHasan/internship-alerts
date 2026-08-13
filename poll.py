#!/usr/bin/env python3
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"

GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
SEEN_FILE = "seen.json"
SKIPPED_LOG_FILE = "skipped_log.json"
AUDIT_HOUR_UTC = 9
AUDIT_WINDOW_HOURS = 24

EXCLUDE_PATTERNS = [
    re.compile(r"\bfrontend\b|\bfront-end\b", re.IGNORECASE),
    re.compile(r"\bUI\b.*\bengineer\b|\bUI/UX\b", re.IGNORECASE),
    re.compile(r"\bSalesforce\b|\bCRM\b", re.IGNORECASE),
    re.compile(r"\bdigital marketing\b|\bSEO\b|\bads?\b.*\btech\b", re.IGNORECASE),
    re.compile(r"\bstreaming\b.*\binfrastructure\b|\bmedia.*platform\b", re.IGNORECASE),
]
DATA_ENGINEER_RE = re.compile(r"\bdata.*engineer\b", re.IGNORECASE)
ML_EXCEPTION_RE = re.compile(r"\bML\b|machine learning|\bAI\b|pipeline for ML", re.IGNORECASE)

ALLOWED_TERMS = {"Summer 2027", "Spring 2027", "Fall 2027", "Winter 2027"}

# Categories confidently in-scope for a UC Berkeley EECS sophomore's interests;
# these skip the LLM call entirely (still pass through the keyword pre-filter).
AUTO_KEEP_CATEGORIES = {"Software", "Software Engineering", "AI/ML/Data", "Data Science, AI & Machine Learning"}

# Applies across all sources (not just Simplify's category field): any title that
# mentions "software" or "AI" as a whole word is a confident keep, skipping the LLM.
AUTO_KEEP_TITLE_RE = re.compile(r"\bsoftware\b|\bai\b", re.IGNORECASE)


def is_auto_keep(e):
    if e.get("category") in AUTO_KEEP_CATEGORIES:
        return True
    return bool(AUTO_KEEP_TITLE_RE.search(e["title"]))


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
        # The upstream JSON is ordered oldest-first (ascending date_posted);
        # sort newest-first so entries are consistent with the other two
        # sources and --limit N reflects the most recent postings.
        data = sorted(data, key=lambda item: item.get("date_posted", 0), reverse=True)
        for item in data:
            if not (item.get("active") and item.get("is_visible")):
                continue
            entries.append({
                "id": item.get("id"),
                "company": item.get("company_name", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "locations": item.get("locations", []) or [],
                "source": "Simplify",
                "category": item.get("category") or None,
                "_terms": set(item.get("terms") or []),
            })
    except Exception as e:
        log(f"[Simplify] fetch failed: {e}")
    log(f"[Simplify] fetched {len(entries)} entries")
    return entries


def normalize_url(url):
    try:
        p = urlparse(url.lower())
        path = p.path.rstrip("/")
        return p.netloc + path
    except Exception:
        return url.lower()


def simplify_term_filter(entries):
    """Only applies to Simplify entries (which carry a '_terms' set); other
    sources pass through untouched. Runs on entries already filtered against
    seen_ids, so term-excluded entries are never marked seen and keep getting
    freshly re-evaluated every run in case the source updates the terms field."""
    kept = []
    excluded = 0
    for e in entries:
        if e.get("source") == "Simplify":
            terms = e.get("_terms") or set()
            if not terms & ALLOWED_TERMS:
                excluded += 1
                continue
        kept.append(e)
    log(f"[TermFilter] {len(entries)} -> {len(kept)} after Simplify term filter (excluded {excluded} not in {sorted(ALLOWED_TERMS)})")
    return kept


def dedup(entries):
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
    return url_deduped


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


RATE_LIMIT_HEADER_CANDIDATES = [
    "retry-after",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "x-ratelimit-reset",
]


def _extract_rate_limit_info(resp):
    """Best-effort extraction of reset/remaining info from response headers.
    Header names are not guaranteed by GitHub Models; capture what's present."""
    headers = {k.lower(): v for k, v in resp.headers.items()}
    info = {}
    for key in RATE_LIMIT_HEADER_CANDIDATES:
        if key in headers:
            info[key] = headers[key]
    for key in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens", "x-ratelimit-remaining"):
        if key in headers:
            info[key] = headers[key]
    return info


def llm_filter(entries, github_token):
    if not entries:
        return entries, [], {"failed": False}

    jobs_payload = []
    for e in entries:
        job = {"id": e["id"], "company": e["company"], "title": e["title"]}
        category = e.get("category")
        if category:
            job["category"] = category
        jobs_payload.append(job)

    system_prompt = (
        "You are a job relevance classifier for a UC Berkeley EECS sophomore applying to "
        "internships. Reply ONLY with a JSON array, no other text, no markdown fences."
    )
    user_prompt = (
        "Classify each job as keep or skip based on interest alignment.\n\n"
        "Each job has a title and, when available, a 'category' field from the source "
        "listing (e.g. 'Software Engineering', 'Data Science', 'Hardware Engineering') — "
        "no full job description is available, so weigh title and category together; "
        "when neither gives a confident signal, default to KEEP per the rule below.\n\n"
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
    except requests.exceptions.RequestException as e:
        log(f"[LLM] network error: {e}")
        return entries, [], {"failed": True, "reason": "network_error", "detail": str(e)}

    if resp.status_code == 429:
        rl_info = _extract_rate_limit_info(resp)
        log(f"[LLM] rate limited (HTTP 429). headers: {rl_info}")
        return entries, [], {"failed": True, "reason": "rate_limit", "rate_limit_info": rl_info}

    try:
        resp.raise_for_status()
        rl_info = _extract_rate_limit_info(resp)
        if rl_info:
            log(f"[LLM] rate limit headers: {rl_info}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
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
        return entries, [], {"failed": True, "reason": "error", "detail": str(e)}


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


def parse_limit_arg(argv):
    for i, arg in enumerate(argv):
        if arg == "--limit" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return None
        if arg.startswith("--limit="):
            try:
                return int(arg.split("=", 1)[1])
            except ValueError:
                return None
    return None


def main():
    dry_run = "--dry-run" in sys.argv
    limit = parse_limit_arg(sys.argv)

    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    github_token = os.environ.get("GITHUB_TOKEN")

    if not dry_run:
        if not telegram_token:
            print("ERROR: TELEGRAM_TOKEN environment variable is required", file=sys.stderr)
            sys.exit(1)
        if not telegram_chat_id:
            print("ERROR: TELEGRAM_CHAT_ID environment variable is required", file=sys.stderr)
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

    all_entries = []
    all_entries.extend(fetch_simplify())
    log(f"[Fetch] total {len(all_entries)} entries across all sources")

    if limit is not None:
        all_entries = all_entries[:limit]
        log(f"[Limit] --limit {limit} set: restricting to first {len(all_entries)} entries fetched")

    new_entries_raw = [e for e in all_entries if e["id"] not in seen_ids]
    log(f"[Fetch] {len(new_entries_raw)} entries not in seen.json")

    new_entries = simplify_term_filter(new_entries_raw)
    new_count = len(new_entries)

    deduped = dedup(new_entries)
    deduped_away_count = new_count - len(deduped)

    prefiltered = keyword_filter(deduped)
    keyword_filtered_count = len(deduped) - len(prefiltered)

    auto_keep = [e for e in prefiltered if is_auto_keep(e)]
    needs_llm = [e for e in prefiltered if not is_auto_keep(e)]
    log(f"[AutoKeep] {len(auto_keep)} auto-kept by category, {len(needs_llm)} need LLM classification")

    llm_info = {"failed": False}
    llm_skipped = []
    if not needs_llm:
        final_jobs = auto_keep
    elif github_token:
        llm_kept, llm_skipped, llm_info = llm_filter(needs_llm, github_token)
        final_jobs = auto_keep + llm_kept
    else:
        log("[LLM] GITHUB_TOKEN not set, skipping LLM stage")
        final_jobs = auto_keep + needs_llm
        llm_info = {"failed": True, "reason": "no_token"}

    llm_filter_failed = llm_info["failed"]

    if llm_filter_failed:
        reason = llm_info.get("reason")
        stats_lines = [
            "",
            "<b>Run stats</b>",
            f"New roles found: {new_count}",
            f"Deduplicated away: {deduped_away_count}",
            f"Filtered by keyword pre-filter: {keyword_filtered_count}",
            f"Auto-kept by category: {len(auto_keep)}",
            f"Sent to LLM for classification: {len(needs_llm)}",
            f"Sending all {len(needs_llm)} unfiltered roles (plus {len(auto_keep)} auto-kept)",
        ]

        if reason == "rate_limit":
            rl_info = llm_info.get("rate_limit_info") or {}
            retry_after = rl_info.get("retry-after")
            reset_line = None
            if retry_after:
                reset_line = f"Resets in: ~{retry_after}s (from Retry-After header)"
            else:
                reset_reqs = rl_info.get("x-ratelimit-reset-requests") or rl_info.get("x-ratelimit-reset")
                if reset_reqs:
                    reset_line = f"Resets in: {reset_reqs} (from rate limit header)"
                else:
                    reset_line = "Resets in: unknown (GitHub Models did not return a reset header)"
            header_lines = [
                "⚠️ <b>LLM rate limit hit</b> (GitHub Models HTTP 429)",
                reset_line,
            ]
        elif reason == "no_token":
            header_lines = ["⚠️ LLM filter skipped: GITHUB_TOKEN not set"]
        elif reason == "network_error":
            header_lines = [f"⚠️ LLM filter network error: {html.escape(str(llm_info.get('detail', ''))[:300])}"]
        else:
            header_lines = [f"⚠️ LLM filter error: {html.escape(str(llm_info.get('detail', 'unknown error'))[:300])}"]

        notify("\n".join(header_lines + stats_lines))

    sent_count = 0
    for e in final_jobs:
        msg = build_job_message(e)
        if notify(msg):
            sent_count += 1
    log(f"[Telegram] sent {sent_count} messages" + (" (dry run)" if dry_run else ""))

    # mark every evaluated entry as seen (term-excluded, deduped-away, keyword-
    # filtered, and LLM-skipped roles all get marked seen too, consistent with
    # how every filter stage behaves — a rejected id is never re-evaluated)
    new_seen_ids = set(seen_ids)
    for e in new_entries_raw:
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
            f"Auto-kept by category: {len(auto_keep)}\n"
            f"Sent to LLM for classification: {len(needs_llm)}\n"
            f"Filtered by LLM: {llm_skipped_count}\n"
            f"Sent to Telegram: {sent_count}"
        )
        notify(summary)

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
                lines.append(
                    f"• {html.escape(r['company'])} — {html.escape(r['title'])} "
                    f"({html.escape(r['source'])})"
                )
            audit_message = "\n".join(lines)

            # Telegram messages are capped at 4096 chars; chunk if needed
            MAX_LEN = 4000
            if len(audit_message) <= MAX_LEN:
                notify(audit_message)
            else:
                chunk_lines = [lines[0]]
                for line in lines[1:]:
                    if sum(len(l) + 1 for l in chunk_lines) + len(line) > MAX_LEN:
                        notify("\n".join(chunk_lines))
                        chunk_lines = [lines[0]]
                    chunk_lines.append(line)
                if len(chunk_lines) > 1:
                    notify("\n".join(chunk_lines))
            log(f"[Audit] sent {len(recent_skips)} skipped roles for daily audit")
        else:
            log("[Audit] no LLM-skipped roles in the last 24h")


if __name__ == "__main__":
    main()
