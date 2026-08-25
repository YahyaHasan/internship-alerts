# Building a new custom-site scraper

This file is the handoff doc for adding a new company to `custom_sites/` —
written so a fresh Claude session (no prior context on this repo) can pick
up one company and build a working adapter end to end.

## Where this fits

`custom_sites/custom_poll.py` is one of two independent pollers in this
repo (a third, `poll.py` at the repo root, polled the SimplifyJobs
community-maintained aggregator as a catch-all; it was retired on
2026-08-24 once its highest-value companies were migrated into direct
tracking — see `custom_sites/HANDOFF_SIMPLIFY_PRIORITY.md` for the
highest-priority companies from that feed still needing an adapter here or
a Workday tenant lookup):

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
(Playwright dependency + runtime cost on every poll cycle). See "Companies
eliminated" below for the running list.

## Status

### Adapters built and live (wired into `fetch_all()`)
Google, Amazon, Apple, Microsoft, Salesforce, Cisco, Bloomberg, PayPal,
Sandisk, 1 Automotive, Cloudera, AT&T, Netflix, Abbott, ABM Industries, AECOM,
Axiado, Oracle, Dell Technologies, Palo Alto Networks, Qualcomm, Texas
Instruments, Applied Materials, Lam Research, Intuit, Boston Scientific,
HP, Honeywell, GitHub, Atlassian.

- **Atlassian** (`atlassian_careers.py`) — also iCIMS-hosted (tenant
  `globalcareers-atlassian`), but its own domain exposes a plain JSON GET
  endpoint (`www.atlassian.com/endpoint/careers/listings`) returning every
  open posting unpaginated, no auth. The user's example URL used
  `team=Interns`, but the API's own `category` field (the closest
  equivalent) has no "Interns" value right now -- zero open internship
  postings currently, nothing live to confirm a stable value against.
  Falls back to a client-side word-boundary "intern" title regex + a
  substring check for "United States" in the (free-text) locations field.
  0 US postings right now, matches the empty category.

Unity (Workday: `unitytech`/`wd1`/`Unity`) and Docker (Ashby: `docker`)
were both confirmed working via the generic adapters and added directly
to `ats_poller/companies.py` -- no bespoke adapter needed for either.

- **GitHub** (`github_careers.py`) — runs on iCIMS's "Jibe" platform
  (tenant `githubinc`), but calls a plain JSON GET on GitHub's own domain
  (`www.github.careers/api/jobs`), no auth needed. The user's example
  URL's `/early-in-profession` path turned out to apply no server-side
  filter of its own (confirmed: identical `totalCount` with/without it),
  and the site's own Career Level filter (Director / Individual
  Contributor / Senior Director / Senior Manager, at time of check) has no
  "Intern" value at all -- zero internship postings open there right now,
  so there's nothing live to confirm a stable facet id against. Falls back
  to `keywords=intern` server-side + a client-side word-boundary title
  regex + US country-code check, the "narrow further, don't trust keyword
  alone" pattern used elsewhere. Currently 0 US postings, matches the live
  site's own "0 Results" state.

Hugging Face is **not** a `custom_sites` adapter -- it's on Workable, a
platform this repo had zero adapter support for, so a new general-purpose
`ats_poller/adapters/workable.py` was built (same shape as
`ashby.py`/`greenhouse.py`: one unpaginated public JSON endpoint per
company, `apply.workable.com/api/v1/widget/accounts/{account}`, no auth).
Added to `ats_poller/companies.py`'s new `WORKABLE_COMPANIES` list as
`("Hugging Face", "huggingface")`. 7 jobs fetched (none currently
Intern-titled -- Hugging Face is a small company with few open roles right
now -- but the shared `keyword_filter` in `ats_poll.py` will pick up any
Intern-titled posting the moment one opens, same as every other
ats_poller-hosted company). This adapter is now reusable for any other
Workable-hosted company found in the future, not just Hugging Face.

- **HP** (`hp_careers.py`) — Eightfold-hosted (`apply.hp.com`), but on a
  shared generic host (`app.eightfold.ai`) rather than a company-specific
  subdomain -- the earlier-guessed `hp.eightfold.ai` / `hp-sandbox.eightfold.ai`
  hosts don't serve the real search API; `app.eightfold.ai` does, found via
  the site's own embedded links. Matches the user's example URL: a real
  `filter_seniority=internship` facet (lowercase, unlike Qualcomm/Applied
  Materials' "Intern"), confirmed accurate -- all 33 global results genuine
  internships, no false positives. Filtered to Seniority=internship +
  Location=United States. 6 US postings live right now.
- **Honeywell** (`honeywell_careers.py`) — Oracle Fusion Recruiting Cloud
  (ORC), own host `ibqbjb.fa.ocs.oraclecloud.com` (a `.ocs.` region,
  unlike the `.us2.` hosts seen elsewhere), siteNumber `CX_1`. The user's
  example URL's keyword `"Intern (Bachelor's)"` looked promising (unlike
  Oracle's own keyword param, which does nothing) -- page 1 genuinely
  returns Intern-titled postings -- but this ORC instance's keyword search
  turned out to be relevance-ranked, not a strict filter: by page 2+,
  relevance degrades and non-intern titles ("Sr Account Manager", "Future
  Finance Leaders") flood in, same degradation behavior already documented
  for Workday's `searchText` in `ats_poller/adapters/workday.py`. Fixed the
  same way: stop paginating once a page's postings no longer mention
  "intern" in the title, rather than trusting `TotalJobsCount`. Filtered
  client-side to `PrimaryLocationCountry == "US"` (same approach as
  Dell/TI). Only 6 intern-titled postings exist globally right now (India
  x4, Puerto Rico, Brazil) and none are US, so this currently returns 0 --
  confirmed via a standalone script that walks the full paginated result
  set, not just an assumption from the adapter's own output. Mechanism is
  verified correct.

### "Next 20" queue (2026-08-22) -- results
Investigated all 20. Booz Allen Hamilton turned out to **already be fully
covered** -- it's on Workday (`bah`/`wd1`/`BAH_Jobs`, already in
`ats_poller/companies.py` from a prior session's Workday sweep), and the
custom in-house portal at `careers.boozallen.com` I was stuck on
yesterday is just a frontend over the same Workday data. Confirmed via a
live `workday.fetch()` run -- 40 jobs, including the same
"AI RAN Telecommunications Engineer Intern" seen in the browser
yesterday. No adapter needed; the Booz Allen investigation from
2026-08-22 (broken Country filter, no findable API) is now moot.

Of the 20:
- **Boston Scientific** -- Eightfold-hosted (`bostonscientific.eightfold.ai`),
  same `filter_seniority=Intern` facet pattern as Qualcomm/Applied
  Materials. Built as `bostonscientific_careers.py`. 1 US posting live
  (an apprenticeship).
- **Xylem, Otis Worldwide, GE Vernova, Stryker, IQVIA, Thermo Fisher
  Scientific, Danaher, Illinois Tool Works** -- all turned out to be
  Workday-hosted (found via each company's own careers page HTML, then
  each verified with a live `workday.fetch()` run before adding). Added
  directly to `ats_poller/companies.py`, no bespoke adapter needed for any
  of them. Thermo Fisher and Danaher's own careers sites actually run a
  Phenom People frontend (`jobs.thermofisher.com/widgets`,
  `jobs.danaher.com/widgets`), but the job data underneath is Workday
  (`applyUrl` fields point at `*.myworkdayjobs.com`) and the generic
  Workday adapter's plain keyword search works fine against them directly
  -- no need to reverse-engineer the Phenom widget facets for these two,
  unlike Cisco/Abbott where Phenom actually is the primary data source.
- **Zimmer Biomet** -- also Phenom-fronted (`careers.zimmerbiomet.com`),
  but its `applyUrl` points at `career8.successfactors.com`
  (SAP SuccessFactors), a platform this repo has no adapter for at all
  (neither `ats_poller` nor `custom_sites`). Not built -- would need a new
  SuccessFactors adapter from scratch, not just a companies.py entry.
- **HP Inc.** -- Eightfold-hosted, but on an oddly-named host
  (`hp-sandbox.eightfold.ai`, found in the site's own `/careers` links);
  the standard `/api/pcsx/search` path 404s there, so this instance's real
  endpoint shape hasn't been found yet. Not built.
- **Corning, Parker-Hannifin** -- plain `curl` gets 403 on both; not yet
  confirmed whether that's simple header/UA sensitivity or real bot
  protection (didn't get to a browser check). Not built.
- **Emerson Electric, Ford Motor** -- connections hang/time out under
  plain `curl` even with a full browser `User-Agent` (Ford: TLS handshake
  succeeds, then the server never responds -- classic slow-drip bot
  gating). Earlier session notes already flagged both as Oracle Fusion
  Recruiting Cloud-hosted; not re-confirmed this session, not built.
- **Honeywell** -- confirmed Oracle Fusion-hosted (`oraclecloud` hit in
  the page HTML) but the URL tried landed on a 404 error page rather than
  the real search page; the working search URL/site number wasn't found
  this session. Not built.
- **GE Aerospace** -- "General Electric" as a single company no longer
  exists (split in 2024 into GE Aerospace / GE Vernova / GE HealthCare);
  `ge.com/careers` now redirects to `geaerospace.com/company/careers`, a
  static page with no platform hint found in this session's pass. Not
  built. (GE Vernova is done -- see above. GE HealthCare below.)
- **GE HealthCare Technologies** -- both careers URLs tried 404'd; the
  real careers URL wasn't found this session. Not built.

Remaining unbuilt from this batch (Zimmer Biomet, HP Inc., Corning,
Parker-Hannifin, Emerson Electric, Ford Motor, Honeywell, GE Aerospace, GE
HealthCare) are candidates for a future session with more investigation
budget -- none are ruled out/dead ends, just not yet resolved.

- **Intuit** (`intuit_careers.py`) — TalentBrew-hosted, same platform as
  AT&T/Palo Alto Networks, org id `27595`. Matches the intent of the user's
  example URL: its "acm" param names three custom Job Category facet ids
  for Intuit's student programs (`9205024`, `9205760`, `9205744`) --
  confirmed via the site's own `/search-jobs/results` endpoint that passing
  all three as `FacetFilters` (Category type) reproduces the same result.
  Only `9205760` ("New College Grad") currently has any open postings --
  the other two (presumably an Internship and a PhD/Grad program facet)
  have zero right now and don't even appear in the site's own facet list
  (same "only shows facet values with an open posting" situation as
  Cloudera/Broadcom above), but all three ids are kept in the filter so
  postings under them are picked up the moment they open. Combined with
  Country = United States. 1 posting currently live.

S&P Global is **not** a `custom_sites` adapter — it's Workday-hosted
(`spgi.wd5.myworkdayjobs.com/SPGI_Careers`) and wasn't yet in
`ats_poller/companies.py`; added
`("S&P Global", "spgi", "wd5", "SPGI_Careers")` there instead of building a
bespoke adapter. Verification hit a **platform-wide Workday outage**
(`community.workday.com/maintenance-page` redirect) at the time this was
checked — confirmed not tenant-specific by testing an already-working
company (KLA) on a different pod (wd1) and getting the identical redirect,
so this is Workday's own infrastructure being down, not a config issue.
Should resolve on its own; the config is correct as entered.

Mastercard is **not** a `custom_sites` adapter — it's already
Workday-hosted and was in `ats_poller/companies.py` all along
(`("Mastercard", "mastercard", "wd1", "CorporateCareers")`), just under a
different site than the user's example URL (early-careers page maps to a
separate `Campus` site on the same tenant). Added
`("Mastercard Campus", "mastercard", "wd1", "Campus")` alongside it rather
than building a bespoke adapter here. Both currently 303-redirect to
`community.workday.com/maintenance-page` — confirmed this is a real
Workday-side outage on this tenant's pod (not a config bug: the existing
`CorporateCareers` entry, unchanged, hits the identical redirect), so
nothing to fix; should resolve on its own.

Rockwell Automation is **not** a `custom_sites` adapter either — Workday-
hosted (`rockwellautomation.wd1.myworkdayjobs.com/en-US/External_Rockwell_Automation`,
tenant/site confirmed by resolving the redirect from
`rockwellautomation.com/en-us/company/careers.html`). Added
`("Rockwell Automation", "rockwellautomation", "wd1", "External_Rockwell_Automation")`
to `ats_poller/companies.py`. Same platform-wide Workday outage as above at
verification time (identical `community.workday.com/maintenance-page`
redirect) — config believed correct but **not actually fetch-tested end to
end** (no successful `workday.fetch()` run happened for this one, unlike
Mastercard/S&P Global which at least had their tenant/site pairs implied by
the user's own URLs — this one was found via a web search + redirect
trace, so slightly less certain).

### Workday outage resolved, all four verified (2026-08-22)
Workday's platform-wide outage cleared. Re-ran `workday.fetch()` directly
against all four entries added during the outage and all returned real
data (no more `maintenance-page` redirects):
- Mastercard (`CorporateCareers`): 20 jobs fetched, e.g. "Lead Software
  Engineer".
- Mastercard Campus: 1184 jobs fetched (this is the much larger
  early-careers/graduate-program tenant).
- S&P Global: 40 jobs fetched, e.g. "Agribusiness Intern (Early Careers)".
- Rockwell Automation: 40 jobs fetched, e.g. "Embedded Software, Intern"
  (Katowice, Poland) — confirms the previously lower-confidence
  redirect-inferred tenant/site was correct.

All four are confirmed working end to end now; no further action needed
on them.

Also paused: Booz Allen (`careers.boozallen.com`) — investigated at length
but not built. Confirmed its Country filter is broken even through the real
UI (typing "United States" returns zero results despite the unfiltered
list already being ~100% US postings), its keyword param is `search` (not
`keywords`) and does fuzzy matching same as everywhere else, and no real
JSON API could be found behind it despite extensive digging (custom
in-house "portalpacks" React app, not Avature despite one stray HTML
reference) — job data only ever showed up in the live-rendered DOM, never
through plain `requests`. Building this would mean a headless-browser
(Playwright) adapter, which per the user's standing instruction on Tesla
needs to be flagged and confirmed before building, not attempted silently.
Revisit together with the user once Workday work resumes.

The "next 20 companies" list (Emerson Electric, Ford, Honeywell, GE,
GE Vernova, GE HealthCare, HP Inc., HPE, Otis Worldwide, Parker-Hannifin,
Stryker, Boston Scientific, IQVIA, Thermo Fisher Scientific, Corning,
Wabtec, Xylem, Zimmer Biomet, Danaher, Illinois Tool Works) proposed
2026-08-22 is also on hold until this session resumes.

- **Qualcomm** (`qualcomm_careers.py`) — Eightfold-hosted, same platform as
  PayPal/Netflix, but the real API lives on `qualcomm.eightfold.ai` (the
  site's own `careers.qualcomm.com/api/apply/v2/jobs` 403s with "Not
  authorized for PCSX"). Exposes a real `filter_seniority=Intern` facet
  (matching the user's example URL) that's accurate server-side — confirmed
  53 global results, every title a genuine internship, no
  "Internal Auditor"-style false positives — so unlike PayPal/Netflix this
  needs no keyword search or client-side regex at all. Filtered to
  Seniority=Intern + Location=United States. 0 US postings open right now
  (confirmed matches the live site's own "0 jobs" state), but the mechanism
  is verified correct.
- **Texas Instruments** (`ti_careers.py`) — Oracle Fusion Recruiting Cloud
  (ORC), own host (`edbz.fa.us2.oraclecloud.com`), siteNumber `CX`. The
  user's example URL was a static landing page, not a search page, so this
  uses the real search endpoint instead. Filtered server-side to a genuine
  Experience Level flex facet, value "Interns"
  (`AttributeChar8|Interns`) — confirmed via the live UI, no false
  positives in the results. No verified numeric "United States"
  locations-facet id (TI's Work Locations facet only exposes city-level
  values), so filters client-side on `PrimaryLocationCountry == "US"`, same
  approach as Dell. Currently 7 real US intern postings live.
- **Applied Materials** (`appliedmaterials_careers.py`) — Eightfold-hosted,
  same platform/pattern as Qualcomm above (real API on
  `appliedmaterials.eightfold.ai`, the site's own domain 403s the same
  way). Matches the user's example URL exactly: a real
  `filter_seniority=Intern` facet, confirmed accurate (10 global results,
  all genuine internship/early-career titles, no false positives).
  Filtered to Seniority=Intern + Location=United States — 5 real US
  postings live right now.
- **Lam Research** (`lamresearch_careers.py`) — Eightfold-hosted, same
  platform (`lamresearch.eightfold.ai`). Matches the user's example URL's
  two facets exactly: `filter_paygrade=intern/apprentice` (confirmed: 8
  global results, all genuine Intern titles) and
  `filter_rmk_country=united states` — combined, returns exactly the one
  posting matching the user's own example URL's job id, confirming both
  facet names/values. 1 US posting live right now.

- **Oracle** (`oracle_careers.py`) — Oracle Fusion Recruiting Cloud (ORC),
  same platform as ABM but a different site instance
  (`eeho.fa.us2.oraclecloud.com`, siteNumber `CX_45001`). The user's example
  URL's category facet + `keyword=Intern` turned out not to actually filter
  to internships at all (verified live: identical `TotalJobsCount` with/
  without the keyword param, and the category id resolved to "Technology
  Operations" — all Director/Manager titles, no Intern facet exists under
  Experience Level for the US). The real signal is a separate Job Type flex
  facet, value "Student/Intern" (`AttributeChar4|Student/Intern`), confirmed
  via the live UI filter — count matched the API response exactly. Filtered
  server-side to that facet + Location = United States
  (`300000000149325`). Currently returns Oracle's Veteran Internship
  Program (OVIP) postings, including some real SWE roles (e.g. "OCI
  Software Engineer Intern - OVIP") — the general (non-veteran) internship
  cycle wasn't open on the day this was verified, but the facet mechanism
  is confirmed correct.
- **Dell Technologies** (`dell_careers.py`) — Oracle Fusion Recruiting Cloud
  (ORC) hosted directly on Dell's own domain
  (`enterpriseplatform.dell.com`), siteNumber `CX_1001`. The user's example
  URL's `selectedTitlesFacet=INTERNS` job-function facet is accurate
  (confirmed: UI count matched the API response exactly, no false
  positives) — unlike `keyword=intern` on this same instance, which is
  fuzzy/relevance-based and returns junk like "Legal Director, Regulatory
  and Trade Compliance". No verified numeric US locations-facet id (the
  Interns facet currently returns zero US postings — all international —
  so there was nothing live to confirm an id against); filters client-side
  on the response's own `PrimaryLocationCountry` field instead (a real
  structured country code), which needs no unverified guess. Zero US intern
  postings open right now (off-season), but the fetch/filter mechanism is
  verified working, same situation as Axiado.
- **Palo Alto Networks** (`paloaltonetworks_careers.py`) — TalentBrew-hosted
  (`jobs.paloaltonetworks.com`), same platform as AT&T, org id `47263`.
  Unlike AT&T, this instance exposes a real Category facet literally named
  "Intern" (id `9246672`) rather than needing a keyword-search-plus-regex
  workaround — confirmed via the response's own filter section. Filtered
  server-side to Category = Intern + Country = United States (`6252001`,
  a GeoNames id — confirmed portable across TalentBrew tenants since this
  instance's other country facet ids, e.g. Canada `6251999`, also match
  their real GeoNames ids). Only 1-2 intern postings open globally right
  now, none in the US, but the fetch/filter mechanism is verified working.

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
above.

Queue (2026-08-21), ranked by fit with the user's stated interests
(SWE/backend/AI-ML/systems/distributed systems/robotics), sourced from a
~470-company Fortune-500-scale candidate list the user provided (large
caps not on a standard ATS -- same pool 1 Automotive/AT&T/Abbott/
ABM/AECOM/Netflix were pulled from). Excludes Broadcom (removed from queue
above) and Meta (Cloudflare-blocked, see "Companies eliminated"):

1. ~~Oracle~~ — done, see "Adapters built and live" above
2. ~~Intuit~~ — done, see "Adapters built and live" above
3. ~~Dell Technologies~~ — done, see "Adapters built and live" above
4. ~~Palo Alto Networks~~ — done, see "Adapters built and live" above
5. ~~Qualcomm~~ — done, see "Adapters built and live" above
6. ~~Texas Instruments~~ — done, see "Adapters built and live" above
7. ~~Capital One~~ — dropped per user (2026-08-22)
8. ~~Mastercard~~ — done, already Workday-hosted, added to
   `ats_poller/companies.py` instead of here (see note above)
9. ~~S&P Global~~ — done, already Workday-hosted, added to
   `ats_poller/companies.py` instead of here (see note above)
10. ~~Workday~~ (the company) — dropped per user (2026-08-22)
11. ~~Applied Materials~~ — done, see "Adapters built and live" above
12. ~~Lam Research~~ — done, see "Adapters built and live" above
13. ~~KLA~~ — already covered, was already in `ats_poller/companies.py`
    (Workday-hosted)
14. Intuitive Surgical (surgical robotics) — **blocked**, see note below
15. Rockwell Automation (industrial automation/robotics)
16. ~~Northrop Grumman~~ — dropped per user, defense contractor
    (2026-08-22)
17. ~~Lockheed Martin~~ — dropped per user, defense contractor
    (2026-08-22)
18. ~~RTX~~ — dropped per user, defense contractor (2026-08-22)
19. ~~L3Harris Technologies~~ — dropped per user, defense contractor
    (2026-08-22)
20. Booz Allen Hamilton — borderline defense-adjacent (government/military
    consulting); confirm with user before building, given the above drops

The user asked (2026-08-22) to drop Capital One, Workday, and all defense
contractors (Northrop Grumman, RTX, Lockheed Martin, L3Harris, and any
others) from this queue going forward.

### Intuitive Surgical — blocked (Cloudflare, real browser required)
`careers.intuitive.com` is Cloudflare-protected: plain `requests`/`curl`
gets a "Just a moment..." JS-challenge page (403) even with a full browser
`User-Agent`/`Accept`/`Accept-Language` header set, while a real Claude-in-
Chrome browser session loads the page fine. All of the page's own requests
stay on `careers.intuitive.com` itself (no visible third-party ATS
domain/API to hit directly), so this would need a headless-browser adapter
(Playwright) to scrape, same class of problem as Tesla -- which the
Playbook's "Tested and ruled out" section already found doesn't reliably
get past this kind of CDN-edge protection even from GitHub Actions. Per
that precedent, flagging this to the user rather than building a headless
adapter without asking first. Also currently 0 US intern postings live
(confirmed via the real browser session), so even if unblocked there'd be
nothing to alert on right now.

Not yet investigated for ATS vs. custom-site status -- check each with the
Investigation steps above before building. Order is a priority suggestion,
not a commitment; confirm with the user before starting each one.

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

### Tested and ruled out: headless-browser (Playwright) for Tesla -- doesn't work
The user asked to actually test whether Playwright gets past Tesla's block
before committing to building Uber/Meta versions too. Result: **it doesn't**,
and the failure mode rules out the whole approach, not just this one attempt.

What was tested: a real headless Chromium (via Playwright, not `requests`)
hitting `tesla.com`, first locally, then confirmed via a real GitHub Actions
`workflow_dispatch` run (the actual runner this repo's pollers use). Both
got the identical Akamai edge `403 Access Denied` (`errors.edgesuite.net`),
and critically **on the plain tesla.com homepage itself**, not just the
careers API -- in ~1.5 seconds, too fast for a JS challenge or behavioral
fingerprint check to have even run. Stealth measures (hiding
`navigator.webdriver`, spoofing `navigator.plugins`/`languages`,
`--disable-blink-features=AutomationControlled`) made no difference.

Conclusion: this is edge-level IP/ASN-reputation blocking, not a
browser/TLS-fingerprint gate. A real Chromium engine looks identical to any
consumer browser at the TLS/fingerprint layer, so if that were the
mechanism, a real headless Chromium should have gotten through where plain
`requests` failed -- it didn't, on either this sandbox's IP or GitHub
Actions' IP pool. That means **no browser automation approach (Playwright
or otherwise) will get past this from either environment** -- the block
happens before the browser's own behavior is ever evaluated. The only way
around it would be routing traffic through a residential/non-datacenter IP,
which is a materially different (and more questionable) approach, not a
"heavier adapter" -- not pursued.

Given Uber and Meta are documented above as "same class of issue"
(Cloudflare-edge-blocked, not just Akamai), this result means the
Uber/Meta Playwright builds are very likely dead ends too for the same
reason, though each would need its own confirmation run rather than
assuming from Tesla's result alone. Not built.

## Known infra issue to be aware of

Both pollers (`ats_poll.py`, `custom_poll.py`) commit and
push their `seen_*.json` state file directly to `main` at the end of every
run. Because both run frequently (every 5-15 min) on independent
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
