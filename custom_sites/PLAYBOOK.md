# Building a new custom-site scraper

This file is the handoff doc for adding a new company to `custom_sites/` —
written so a fresh Claude session (no prior context on this repo) can pick
up one company and build a working adapter end to end.

## Where this fits

`custom_sites/custom_poll.py` is one of three independent pollers in this
repo:

- `poll.py` (repo root) — polls the SimplifyJobs community-maintained
  aggregator. **Do not touch.**
- `ats_poller/ats_poll.py` — polls companies hosted on a standard ATS
  (Greenhouse, Lever, Workday) via each platform's public JSON API. Company
  list lives in `ats_poller/companies.py`. **Do not touch for this task.**
- `custom_sites/custom_poll.py` — **this is what you're extending.** For
  companies whose careers site is bespoke (not on a standard ATS), each
  needs its own hand-built adapter.

All three run on independent GitHub Actions workflows
(`.github/workflows/{poll,ats_poll,custom_poll}.yml`), each triggered
externally via `workflow_dispatch` by a cron-job.org job hitting the GitHub
API every 5-15 min (GitHub's own native `schedule:` trigger was found to be
unreliable at sub-hourly intervals — see git history on `poll.yml` if
curious). **Adding a new company to `custom_sites` does NOT require a new
cron job** — it rides the existing `custom_poll.yml` schedule automatically,
since `custom_poll.py`'s `fetch_all()` just loops over every registered
adapter in one run.

## The adapter contract

Each adapter lives at `custom_sites/adapters/{company}.py` and exposes a
`fetch()` function returning a list of dicts shaped like:

```python
{
    "id": "companyprefix_<something globally unique and stable>",
    "company": "Display Name",
    "title": "Job title as posted",
    "url": "https://.../apply-or-view-link",
    "locations": ["City, ST, USA", ...],  # list of strings, can be empty
    "source": "CompanyDisplayName",  # shown in the Telegram message footer
}
```

Requirements:
- `id` must be **stable across runs** (same posting = same id every time) and
  **globally unique** (prefix with a short site tag, e.g. `aws_`, `meta_`, so
  it can never collide with another adapter's ids in the shared
  `seen_custom.json`).
- Never include an entry with a missing/null `url` or `title` — the pipeline
  drops these automatically (`main()` filters on `e.get("title") and
  e.get("url")`), but better to skip them in the adapter and log why.
- Wrap all network calls in a `try/except` **in `fetch_all()`**, not inside
  the adapter — see the existing Google entry in `custom_poll.py` for the
  pattern. One company's site being down/blocked should never take down the
  rest of the run.

Then register it in `custom_sites/custom_poll.py`'s `fetch_all()`:

```python
try:
    got = your_company.fetch()
    log(f"[YourCompany] fetched {len(got)} jobs")
    entries.extend(got)
except Exception as e:
    log(f"[YourCompany] fetch failed: {e}")
```

And add the import at the top: `from adapters import google_careers,
your_company  # noqa: E402`.

## Filtering: what the adapter should and shouldn't do

The shared pipeline (`custom_poll.py`) already applies, after every
adapter's `fetch()` runs:

1. Drop entries with an explicit stale year in the title (2023-2026, unless
   also mentioning 2027/2028) — see `term_filter_ok()`.
2. Drop obvious frontend/CRM/marketing titles — see `EXCLUDE_PATTERNS`.
3. Optional Groq LLM interest classification (`GROQ_API_KEY` secret) against
   the user's stated interests (SWE/backend/AI-ML/systems/distributed
   systems/robotics/etc.) — same prompt as `ats_poller`.

**What the adapter itself must do**: narrow the fetch to intern/entry-level
roles using **the site's own facet/filter, not a title-keyword guess**.
This matters a lot — do not assume titles contain the word "intern".
Concrete example: Google's internship-tier roles are titled "Student
Researcher, PhD", "Apprenticeship in ...", etc. and almost never say
"intern" — the ATS-hosted companies (`ats_poller`) get away with a title
regex because Greenhouse/Lever/Workday companies overwhelmingly do title
their internships literally, but that's not a safe assumption for a
from-scratch site. Find the site's own experience-level/employment-type
filter (see investigation steps below) and use that server-side, so the
adapter only ever fetches roles that are actually intern-tier.

## Investigation steps (do this before writing any code)

This is the actual process used for the Google adapter
(`custom_sites/adapters/google_careers.py`) — repeat it per company.

1. **Check if the page is server-rendered.** `curl` the company's jobs/careers
   search URL with a normal browser `User-Agent` header. If the HTML back
   has real content (job titles visible in the raw response), you may not
   need a browser at all — skip to step 3.

   ```bash
   curl -s "https://company.com/careers/jobs" \
     -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36" \
     | grep -o "some-expected-job-title-fragment"
   ```

   If the response is empty/skeleton HTML (a `<div id="root"></div>` and a
   pile of `<script>` tags), the data loads client-side — go to step 2.

2. **Use the browser tools to find the real data source.** Load the claude-in-chrome
   tools (`ToolSearch` for `mcp__claude-in-chrome__*` if not already loaded),
   navigate to the jobs page, then call `read_network_requests` (filtered by
   a guess like the company domain or "api"/"jobs") to find the XHR/fetch
   call that actually returns job data. Common patterns to look for:
   - A dedicated `/api/jobs` or GraphQL endpoint returning clean JSON — best
     case, just hit it directly with `requests`.
   - Embedded JSON in the initial HTML via a framework-specific mechanism:
     `__NEXT_DATA__` (Next.js), `window.__INITIAL_STATE__` (many
     React/Redux apps), `AF_initDataCallback` (Google/Wiz-based sites —
     Google Careers, Google Flights, Search all use this), Apollo/GraphQL
     `window.__APOLLO_STATE__`. If you find one of these, re-run the plain
     `curl` from step 1 to confirm the *same* embedded data appears in the
     server-rendered HTML — it very often does, meaning you *still* don't
     need a headless browser, just a regex + `json.loads` to pull it out
     (this is exactly what happened with Google: looked like it needed a
     browser, turned out `curl` + a regex was enough).
   - If genuinely nothing works without JS execution (data only appears
     after client-side rendering with no embedded/XHR JSON anywhere), that
     company needs a headless-browser adapter (Playwright, run in the GitHub
     Actions job) — meaningfully heavier, flag this to the user before
     building it, since it changes the workflow's dependencies and runtime.

3. **Find the real intern/entry-level filter.** Use the site's own job-search
   UI filters (checkboxes/dropdowns for "Experience level", "Employment
   type", etc.) and watch how the URL or the request payload changes when
   you toggle them — that's the param to replicate in the adapter, exactly
   like `target_level=INTERN_AND_APPRENTICE` was reverse-engineered from
   Google's "Intern & Apprentice" checkbox.

4. **Confirm you can parse it in Python with no JS engine.** Save a real
   response to a file and try `json.loads()` (or a small regex extraction
   first, if it's embedded in a script tag) in a plain `python3` shell.

5. **Figure out pagination.** Look for `page`/`offset`/`cursor` params and a
   total-count field in the response so you know when to stop. Add a
   `MAX_PAGES` safety cap regardless (see `workday.py` or
   `google_careers.py` for the pattern) — never loop unbounded.

6. **Sanity-check for edge-case entries.** Fetch a real page of results and
   check for entries with null/missing URLs, weird aggregate/collection
   listings that aren't real individual postings (Google had a few "Open
   Engineering Career Opportunities, CapitalG Portfolio Companies" rows with
   no apply link — skip these at the source).

7. **Think about the blocking question honestly.** Nobody can guarantee a
   site won't ever rate-limit or block a scraper. The real risk assessment:
   is this a public-facing page meant for any visitor including logged-out
   users and search crawlers (low risk, e.g. Google Careers), or something
   that looks like an authenticated/internal API not meant for external
   traffic (higher risk, treat cautiously, consider lower polling frequency
   for that one adapter specifically if genuinely worried, or skip it)? A
   single low-frequency GET per poll cycle (every 5-15 min) is a very
   different risk profile than concurrent/high-frequency scraping — but
   still not zero risk. Make sure `fetch_all()`'s try/except means one
   blocked site fails that one adapter's log line, not the whole run.

8. **Write and test the adapter standalone first**, before wiring it in:

   ```bash
   python3 -c "
   import sys; sys.path.insert(0, 'custom_sites')
   from adapters import your_company
   jobs = your_company.fetch()
   print(len(jobs))
   for j in jobs[:5]: print(j['company'], '|', j['title'], '|', j['locations'])
   "
   ```

9. **Wire it into `fetch_all()`, then run the full pipeline dry-run**:

   ```bash
   python3 custom_sites/custom_poll.py --dry-run
   ```

   Check the log for `fetch failed` lines (should be none) and skim the
   `[DryRun] would send Telegram message` output for garbage/malformed
   entries.

10. **Seed before going live.** A brand-new adapter's first real (non-dry)
    run will treat every currently-open posting as "new" and notify on all
    of them at once. To avoid that flood, run once with `--dry-run` (which
    still writes `seen_custom.json`, just skips the actual Telegram send)
    right before merging, so the next *real* scheduled run only alerts on
    genuinely new postings going forward. Don't commit a `--dry-run` seed
    that includes postings from a much earlier test run, though — re-run it
    fresh right before commit so you're not silently suppressing a
    still-unnotified real posting (see git history around
    `custom_sites/seen_custom.json` for why this matters in practice).

## Companies still needed (from the original interest list)

Not yet covered by `ats_poller` (Greenhouse/Lever/Workday) or
`custom_sites` (Google done): **Splunk, Broadcom, Sandisk, Uber, Snap,
Salesforce, Meta, Apple, Slack, OpenAI, Microsoft, Tesla, AWS, IBM, NASA,
Cisco, TikTok, Bloomberg, Axiado, PayPal, Cloudera.**

Each needs the investigation process above run individually — assume
roughly 15-30 minutes of real work per company, and expect a wide range of
difficulty (some will be a `curl` + regex like Google; others will need
headless-browser rendering or turn out to have real bot protection worth
flagging back to the user before investing more time).

## Known infra issue to be aware of

All three pollers (`poll.py`, `ats_poll.py`, `custom_poll.py`) commit and
push their `seen_*.json` state file directly to `main` at the end of every
run. Because all three run frequently (every 5-15 min) on independent
schedules, two runs landing close together can race: the second one's
`git push` gets rejected as non-fast-forward since the first already moved
`main`. This shows up as the workflow's final "Commit ..." step failing
(and the run reporting overall `failure`) even though the actual poll logic
and Telegram sends succeeded — the only real cost is that run's
`seen_*.json` update gets silently dropped, so whatever it found gets
re-evaluated (and potentially re-notified) on the next run. Not yet fixed;
the fix would be a fetch+rebase+retry loop around the `git push` in each
workflow's commit step. Worth doing if this becomes a repeated visible
problem (duplicate Telegram messages) rather than an occasional harmless
retry.
