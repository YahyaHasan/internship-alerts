"""
Filtering, LLM classification, messaging, and state-persistence helpers
shared by ats_poller/ats_poll.py and custom_sites/custom_poll.py. Both
pollers behave the same way in all of this (same deny/allow terms, same
stale-year and non-US-location rules, same LLM prompt, same Telegram message
format, same seen/skipped-log file shape) even though they pull from
different sources -- keeping one copy here means a change doesn't need to be
made twice and can't drift between the two pollers. Each poller still owns
its own state files (seen_ats.json vs seen_custom.json, etc.) and passes its
own path into the load/save helpers below.
"""
import html
import json
import re

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"

# Explicit stale-year postings (leftover listings from a prior cycle) get
# dropped; anything with no year mentioned, or a 2027/2028 mention, passes.
STALE_YEAR_RE = re.compile(r"\b(2023|2024|2025|2026)\b")
CURRENT_YEAR_RE = re.compile(r"\b(2027|2028)\b")

# Titles clearly outside our interest area get dropped before the LLM step
# (cheaper, and avoids relying on the LLM to catch obvious non-matches).
DENY_TITLE_RE = re.compile(
    r"\b(Sales|Marketing|Recruiting|Recruiter|Manufacturing|CAD|Mechanical|Electrical|Cyber|Mobile|"
    r"Quant|Analog|Trader|Trading|Robotics?|Supply Chain|Help Desk|Service Desk|Facilities|"
    r"Human Resources|Accounting|Actuarial|Legal|Purchasing|Executive Assistant|Real Estate|"
    r"SkillBridge|Avionics|Propulsion|Structures|Biologics|Chemical|Materials|"
    r"Hardware|Data Scien(ce|tist)s?|"
    # Non-technical business functions.
    r"Finance|Financial|FP&A|Treasury|Tax|Audit|MBA|Investment|Procurement|Sourcing|"
    r"Business Development|Account Development|SDR|Leadership Development|Consulting|Strategy|"
    r"Public Policy|Public Affairs|Customer Service|Customer Support|Technical Support|"
    # Operations / EHS / warehouse-floor management.
    r"Operations|Area Manager|Environmental|Sustainability|Safety|"
    # Fab & manufacturing engineering -- the LLM prompt already says to skip
    # these, so denying them up front just saves the call.
    r"Process (Development|Integration|Engineer)|Industrial Engineer|Quality|QA|"
    r"Validation|Test Engineer|Clinical|Pharmaceutical|"
    # Non-eng program/business functions found by manual labeling
    # (2026-09-15 sample: Google Security Consultant, Cisco/Microsoft
    # Product/Program Management titles all rejected by the user).
    r"Consultant|Product Management|Technical Program Management|TPM|"
    # Requires an active security clearance -- not something an
    # undergrad intern applicant has. "CTJ" is Microsoft's clearance-track
    # job-family code (seen suffixed "- CTJ - TS" on cleared postings).
    r"CTJ|Top Secret|TS/SCI|Security Clearance|"
    # Semiconductor/silicon physical-design engineering -- distinct from
    # software/firmware/embedded roles at the same companies (e.g. Nvidia
    # "System Software Intern" was kept, "Physical Design ... Intern" was
    # not, in the same labeling sample).
    r"Physical Design|Silicon Engineering|SoC|DRAM|Design Evaluation|"
    r"Node Development|Product Applications|Package (Design|Engineering))\b",
    re.IGNORECASE,
)

# Degree-gated grad roles -- not applicable to an undergrad. Kept separate
# from DENY_TITLE_RE (rather than folded in) because ALLOW_TITLE_RE would
# otherwise rescue it: nearly every PhD/Master's posting also says "Software
# Engineering Intern" or similar, so it'd match ALLOW_TITLE_RE and slip back
# in. This one is a hard stop with no escape hatch.
DEGREE_GATE_RE = re.compile(
    r"\b(PhD|Ph\.D\.?|Master'?s|Doctoral|Grad(uate)? Intern)\b", re.IGNORECASE
)

# Escape hatch for DENY_TITLE_RE: a title matching this is kept even if it
# also matches a denied term, since these words identify the role as one we
# want regardless of what else the title says. Without it a genuinely
# relevant posting is lost outright -- the Groq classifier runs *after* the
# keyword filter, so it never sees a denied title and can't rescue one.
# Real examples this saves: "FY27 Engineering Intern - Hardware, Software &
# Systems" (denied by Hardware) and "Internship - Product Engineering (Data
# Science: Machine Learning Analyst)" (denied by Data Science).
#
# Deliberately narrow and high-precision: every term here must be one that
# can't plausibly appear in a role we don't want, or it silently undoes the
# deny list. Bare "AI" is excluded for exactly that reason -- it shows up in
# titles like "AI & Strategic Marketing Intern" and "Digital Marketing Intern
# - Technical AI & Automation". Bare "Agent" is safe by contrast: all 7
# Agent-matching titles across both pollers' live corpus are genuine agentic
# -AI/software roles.
ALLOW_TITLE_RE = re.compile(
    r"\b(Software|Agentic|Agents?|Machine Learning|AI/ML|ML|LLMs?|NLP|Generative AI|"
    r"Compilers?|Distributed Systems|Back[- ]?end|Full[- ]?Stack|Embedded|Quantum)\b",
    re.IGNORECASE,
)

# We can't reliably enumerate every valid "US" location string (bare city
# names, "Remote", full state names, "Bay Area", etc. all vary by
# ATS/adapter), so an allowlist would silently drop legitimate US roles that
# don't happen to match. Instead, blocklist locations that are unambiguously
# non-US; any entry not matching this (including ones with no location data,
# or an unrecognized location) is kept.
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


def term_filter_ok(title):
    if STALE_YEAR_RE.search(title) and not CURRENT_YEAR_RE.search(title):
        return False
    return True


def location_filter_ok(locations):
    """A posting's 'locations' field is a list (a role can span multiple
    offices). Reject only if EVERY listed location is unambiguously non-US --
    a multi-location posting that includes a US site should survive even if
    it also lists a foreign one."""
    if not locations:
        return True
    return any(not NON_US_LOCATION_RE.search(loc) for loc in locations)


# System/user prompt for the Groq classification pass that runs after the
# keyword filter. Tuned against a manually-labeled sample (2026-09-15, 43
# US/non-PhD candidates, 7 the user would actually apply to) -- the original
# "when in doubt, KEEP" version kept generalist/non-software titles like
# "Special Projects Intern", "Customer and Partner Solutions Engineering
# Intern", and "Digital Engineering Intern - BIM" that the user rejected.
# This version flips the default to skip on any *named* non-software
# discipline, but still keeps titles with literally no discipline named at
# all (e.g. "<Company> Summer Internship") since there's no signal either way
# there and skipping those turned out to cost real matches (e.g. E-Space).
LLM_SYSTEM_PROMPT = (
    "You are a strict job relevance classifier for a UC Berkeley EECS sophomore who applies to "
    "roughly 1 in 50 internships they see -- they want the filter aggressive, not lenient. "
    "Reply ONLY with a JSON array, no other text, no markdown fences."
)

LLM_USER_PROMPT_TEMPLATE = (
    "Classify each internship posting as keep or skip. Only a title and company are available, "
    "no full description -- judge from the title alone, and read it literally rather than "
    "optimistically (do not assume a vague or generalist title secretly involves core software work).\n\n"
    "KEEP if the title clearly signals hands-on software/ML engineering work: writing code, "
    "building systems, or ML/AI model or infrastructure work. Examples: Software Engineer Intern, "
    "Backend/Full-Stack/Embedded Software Intern, Machine Learning/AI Engineer Intern, Systems "
    "Software Intern, Research Intern with ML/AI/NLP/LLM explicitly in the title, Compiler/Distributed "
    "Systems Intern.\n\n"
    "ALSO KEEP if the title is truly bare with no discipline or function named at all -- just "
    '"<Company> [Year] Summer Internship/Internship Program" and nothing else. With zero information '
    "either way, default to keep.\n\n"
    "SKIP everything else, including: titles with a named non-software function or generalist/business "
    'euphemism ("Special Projects Intern", "Rotational Program", "Design Intern" with no '
    '"software"/"engineering" qualifier, generic "R&D Engineering Intern", generic "Digital '
    'Engineering Intern"); customer-facing or solutions/consulting engineering ("Customer and Partner '
    'Solutions Engineering", "Solutions Engineer", "Sales/Field Engineering"); IT/helpdesk/sysadmin '
    'roles ("Information Technology Intern" with no "software developer/engineer" wording); '
    'hardware, silicon, firmware-adjacent-but-not-software roles unless the title itself says '
    '"Software" or "Firmware Engineer"; product/UX/industrial design ("Product Design '
    'Engineering", unless it explicitly says software design); biotech/pharma/clinical/research-science '
    "roles with no software or ML component; civil/construction/BIM engineering; energy/utility/grid "
    "engineering unless it says software; any role whose primary discipline is named and is not "
    "software or ML engineering.\n\n"
    "When in doubt between a named non-software discipline and software, SKIP. When the title names "
    "no discipline at all, KEEP.\n\n"
    "Jobs: {jobs_json}\n\n"
    'Reply with: [{{"id": "...", "keep": true/false}}]'
)


def llm_filter(entries, groq_api_key, log=lambda msg: None):
    """Classifies `entries` (each needs at least id/company/title) as
    keep/skip via Groq. Returns (kept, skipped, info); on any failure, fails
    open -- returns all entries as kept with info["failed"] = True, so a
    transient Groq outage doesn't silently drop postings the keyword filter
    already thought were worth showing."""
    if not entries:
        return entries, [], {"failed": False}

    jobs_payload = [
        {"id": e["id"], "company": e["company"], "title": e["title"], "locations": e.get("locations") or []}
        for e in entries
    ]
    user_prompt = LLM_USER_PROMPT_TEMPLATE.format(jobs_json=json.dumps(jobs_payload))

    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": LLM_SYSTEM_PROMPT},
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


def send_telegram_message(token, chat_id, text, log=lambda msg: None):
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


def load_seen(seen_file):
    try:
        with open(seen_file, "r") as f:
            return set(str(x) for x in json.load(f))
    except Exception:
        return set()


def save_seen(seen_ids, seen_file):
    with open(seen_file, "w") as f:
        json.dump(sorted(seen_ids), f, indent=2)
        f.write("\n")


def load_skipped_log(skipped_log_file):
    try:
        with open(skipped_log_file, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_skipped_log(records, skipped_log_file):
    with open(skipped_log_file, "w") as f:
        json.dump(records, f, indent=2)
        f.write("\n")
