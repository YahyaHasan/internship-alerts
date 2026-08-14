#!/usr/bin/env python3
"""
Probes Greenhouse/Lever/Workday's public APIs to find a company's board,
given just its display name. Not part of the poll pipeline -- a one-off (or
occasionally re-run) research tool to grow companies.py.

Usage:
    python3 ats_poller/slug_finder.py companies.txt
    (one company name per line)

Prints a report and a companies.py-ready snippet for every hit.
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
WORKDAY_URL = "https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

WORKDAY_HOSTS = ["wd1", "wd3", "wd5"]
WORKDAY_SITES = [
    "External", "Careers", "Global", "External_Career_Site",
    "External_Careers", "{tenant}_External", "External_Site",
]

STOPWORDS = {"inc", "corp", "corporation", "llc", "ltd", "co", "company", "the", "group", "technologies", "holdings"}


def slug_variants(name):
    base = name.lower().strip()
    base = re.sub(r"[.,]", "", base)
    words = [w for w in re.split(r"[\s\-/]+", base) if w and w not in STOPWORDS]
    joined = "".join(words)
    hyphen = "-".join(words)
    variants = {joined, hyphen, base.replace(" ", ""), base.replace(" ", "-")}
    return [v for v in variants if v]


def get_json(url, method="GET", timeout=5):
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def check_greenhouse(slug):
    data = get_json(GREENHOUSE_URL.format(slug=slug))
    if data and isinstance(data.get("jobs"), list) and len(data["jobs"]) > 0:
        return len(data["jobs"])
    return None


def check_lever(slug):
    data = get_json(LEVER_URL.format(slug=slug))
    if isinstance(data, list) and len(data) > 0:
        return len(data)
    return None


def check_workday(tenant):
    for wd_host in WORKDAY_HOSTS:
        for site_tmpl in WORKDAY_SITES:
            site = site_tmpl.format(tenant=tenant)
            url = WORKDAY_URL.format(tenant=tenant, wd_host=wd_host, site=site)
            data = get_json(url, method="POST")
            if data and isinstance(data.get("jobPostings"), list) and data.get("total", 0) > 0:
                return wd_host, site, data["total"]
    return None


def probe_company(name):
    for slug in slug_variants(name):
        n = check_greenhouse(slug)
        if n:
            return {"name": name, "platform": "greenhouse", "slug": slug, "jobs": n}
    for slug in slug_variants(name):
        n = check_lever(slug)
        if n:
            return {"name": name, "platform": "lever", "slug": slug, "jobs": n}
    for slug in slug_variants(name):
        result = check_workday(slug)
        if result:
            wd_host, site, n = result
            return {"name": name, "platform": "workday", "tenant": slug, "wd_host": wd_host, "site": site, "jobs": n}
    return {"name": name, "platform": None}


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 slug_finder.py companies.txt", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        names = [line.strip() for line in f if line.strip()]

    hits = {"greenhouse": [], "lever": [], "workday": []}
    misses = []

    for i, name in enumerate(names, 1):
        result = probe_company(name)
        if result["platform"]:
            print(f"[{i}/{len(names)}] {name}: FOUND on {result['platform']} ({result['jobs']} jobs)", flush=True)
            hits[result["platform"]].append(result)
        else:
            print(f"[{i}/{len(names)}] {name}: not found", flush=True)
            misses.append(name)
        time.sleep(0.15)  # be polite to these free public APIs

    print("\n--- companies.py snippet ---\n")
    print("GREENHOUSE_COMPANIES additions:")
    for h in hits["greenhouse"]:
        print(f'    ("{h["name"]}", "{h["slug"]}"),')

    print("\nLEVER_COMPANIES additions:")
    for h in hits["lever"]:
        print(f'    ("{h["name"]}", "{h["slug"]}"),')

    print("\nWORKDAY_COMPANIES additions:")
    for h in hits["workday"]:
        print(f'    ("{h["name"]}", "{h["tenant"]}", "{h["wd_host"]}", "{h["site"]}"),')

    print(f"\n--- summary: {len(hits['greenhouse'])} greenhouse, {len(hits['lever'])} lever, "
          f"{len(hits['workday'])} workday, {len(misses)} not found ---")
    if misses:
        print("Not found (likely custom career site):")
        for m in misses:
            print(f"  - {m}")


if __name__ == "__main__":
    main()
