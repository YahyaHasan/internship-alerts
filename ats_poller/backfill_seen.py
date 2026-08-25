#!/usr/bin/env python3
"""
Seeds seen_ats.json with the currently-open postings for companies that
have never been polled before -- run this BEFORE pushing any addition to
companies.py, in the same commit as the addition.

Why this exists: adding a company to companies.py without backfilling means
the next real (non-dry-run) poll treats every one of that company's
existing open postings as brand new, since none of their ids are in
seen_ats.json yet -- so the poller alerts on a company's entire backlog at
once instead of just genuinely new postings. This happened on 2026-08-25
when 185 companies were migrated from a SimplifyJobs-based poller without
backfilling first: the first production run sent 441 Telegram messages,
and a second overlapping run (concurrent because a single run now takes
8-12+ minutes with this many companies, longer than the ~5 min external
cron interval) sent 442 more, unfiltered, before failing to commit its own
state. Never sends anything to Telegram -- only fetches and records ids.

Usage:
    # Backfill every company in companies.py with zero ids currently in
    # seen_ats.json (the common case right after adding new companies):
    python3 ats_poller/backfill_seen.py

    # Backfill specific companies by exact display name (comma-separated),
    # regardless of whether they already have some ids seen:
    python3 ats_poller/backfill_seen.py --company "Tesla,AMD"

    # See what would be backfilled without writing seen_ats.json:
    python3 ats_poller/backfill_seen.py --dry-run
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "adapters"))
from companies import (  # noqa: E402
    ASHBY_COMPANIES,
    GREENHOUSE_COMPANIES,
    LEVER_COMPANIES,
    SMARTRECRUITERS_COMPANIES,
    WORKABLE_COMPANIES,
    WORKDAY_COMPANIES,
)
from adapters import ashby, greenhouse, lever, smartrecruiters, workable, workday  # noqa: E402

BASE_DIR = Path(__file__).parent
SEEN_FILE = BASE_DIR / "seen_ats.json"

# (id prefix used by that adapter, fetch fn, entries in companies.py)
PLATFORMS = [
    ("gh_", lambda name, slug: greenhouse.fetch(name, slug), GREENHOUSE_COMPANIES),
    ("lv_", lambda name, slug: lever.fetch(name, slug), LEVER_COMPANIES),
    ("ashby_", lambda name, slug: ashby.fetch(name, slug), ASHBY_COMPANIES),
    ("sr_", lambda name, slug: smartrecruiters.fetch(name, slug), SMARTRECRUITERS_COMPANIES),
    ("workable_", lambda name, account: workable.fetch(name, account), WORKABLE_COMPANIES),
    ("wd_", lambda name, tenant, wd_host, site: workday.fetch(name, tenant, wd_host, site), WORKDAY_COMPANIES),
]


def log(msg):
    print(msg, flush=True)


def load_seen():
    try:
        return set(json.load(open(SEEN_FILE)))
    except Exception:
        return set()


def save_seen(ids):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=2)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", help="Comma-separated exact display names to force-backfill, "
                                           "regardless of existing seen ids")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report, but don't write seen_ats.json")
    args = parser.parse_args()

    seen_ids = load_seen()
    log(f"[Seen] loaded {len(seen_ids)} existing ids")

    forced_names = None
    if args.company:
        forced_names = {n.strip() for n in args.company.split(",") if n.strip()}

    tasks = []
    for id_prefix, fetch_fn, company_list in PLATFORMS:
        for entry in company_list:
            name = entry[0]
            if forced_names is not None:
                if name not in forced_names:
                    continue
            else:
                # auto mode: only backfill companies with literally zero ids
                # already recorded under this platform's id prefix for this
                # slug -- i.e. never successfully polled before.
                slug_fragment = entry[1]
                already_seen = any(
                    i.startswith(id_prefix) and slug_fragment in i for i in seen_ids
                )
                if already_seen:
                    continue
            tasks.append((fetch_fn, entry, name))

    if not tasks:
        log("[Backfill] nothing to do -- every company already has seen ids on record")
        return

    log(f"[Backfill] {len(tasks)} companies to fetch")

    new_ids = set()
    errors = []

    def job(t):
        fetch_fn, entry, name = t
        try:
            got = fetch_fn(*entry)
            return name, [e["id"] for e in got], None
        except Exception as e:
            return name, [], str(e)

    with ThreadPoolExecutor(max_workers=25) as ex:
        futs = [ex.submit(job, t) for t in tasks]
        for i, fut in enumerate(as_completed(futs)):
            name, ids, err = fut.result()
            if err:
                errors.append((name, err))
                log(f"[Backfill] ERROR {name}: {err}")
            else:
                new_ids.update(ids)
            if (i + 1) % 25 == 0:
                log(f"[Backfill] ...{i + 1}/{len(tasks)}")

    added = new_ids - seen_ids
    log(f"[Backfill] {len(new_ids)} current postings fetched, {len(added)} new ids to add, "
        f"{len(errors)} companies errored")

    if args.dry_run:
        log("[DryRun] not writing seen_ats.json")
        return

    save_seen(seen_ids | new_ids)
    log(f"[Seen] wrote {len(seen_ids | new_ids)} total seen ids")

    if errors:
        log("[Backfill] WARNING: some companies errored and were NOT backfilled -- "
            "their first real poll run will still alert on their full current backlog. "
            "Re-run this script (or fix the adapter) before that happens.")
        sys.exit(1)


if __name__ == "__main__":
    main()
