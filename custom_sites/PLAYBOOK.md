# Building a new custom-site scraper

This file is the handoff doc for adding a new company to `custom_sites/` —
written so a fresh Claude session (no prior context on this repo) can pick
up one company and build a working adapter end to end.

## Where this fits

`custom_sites/custom_poll.py` is one of two independent pollers in this
repo. (A third, `poll.py` at the repo root, polled the SimplifyJobs
community-maintained aggregator as a catch-all; it was retired on
2026-08-24 once its highest-value companies were migrated into direct
tracking. What was left of that feed is now the build queue under
"Next companies to build" below.)

- `ats_poller/ats_poll.py` — polls companies hosted on a standard ATS
  (Greenhouse, Lever, Ashby, Workday) via each platform's public JSON API.
  Company list lives in `ats_poller/companies.py`. **Do not touch for this
  task.**
- `custom_sites/custom_poll.py` — **this is what you're extending.** For
  companies whose careers site is bespoke (not on a standard ATS), each
  needs its own hand-built adapter.

Both run on independent GitHub Actions workflows
(`.github/workflows/{ats_poll,custom_poll}.yml`), each triggered
externally via `workflow_dispatch` by a cron-job.org job hitting the GitHub
API every 5-15 min (GitHub's own native `schedule:` trigger was found to be
unreliable at sub-hourly intervals — see git history on the old `poll.yml`
if curious). **Adding a new company to `custom_sites` does NOT require a new
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
(Playwright dependency + runtime cost on every poll cycle). See
"Won't build" below for the running list.

## Platform cheat-sheet

Most sites turn out to run one of a handful of hosted platforms. Identify the
platform first — an adapter for a platform already listed here is usually a
copy of the named example with different ids.

| Platform | How to spot it | Endpoint / filter approach | Example adapter |
|---|---|---|---|
| **Eightfold** | `{company}.eightfold.ai`, or the shared `app.eightfold.ai` host | `/api/pcsx/search`; real `filter_seniority` facet — value is `Intern` on most, lowercase `internship` on some. Check the site's own facet list rather than guessing | `qualcomm_careers.py`, `netflix_careers.py`, `hp_careers.py` |
| **Oracle Fusion Recruiting Cloud (ORC)** | `*.fa.<region>.oraclecloud.com`, `siteNumber=CX_1` | Keyword search is **relevance-ranked, not a strict filter** — stop paginating once a page's titles stop matching, don't trust `TotalJobsCount`. Filter US client-side on `PrimaryLocationCountry == "US"` | `oracle_careers.py`, `dell_careers.py`, `ti_careers.py` |
| **TalentBrew / Radancy** (formerly TMP Worldwide) | own domain, `/search-jobs/results` | Returns JSON whose `results` field is an **HTML fragment**, not structured job data — parse with a regex. Filters go in as `FacetFilters[i].{ID,FacetType,Display}` | `att_careers.py`, `intuit_careers.py`, `schwab_careers.py` |
| **Phenom People** | `/widgets` POST endpoint | Request body mirrors the site's facet state; match responses on a distinguishing body field (`ddoKey`/`widgetId`), not the URL — one endpoint serves several unrelated widgets | `cisco_careers.py`, `abbott_careers.py` |
| **iCIMS / Jibe** | tenant name in the careers URL | Often exposes a plain unpaginated JSON GET on the company's *own* domain, no auth | `github_careers.py`, `atlassian_careers.py` |
| **Avature** | server-rendered HTML | Parse the rendered page directly | `bloomberg_careers.py` |
| **SmartRecruiters** | `careers.smartrecruiters.com` | Prefer `ats_poller`'s generic adapter unless the company needs custom filtering | `axiado_careers.py`, `sandisk_careers.py` |
| **SAP SuccessFactors** | `career*.successfactors.com` | **No adapter exists in this repo** — would need building from scratch | none |

Two traps worth knowing:

- **A Phenom/custom frontend over Workday data.** Thermo Fisher, Danaher, and
  Booz Allen all look bespoke but their `applyUrl` fields point at
  `*.myworkdayjobs.com`. Check for this before building anything — the right
  answer is a one-line entry in `ats_poller/companies.py`, not an adapter.
  Cloudera was the same story.
- **Facet lists only show values that currently have an open posting.**
  A facet-id list captured today can silently miss a category the company
  starts posting under later. Where this matters, prefer a broader net
  (Country=US + intern keyword, narrowed by a title regex) over a hardcoded
  facet list, accepting more noise rather than silent misses.

## Status

### Adapters built and live (wired into `fetch_all()`)

Google, Amazon, Apple, Microsoft, Salesforce, Cisco, Bloomberg, PayPal,
Sandisk, 1 Automotive, Cloudera, AT&T, Netflix, Abbott, ABM Industries, AECOM,
Axiado, Oracle, Dell Technologies, Palo Alto Networks, Qualcomm, Texas
Instruments, Applied Materials, Lam Research, Intuit, Boston Scientific,
HP, Honeywell, GitHub, Atlassian, Charles Schwab.

`fetch_all()` in `custom_poll.py` is the authoritative list — check
there, not here, if the two ever disagree.

Several companies that *look* like custom-site candidates are already covered
elsewhere and need no adapter: **Slack** (via the Salesforce adapter),
**Splunk** (via Cisco — Splunk reqs live in Cisco's pool since the
acquisition), **KLA**, **Booz Allen**, **Unity**, **Docker**, **S&P Global**,
**Mastercard**, **Rockwell Automation**, and **Hugging Face** (all on a
standard ATS, in `ats_poller/companies.py`).

### Next companies to build

Not yet investigated for ATS vs. custom-site status — run the Investigation
steps above on each before building. Order is a priority suggestion, not a
commitment; confirm with the user before starting one.

Ranked by posting volume, from the retired SimplifyJobs feed (companies not
tracked anywhere in this repo, and which did not resolve against the
Greenhouse/Lever/Ashby/SmartRecruiters/Workable public APIs in an automated
slug-guessing pass):

| Postings | Company | Known starting point |
|---|---|---|
| 28 | AMD | confirmed on iCIMS — see cheat-sheet |
| 27 | American Express | try Workday first |
| 23 | Susquehanna (SIG) | likely Greenhouse under a non-obvious slug |
| 20 | The Walt Disney Company | |
| 15 | John Deere | |
| 14 | Citadel | likely Greenhouse, non-obvious slug |
| 14 | Copart | |
| 14 | PricewaterhouseCoopers | try Workday first |
| 12 | Vertiv | |
| 12 | Zipline | |
| 12 | BNY | |
| 11 | Magna | |
| 11 | Micron Technology | |
| 10 | Citadel Securities | likely Greenhouse, non-obvious slug |
| 9 | DRW | likely Greenhouse, non-obvious slug |
| 8 | JP Morgan Chase | try Workday first |
| 7 | Freddie Mac | |
| 6 | Synchrony Financial | |
| 6 | Virtu Financial | likely Greenhouse, non-obvious slug |
| 6 | Deloitte | try Workday first |
| 6 | LPL Financial Holdings | |

Trading/quant firms are frequently on Greenhouse under an abbreviation or a
desk-specific name — do a manual slug search before assuming they need a
bespoke adapter (Chicago Trading Company was found this way).

Also unresolved, from an earlier batch — none ruled out, just not yet
cracked:
**Zimmer Biomet** (SuccessFactors, needs a new platform adapter), **Corning**
and **Parker-Hannifin** (plain `curl` 403s, unconfirmed whether real bot
protection), **Emerson Electric** and **Ford** (connections hang under `curl`
— likely slow-drip bot gating), **GE Aerospace** and **GE HealthCare**
(careers URLs not yet located).

Lower priority: **NASA** (government site, low volume), **Axiado** (small
startup, low volume).

### Won't build

**Defense contractors — excluded by user preference.** Northrop Grumman, RTX,
Lockheed Martin, L3Harris, and any others. Booz Allen Hamilton is
defense-adjacent (government/military consulting) — it is already covered via
Workday, but do not add more in this category. Ask before adding anything
that fits this description.

**Dropped by the user:** Capital One, Workday (the company), Broadcom.

**Bot-protected — confirmed dead, do not retry.** Tesla (Akamai), Meta, Uber
(Cloudflare), TikTok, ByteDance, Intuitive Surgical (Cloudflare
JS-challenge). `tesla_careers.py` exists in the repo but is deliberately
**not** wired into `fetch_all()`.

Playwright was actually tested against Tesla and **does not work** — this
rules out the whole headless-browser approach, not just that one attempt.
A real headless Chromium got the identical Akamai `403 Access Denied` both
locally and from a GitHub Actions runner, on the plain `tesla.com` homepage,
in ~1.5s — too fast for a JS challenge or behavioral check to have run.
Stealth measures (hiding `navigator.webdriver`, spoofing
`navigator.plugins`/`languages`, `--disable-blink-features=Automation
Controlled`) made no difference. That timing means it is **edge-level IP/ASN
reputation blocking, not a TLS-fingerprint gate** — a real browser looks
identical at the fingerprint layer, so if fingerprinting were the mechanism
Chromium would have gotten through where `requests` failed. No browser
automation will get past this from either environment. The only way around it
is routing through residential IPs, which is a materially different and more
questionable approach — not pursued.

Out of scope generally: universities and national laboratories (no standard
public job-board API to adapt against).

## Infra notes

Both pollers commit and push their `seen_*.json` state file to `main` at the
end of every run. Because they run every 5-15 min on independent schedules,
two runs can race and the second `git push` gets rejected as non-fast-forward.
Both workflows now wrap that push in a fetch+rebase+retry loop, so this
self-heals; a run whose state update is dropped anyway will re-evaluate (and
possibly re-notify) what it found on the next run.

If duplicate Telegram messages ever reappear, check for a **paginated source
replaying the same page**, not the push race — that was the cause of an
earlier duplicate-alert bug (a Workday tenant that ignored `offset` past the
end of its result set). Both pollers now dedupe by `id` within a single run,
and adapters should never emit the same `id` twice from one `fetch()`.
