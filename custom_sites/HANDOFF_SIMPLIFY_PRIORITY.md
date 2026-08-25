# Handoff: highest-priority Simplify companies still needing adapters

Context: this repo used to lean on `poll.py` (root), which polls the
SimplifyJobs community-maintained `listings.json` as a catch-all source. As of
2026-08-24 we migrated 180 companies out of that feed into
`ats_poller/companies.py` (standard ATS platforms: Greenhouse, Lever, Ashby,
SmartRecruiters) once each one was verified against the live API. `poll.py`
now excludes those migrated companies via `MIGRATED_TO_ATS_COMPANIES` so they
don't double-alert.

`poll.py` is being removed. Before/after that happens, the companies below
are the highest-priority ones from Simplify's list that are **not yet
tracked anywhere** in this repo (checked against `ats_poller/companies.py`
and the custom adapters wired into `custom_sites/custom_poll.py`) and did
**not** resolve against Greenhouse/Lever/Ashby/SmartRecruiters/Workable's
public APIs in an automated slug-guessing pass -- meaning each one needs
either a Workday tenant/site lookup (fast, same pattern as the existing
`WORKDAY_COMPANIES` entries in `ats_poller/companies.py`) or a bespoke
`custom_sites/adapters/*.py` adapter (see `custom_sites/PLAYBOOK.md` for the
adapter-building playbook and conventions).

Ranked by current posting volume in Simplify's feed (as of 2026-08-24):

| Postings | Company |
|---|---|
| 55 | Tesla |
| 45 | Royal Bank of Canada |
| 28 | AMD |
| 27 | American Express |
| 23 | Susquehanna International Group (SIG) |
| 20 | The Walt Disney Company |
| 15 | Tencent |
| 15 | John Deere |
| 14 | Citadel |
| 14 | Copart |
| 14 | PricewaterhouseCoopers (PwC) |
| 12 | Vertiv |
| 12 | L3Harris Technologies |
| 12 | Zipline |
| 12 | BNY |
| 11 | Magna |
| 11 | Micron Technology |
| 10 | Citadel Securities |
| 9 | DRW |
| 9 | Crowe |
| 8 | JP Morgan Chase |
| 8 | Castleton Commodities International |
| 7 | Meta |
| 7 | GE Aerospace |
| 7 | Bank of Montreal |
| 7 | Ontario Teachers' Pension Plan |
| 7 | Freddie Mac |
| 6 | Synchrony Financial |
| 6 | Virtu Financial |
| 6 | Capital One |
| 6 | Deloitte |
| 6 | LPL Financial Holdings |

Notes carried over from prior verification work (see git history /
`ats_poller/companies.py` comments for the fuller record):

- **Tesla**: `custom_sites/adapters/tesla_careers.py` groundwork exists but
  is NOT wired into `fetch_all()` -- Tesla's `cua-api` is behind an
  Akamai bot-protection gate that blocks plain `requests` at the CDN edge
  (looks like a TLS-fingerprint gate, not a header check). Needs
  verification from an actual GitHub Actions run before wiring it in --
  Actions' network path/IP may not be blocked the same way.
- **AMD**: confirmed on iCIMS (not Greenhouse/Lever/Ashby/Workday) --
  needs a custom adapter or an iCIMS-specific approach.
- **Meta**: no adapter built yet; Meta's careers site (`metacareers.com`)
  would need its own custom adapter, similar to the Google/Amazon/Apple/
  Microsoft ones already in `custom_sites/adapters/`.
- **American Express, JP Morgan Chase, Capital One, Deloitte, PwC**: large
  enterprises -- worth checking Workday first (same pattern as
  `WORKDAY_COMPANIES`), since several peer companies in that list turned
  out to be Workday-hosted.
- Trading/quant firms (Citadel, Citadel Securities, DRW, Susquehanna (SIG),
  Virtu Financial, Chicago Trading Company (**already added** --
  see `ats_poller/companies.py`, Greenhouse slug `chicagotrading`)) are
  often on Greenhouse under a non-obvious slug (e.g. abbreviations or a
  trading-desk-specific name) -- worth a manual slug search rather than
  assuming Workday/custom.

Not included in this handoff (deliberately out of scope): universities and
national laboratories (Pennsylvania State University, National Laboratory
of the Rockies, University of Texas at Austin, Lawrence Livermore National
Laboratory, University of Virginia, etc.) -- no standard public job-board
API to adapt against, and TikTok/ByteDance/RTX, which are excluded on
purpose via `SIMPLIFY_EXCLUDED_COMPANIES_RE` in `poll.py`.
