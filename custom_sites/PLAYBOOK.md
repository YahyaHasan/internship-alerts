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
  (Greenhouse, Lever, Ashby, Workday) via each platform's public JSON API.
  Company list lives in `ats_poller/companies.py`. **Do not touch for this
  task.**
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
  the adapter — see any existing entry in `custom_poll.py` for the pattern.
  One company's site being down/blocked should never take down the rest of
  the run.

Then register it in `custom_sites/custom_poll.py`'s `fetch_all()`:

```python
try:
    got = your_company.fetch()
    log(f"[YourCompany] fetched {len(got)} jobs")
    entries.extend(got)
except Exception as e:
    log(f"[YourCompany] fetch failed: {e}")
```

And add the import at the top (alphabetical, in the existing `from adapters
import (...)` block).

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
roles using **the site's own facet/filter, not a title-keyword guess**, and
to the **US locale using the site's own country facet/field**, not a
substring guess. Concrete examples of why title-keyword filtering fails:
Google's internship-tier roles are titled "Student Researcher, PhD",
"Apprenticeship in ...", etc. and almost never say "intern"; Cisco's site
splits internship-tier postings across two distinct facet values ("Intern"
and "Internships, Apprenticeships, and Co-Ops") that both need to be
selected. The ATS-hosted companies (`ats_poller`) get away with a title
regex because Greenhouse/Lever/Workday companies overwhelmingly do title
their internships literally, but that's not a safe assumption for a
from-scratch site.

**If a site's postings encode a specific term/season in the title** (e.g.
Tesla's `"... (Winter/Spring 2027)"`, `"... (Fall 2026)"`), and you only
want specific upcoming terms rather than everything not-yet-stale: don't
lean on the shared pipeline's generic `term_filter_ok()` alone (it only
knows "stale year" vs "not"), write an adapter-local filter that extracts
the year(s) from the title and matches against the exact target year(s) you
want. See `custom_sites/adapters/tesla_careers.py`'s `_term_ok()` for the
pattern: pull all 4-digit years out of the title, keep if the target year is
present or if no year is present at all (absence of a year isn't evidence of
staleness), drop otherwise.

## Investigation steps (do this before writing any code)

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
   pile of `<script>` tags), the data loads client-side — go to step 2. If
   you get an Akamai/Cloudflare/PerimeterX "Access Denied" block page
   instead of real HTML, see the **bot-protection dead ends** note below
   before sinking more time in.

2. **Use the browser tools to find the real data source.** Load the
   claude-in-chrome tools (`ToolSearch` for `mcp__claude-in-chrome__*` if
   not already loaded), navigate to the jobs page, then check the page's
   own `performance.getEntriesByType('resource')` list (the
   `read_network_requests` tool frequently misses XHR/fetch calls issued
   before it attaches, or blocks entries containing certain cookie/query
   patterns — `javascript_tool` reading `performance` entries directly is
   more reliable) for the real API call. Common patterns:
   - A dedicated `/api/jobs` or GraphQL endpoint returning clean JSON — best
     case, just hit it directly with `requests`.
   - A static JSON blob on a CDN subdomain that the site's own JS fetches
     client-side (e.g. Salesforce's `a.sfdcstatic.com/.../jobs_1.json`) —
     just as good as a dedicated API, and often *doesn't* need any bot
     -protection workaround since it's served from a plain CDN, not the
     app's own protected domain.
   - Embedded JSON in the initial HTML via a framework-specific mechanism:
     `__NEXT_DATA__` (Next.js), `window.__INITIAL_STATE__` (many
     React/Redux apps), `AF_initDataCallback` (Google/Wiz-based sites). If
     you find one of these, re-run the plain `curl` from step 1 to confirm
     the *same* embedded data appears in the server-rendered HTML — it very
     often does, meaning you *still* don't need a headless browser, just a
     regex + `json.loads`.
   - A platform-specific search endpoint (e.g. Phenom People's
     `/widgets` POST endpoint, used by Cisco and many other enterprise
     careers sites) that expects a specific request-body shape mirroring
     the site's facet state. Find the shape by hooking `window.fetch` /
     `XMLHttpRequest.prototype.send` in the page via `javascript_tool`
     *before* triggering the search/filter action, then read back what was
     captured — this reveals the exact body (and any required headers) far
     more reliably than guessing from bundle JS. Example hook:
     ```js
     window.__reqs = [];
     const orig = window.fetch;
     window.fetch = function(...args){
       const p = orig.apply(this, args);
       if (String(args[0]).includes('/your-endpoint')) {
         p.then(r=>r.clone().text()).then(t=>window.__reqs.push({body:(args[1]||{}).body, resp:t}));
       }
       return p;
     };
     ```
     Watch for **multiple different response shapes from the same
     endpoint** if the page fires several widget calls to one shared URL —
     match on the request body's distinguishing field (e.g. a `ddoKey` or
     `widgetId`), not just the URL, to find the one actually carrying job
     results. A response that looks like a failure (e.g.
     `{"tokenAvailable": false}`) may just be a *different, unrelated*
     widget on the page, not evidence the real search call needs auth.
   - If genuinely nothing works without JS execution, that company needs a
     headless-browser adapter (Playwright, run in the GitHub Actions job) —
     meaningfully heavier, flag this to the user before building it.

3. **Find the real intern/entry-level filter AND the real US-location
   filter.** Use the site's own job-search UI filters (checkboxes/dropdowns)
   and watch how the URL, or the XHR request body/params, change when you
   toggle them — that's what to replicate server-side in the adapter. Do
   this for country/location too, not just experience level: some sites'
   raw job-location strings carry no explicit country field at all (e.g.
   Tesla's are bare `"City, Region"` strings), which means you need a
   client-side allowlist (e.g. all 50 US state names + DC) rather than a
   substring match — verify this by dumping the full set of distinct
   location strings for intern-tier postings and checking whether any
   country marker is present anywhere in the payload.

4. **Confirm you can parse it in Python with no JS engine.** Try the exact
   request (`requests.get`/`requests.post` with the discovered
   params/body/headers) in a plain `python3` shell, standalone from any
   browser session/cookies if possible — many of these endpoints turn out to
   be fully stateless (no session cookie or auth token actually required)
   even when the browser's own request happened to include one.

5. **Figure out pagination.** Look for `from`/`start`/`page`/`offset`
   params and a total-count field in the response so you know when to stop.
   Add a `MAX_PAGES` safety cap regardless — never loop unbounded.

6. **Sanity-check for edge-case entries.** Fetch a real page of results and
   check for entries with null/missing URLs, weird aggregate/collection
   listings that aren't real individual postings — skip these at the
   source.

7. **Think about the blocking question honestly.** Is this a public-facing
   page meant for any visitor including logged-out users and search crawlers
   (low risk), or something that looks like an authenticated/internal API
   not meant for external traffic (higher risk, treat cautiously)? A single
   low-frequency GET per poll cycle (every 5-15 min) is a very different
   risk profile than concurrent/high-frequency scraping — but still not zero
   risk. Make sure `fetch_all()`'s try/except means one blocked site fails
   that one adapter's log line, not the whole run.

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
    fresh right before commit.

### Bot-protection dead ends

Some sites sit behind CDN-level bot protection (Cloudflare, Akamai,
PerimeterX) that blocks plain `requests`/`curl` traffic *even for the site's
own JSON API endpoints*, regardless of how browser-like the headers look —
this is usually a TLS-fingerprint or behavioral gate, not a header check, so
spoofing `User-Agent`/`Referer`/`sec-fetch-*` headers won't get past it. If
you hit this: confirm it's really CDN-edge-level (the response is a generic
"Access Denied" page from the CDN vendor itself, not the app), not just a
missing header, then stop and flag it to the user rather than sinking
further time in — don't build a headless-browser adapter to work around it
without asking first, since that's a meaningfully heavier commitment
(Playwright dependency + runtime cost on every poll cycle). See "Companies
eliminated" below for the running list.

## Status

### Adapters built and live (wired into `fetch_all()`)
Google, Amazon, Apple, Microsoft, Salesforce, Cisco, Bloomberg, PayPal, IBM,
Sandisk, 1 Automotive, Cloudera, AT&T, Netflix, Abbott, ABM Industries, AECOM,
Axiado.

- **1 Automotive** (`group1_careers.py`) — AutoFusion-hosted
  (`www.group1careers.com`), server-rendered HTML table, plain `requests`
  works, no auth. Per the user's request, filtered to intern-keyword only
  (no location/category filter). The site's own `keyword=intern` query does
  a substring match (also returns "Internet Salesperson" roles), so narrowed
  further client-side with a word-boundary regex on the title.
- **Cloudera** (`cloudera_careers.py`) — actually Workday-hosted
  (`cloudera.wd5.myworkdayjobs.com/External_Career`), but needs a
  `Business_Area` facet the generic `ats_poller` Workday adapter doesn't
  support, so built here instead (same situation as the open Broadcom
  question below). User asked for Business_Area = Engineering-Team, Info
  Systems/Technology-Team, and Engineering Operations Team, plus
  Country = United States. Workday only lists facet values with at least one
  currently-open posting, and "Engineering Operations Team" has none right
  now, so there's no id to filter on for it — per the user's explicit choice
  ("stick with a broader net"), this **does not apply the Business_Area
  facet at all**, only `locationCountry` = United States (id
  `bc33aa3152ec42d4995f4791a106ed09`), server-side, plus the standard
  word-boundary intern-title filter client-side. Trades some noise from
  unrelated business areas (Sales, Marketing, etc.) for not silently missing
  Engineering Operations Team postings once they open.
- **AT&T** (`att_careers.py`) — TalentBrew-hosted (`www.att.jobs`), server-
  rendered search page whose facet checkboxes fire a stateless GET to
  `/search-jobs/results` (no cookies needed — confirmed via plain `curl`
  after capturing the exact param shape by hooking `fetch`/`XMLHttpRequest`
  in the browser while clicking the real "Technology" category and "United
  States" country checkboxes). Filtered server-side to
  Category = Technology (facet id `36864`) and Country = United States
  (facet id `6252001`), per the user's request. AT&T's own `Keywords=intern`
  search is relevance-based, not literal (returned "Lead Cybersecurity -
  Insider Risk Engineer" for that query), so narrowed further client-side
  with a word-boundary regex on the title.
- **ABM Industries** (`abm_careers.py`) — Oracle Fusion Recruiting Cloud
  (ORC) hosted (`eiqg.fa.us2.oraclecloud.com`, site `CX_1001`). Client-side
  rendered page, but backed by Oracle's standard public
  `recruitingCEJobRequisitions` REST API, no auth needed. Filtered
  server-side to Job Category (`AttributeChar8`) = Engineering, Information
  Technology, and Location facet = United States (id `300000000289738`),
  per the user's request — the user didn't ask for a keyword filter here
  (unlike the other three above), so this only adds the client-side
  word-boundary intern-title regex as a baseline, same as every other
  adapter, since Oracle's own search exposes no employment-type facet.
- **AECOM** (`aecom_careers.py`) — Nuxt app (`aecom.jobs`) whose search page
  calls a shared multi-tenant search API on `prod-search-api.jobsyn.org`
  (the Jobsyn job-board network used by many corporate careers sites).
  Plain `requests` GET works, but the API rejects requests without a custom
  `x-origin: aecom.jobs` header — a normal `Origin` header isn't enough
  ("Mismatched origin" even with `Origin: https://aecom.jobs` set); the
  actual required header name/value was only found by pulling the site's
  embedded `window.__NUXT__.config` blob out of the raw HTML, which has
  `"x-origin":"aecom.jobs"` in its public config. Filtered server-side to
  the user's chosen `location=usa` and `careerarea=digital-engineering-technology`
  facets, matching the user's example URL. No `url` field in the API
  response itself — the job detail URL is reconstructed client-side from
  `city_exact` + `state_short_exact` + `title_slug` + `guid`
  (`https://aecom.jobs/{city}-{state}/{title-slug}/{guid}/job/`), confirmed
  against a real link on the rendered page.
- **Axiado** (`axiado_careers.py`) — SmartRecruiters-hosted, same pattern as
  Sandisk: hits `api.smartrecruiters.com/v1/companies/Axiado/postings`
  directly, no auth needed. No employment-type facet reliably tags "Intern"
  postings, so per the user's explicit "intern keyword" filter request this
  fetches all postings and narrows client-side with a word-boundary regex on
  the title (SmartRecruiters' own `q=` search is full-text, not title-only —
  confirmed `q=intern` surfacing "Head of Legal" and "Vice President,
  Finance"). No location filter requested for this company. Zero intern
  postings open right now, but the fetch/filter mechanism is verified
  working.

- **Netflix** (`netflix_careers.py`) — Eightfold-hosted (same platform as
  PayPal), real API on `explore.jobs.netflix.net/api/apply/v2/jobs`, plain
  stateless GET, no auth. Filtered server-side to the user's chosen Region
  (`ucan`) and Teams (`Engineering`, `Engineering Operations`) facets,
  discovered from the response's own `facets` block. Like PayPal's Eightfold
  instance, `query=intern` substring-matches ("Internal Communications"), so
  narrowed further client-side with a word-boundary regex on the title.
  Pagination is fixed at 10 results per page regardless of the `num` param
  (confirmed by testing `num=200` — still only 10 returned), so paginate via
  `start` in increments of 10.
- **Abbott** (`abbott_careers.py`) — Phenom People-hosted (same platform and
  same `/widgets` POST endpoint as Cisco), plain `requests` works, no auth.
  Filtered server-side to the user's chosen `country` facet
  (`"United States"`, confirmed via the response's `aggregations` block).
  Phenom's `keywords` search here does full-text matching against the whole
  job description, not just the title (confirmed: results included titles
  like "Test Technician I" with no "intern" substring anywhere in the title)
  — even looser than the substring-on-title issue seen elsewhere, so this
  also narrows client-side with a word-boundary regex on the title.

- **IBM** (`ibm_careers.py`) — the search page (`www.ibm.com/careers/search`)
  is a Next.js app whose facet widgets POST real Elasticsearch query DSL to
  `www-api.ibm.com/search/api/v2` (found by hooking `XMLHttpRequest` in the
  browser and toggling a filter checkbox -- the initial page-load request
  fires too early to intercept normally, so a facet click was needed to
  catch a *second* request with the hook already installed). Filtered
  server-side to the user's exact three Career Areas + Internship + United
  States, matching their URL's filters exactly. `careers.ibm.com` itself
  (the underlying Avature-esque job-detail host) is behind an AWS WAF
  bot-challenge, but this search API on `www-api.ibm.com` is not.
- **Sandisk** (`sandisk_careers.py`) — SmartRecruiters-hosted, hits the
  platform's public `api.smartrecruiters.com/v1/companies/Sandisk/postings`
  directly, no auth needed. No employment-type facet reliably tags
  "Intern" postings (found via WebSearch since Sandisk's own domain didn't
  surface it — spun off from Western Digital, uses SmartRecruiters), so per
  the user's choice this is a keyword search (word-boundary `\bintern\b` on
  title) combined with a client-side filter to `location.city == "Milpitas"`
  (the only location the user wants), using the API's own structured
  location field rather than string matching.

Notes on the last two:
- **Bloomberg** (`bloomberg_careers.py`) — Avature-hosted, server-rendered,
  plain `requests` works fine. Filtered server-side to the user's chosen
  Experience Level (Early Careers, Internships) + Business Area (Data,
  Engineering and CTO, Technology Support) facets via URL params.
  Deliberately **not** filtered to US — the user wants Bloomberg results
  worldwide.
- **PayPal** (`paypal_careers.py`) — Eightfold-hosted, hits the site's own
  `/api/pcsx/search` GET endpoint (stateless, no auth needed). No
  employment-type/experience-level facet is exposed for PayPal's instance,
  so per the user's own filter choice this uses `query=intern` — **but
  Eightfold's search does prefix/substring matching, so "intern" also
  matches "Internal"** (real results seen: "Manager, Internal Controls",
  "Sr Auditor, Internal Audit" — not internships). Client-side filters on a
  word-boundary regex (`\bintern(s|ship|ships)?\b`) to drop those false
  positives. Deliberately **not** filtered to US — worldwide, like
  Bloomberg. Worth re-checking if PayPal's postings ever start reliably
  including "Intern" as a distinct word in every real internship title.

### Splunk is already covered — no separate adapter needed
Splunk's careers page (`splunk.com/en_us/careers.html`) redirects straight
to `careers.cisco.com/global/en/splunk`, and Splunk requisitions live in the
same Cisco Workday/Phenom People pool the `cisco_careers.py` adapter already
queries (confirmed: searching that pool for "splunk" returns live Splunk
postings, e.g. "Account Executive - Splunk", with no separate brand/domain
facet to distinguish them — they're just regular Cisco postings). Any
Splunk internship that's Intern-faceted and US-located is already picked up
by the existing Cisco adapter's fetch.

### Built but not wired in
- **Tesla** (`custom_sites/adapters/tesla_careers.py`) — adapter logic/schema
  is correct (verified the request shape and a real response via the
  browser), but confirmed dead: Tesla's `cua-api` endpoint returns 403 to
  plain `requests` both locally *and* from a GitHub Actions runner (tested
  via a throwaway `workflow_dispatch` workflow, same 403 both places) — so
  this is an Akamai bot-protection gate that isn't network/IP-specific, and
  a different Actions runner won't get around it either. Kept in the repo
  unwired in case a future non-`requests`-based approach (headless browser)
  is ever worth the added weight; effectively in the same bucket as Meta/
  Uber/TikTok/ByteDance below.

### Companies eliminated
- **Tesla** — Akamai-blocked (see above; confirmed from GitHub Actions too,
  not just local).
- **Meta, Uber** — Cloudflare-blocked at the CDN edge (same class of issue).
- **TikTok, ByteDance** — same bot-protection dead end.

Bot-protected sites like these don't become scrapable by moving the poller
to a different host/workflow — Cloudflare/Akamai/PerimeterX gate on
TLS-fingerprint and behavioral signals that a plain HTTP client (wherever
it runs) doesn't produce, not on source IP. The only way past this class of
block is a real browser engine (headless Chromium via Playwright) actually
rendering the page, which is a materially heavier adapter (browser binary +
much slower/costlier poll cycle) — worth doing only if the user explicitly
wants to invest in it for a specific company, not as a default fallback.

### Next companies to build
Broadcom was removed from the queue by the user. Cloudera, 1 Automotive,
AT&T, and ABM Industries (this session) plus Netflix and Abbott (built
concurrently in another session) are done -- see "Adapters built and live"
above. No standing queue right now; next picks TBD with the user.

Lower priority / not yet investigated: NASA (govt site, likely low volume /
harder to scrape), Axiado (small startup, low posting volume). **Slack**
(covered by Salesforce adapter) and **Splunk** (covered by Cisco adapter)
are already handled — see notes above, no separate adapter needed for
either.

### Resolved: the Broadcom/Cloudera facet-availability problem
Broadcom and Cloudera (both Workday-hosted) each had a business-area/
job-family facet the user wanted to filter on, where **Workday's facet list
only shows values that currently have at least one open posting** -- a
hardcoded facet-id list captured today could silently miss a real category
once the company starts posting under it later. For Cloudera the user chose
option (c) from the three considered here: skip that facet entirely and use
a broader net (Country=US + intern-keyword only), accepting more noise
rather than risk silently missing postings. Same tradeoff would apply if
Broadcom's IT-facet question is revisited later.

### Planned: headless-browser (Playwright) adapters for Tesla, Uber, Meta
The user intends to build these soon to get past the Cloudflare/Akamai
blocks noted above. Brief pointers for whoever picks this up:
- Add `playwright` to `requirements.txt`, and a `playwright install
  chromium --with-deps` step to whichever GitHub Actions workflow runs
  these adapters (confirmed free to run on Actions since this repo is
  public — unlimited minutes regardless of job duration).
- Adapter shape stays the same (`fetch()` returning the standard entry
  dicts) — internally it launches headless Chromium, navigates to the
  search URL with the right filters already applied via query params (like
  every other adapter here), waits for the results to render, then reads
  the DOM (or, better, re-hooks `page.on("response")` to grab whatever
  underlying JSON/XHR call the rendered page itself makes, same as the
  investigation approach used for Cisco/PayPal above — often still cleaner
  than scraping the DOM even behind a real browser).
- Expect meaningfully slower runs per adapter (browser launch + render, not
  a fast HTTP call) — keep it as its own try/except in `fetch_all()` same as
  every other adapter, so a slow/failing headless run never blocks the rest
  of the poll cycle.

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
