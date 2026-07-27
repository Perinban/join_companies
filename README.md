# JOIN company discovery

This repository maintains `websites.json`, the company catalog consumed by `Perinban/WebScrapJobs`.

## Why the old collector was incomplete

The previous implementation depended on Google Custom Search. Each configured query returned at most 100 results, so it could never enumerate JOIN's public company pages. The old workflow also stashed `websites.json` after discovery and never restored the stash, which prevented newly found companies from being committed.

## Discovery strategy

The refresh now preserves the existing catalog and unions companies from four sources:

1. JOIN's recursive jobs sitemap advertised in `robots.txt`;
2. recent Common Crawl URL indexes for the public `join.com/companies/` prefix;
3. current job URLs already published by `Perinban/WebScrapJobs`; and
4. Google Custom Search as an optional final fallback.

The sitemap is tried first. When JOIN's Cloudflare configuration blocks the XML request, Common Crawl provides independent URL discovery without downloading archived page bodies or bypassing JOIN access controls.

Company URLs are canonicalized, deduplicated, and sorted. Existing valid slugs are never removed automatically, so a temporary source outage cannot shrink the catalog.

No public source can guarantee every private or never-indexed JOIN account. This approach removes the Google 100-result ceiling and continuously accumulates newly indexed public companies.

## Schedule

The company refresh runs daily at `20:17 UTC`, several hours before the nominal `00:01 UTC` WebScrapJobs schedule.

The daily workflow unions the four newest Common Crawl collections. A manual backfill can use a larger window by setting the workflow input or environment variable:

```bash
CC_CRAWL_COUNT=12 python script.py
```

## Run locally

```bash
python -m pip install -r requirements.txt
python -m pip check
python -m unittest discover -s tests -v
CC_CRAWL_COUNT=4 python script.py
```

Optional environment variables:

```text
JOIN_SITEMAP_INDEX
JOB_URL_SOURCE
OUTPUT_FILE
CC_CRAWL_COUNT
DISCOVERY_TIMEOUT_SECONDS
MIN_COMPANY_COUNT
MAX_COMPANY_COUNT
API_KEYS
CSE_CONFIG
LOG_LEVEL
```

`API_KEYS` and `CSE_CONFIG` are JSON values and are only needed for the Google CSE fallback. Safety guards reject unexpectedly small or oversized catalogs.
