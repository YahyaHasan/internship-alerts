"""
Company registry for the ATS poller.

Each entry maps a company to the ATS platform it posts jobs on, plus
whatever identifier that platform's API needs to look it up. Verified by
hitting the platform's public JSON API directly (see PR/commit history) --
don't add an entry here without confirming the API actually returns data
for that slug/tenant.
"""

GREENHOUSE_COMPANIES = [
    # display_name, board_slug
    ("Coinbase", "coinbase"),
    ("Waymo", "waymo"),
    ("Block", "block"),
    ("Anthropic", "anthropic"),
    ("Cloudflare", "cloudflare"),
    ("Stripe", "stripe"),
    ("Dropbox", "dropbox"),
    ("Duolingo", "duolingo"),
]

LEVER_COMPANIES = [
    # display_name, company_slug
]

WORKDAY_COMPANIES = [
    # display_name, tenant, wd_host (e.g. "wd5"), site
    ("Adobe", "adobe", "wd5", "external_experienced"),
    ("Nvidia", "nvidia", "wd5", "NVIDIAExternalCareerSite"),
]
