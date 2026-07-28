# JOIN company discovery

This repository maintains `websites.json`, the company catalog consumed by `Perinban/WebScrapJobs`.

## Why the old collector was incomplete

The previous implementation depended on Google Custom Search. Each configured query returned at most 100 results, so it could never enumerate JOIN's public company pages. The old workflow also stashed `websites.json` after discovery and never restored the stash, which prevented newly found companies from being committed.

## Discovery strategy

The refresh now preserves the existing catalog and unions companies from four sources:

1. JOIN's recursive jobs sitemap advertised in `robots.txt`;
2. company slugs found in recent Common Crawl records for actual JOIN job URLs;
3. current job URLs already published by `Perinban/WebScrapJobs`; and
4. Google Custom Search as an optional final fallback.

The sitemap is tried first. When JOIN's Cloudflare configuration blocks the XML request, Common Crawl provides independent URL discovery without downloading archived page bodies or bypassing JOIN access controls. Common Crawl company-page-only records are excluded by default; a company is added only when an archived JOIN job URL identifies it.

Company URLs are canonicalized, deduplicated, and sorted. The catalog uses a strictly non-shrinking union:

```text
final catalog = existing catalog + every valid company discovered by any source
```

The restored baseline contains 29,200 unique publicly discovered JOIN company slugs. A company is not deleted merely because it has no current job, disappears from a recent crawl, or a public source temporarily fails. Discovery runs may add valid companies but must never replace the preserved catalog with a smaller source-specific subset.

No public source can guarantee every private or never-indexed JOIN account. The catalog therefore represents publicly discoverable companies accumulated over time; downstream job scraping determines which of those companies currently have active jobs.

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
COMMON_CRAWL_INDEX_URL
JOIN_CDX_URL
API_KEYS
CSE_CONFIG
LOG_LEVEL
```

`API_KEYS` and `CSE_CONFIG` are JSON values and are only needed for the Google CSE fallback. `JOIN_CDX_URL` is an opt-in historical backfill source and is intentionally disabled in the daily workflow. Safety guards reject unexpectedly small or oversized catalogs.
