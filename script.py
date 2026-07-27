from __future__ import annotations

import gzip
import itertools
import json
import logging
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)
JOIN_HOSTS = {"join.com", "www.join.com"}
COMPANY_PATH = re.compile(r"^/companies/([^/?#]+)/?(?:.*)?$")
COMPANY_SLUG = re.compile(r"[a-z0-9][a-z0-9-]+")
RESERVED_COMPANY_SLUGS = {"apply", "jobs", "sitemap-jobs-index.xml"}
DEFAULT_SITEMAP_INDEX = "https://join.com/companies/sitemap-jobs-index.xml"
DEFAULT_JOB_URL_SOURCE = "https://raw.githubusercontent.com/Perinban/WebScrapJobs/main/job_post_url.txt"
COMMON_CRAWL_COLLECTIONS_URL = "https://index.commoncrawl.org/collinfo.json"
DEFAULT_CDX_URL = (
    "https://web.archive.org/cdx/search/cdx?"
    "url=join.com/companies/*&output=json&fl=original&filter=statuscode:200&collapse=urlkey"
)
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
USER_AGENT = "TalentBlissCompanyDiscovery/2.0 (+https://github.com/Perinban/join_companies)"


def normalize_company_slug(value: str) -> str | None:
    slug = value.strip().strip("/").lower()
    if not slug or slug in RESERVED_COMPANY_SLUGS:
        return None
    if not COMPANY_SLUG.fullmatch(slug):
        return None
    return slug


def extract_company_slug(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except (AttributeError, ValueError):
        return None
    if parsed.hostname and parsed.hostname.lower() not in JOIN_HOSTS:
        return None
    match = COMPANY_PATH.match(parsed.path)
    return normalize_company_slug(match.group(1)) if match else None


company_slug_from_url = extract_company_slug


def canonical_entry(slug: str) -> dict[str, str]:
    normalized = normalize_company_slug(slug)
    if not normalized:
        raise ValueError(f"Invalid JOIN company slug: {slug!r}")
    return {"link": f"https://join.com/companies/{normalized}", "company_name": normalized}


company_entry = canonical_entry


def build_session() -> requests.Session:
    retries = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def normalized_entries(values: Iterable[str | dict[str, str]]) -> list[dict[str, str]]:
    slugs: set[str] = set()
    for value in values:
        if isinstance(value, str):
            slug = normalize_company_slug(value) or extract_company_slug(value)
        else:
            slug = normalize_company_slug(str(value.get("company_name", ""))) or extract_company_slug(
                str(value.get("link", ""))
            )
        if slug:
            slugs.add(slug)
    return [canonical_entry(slug) for slug in sorted(slugs)]


def request_bytes(session: requests.Session, url: str, timeout: int = 30) -> bytes:
    response = session.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,text/plain,*/*"},
    )
    response.raise_for_status()
    payload = response.content
    if url.lower().endswith(".gz") or response.headers.get("content-encoding", "").lower() == "gzip":
        try:
            payload = gzip.decompress(payload)
        except OSError:
            pass
    return payload


def xml_locations(payload: bytes) -> tuple[str, list[str]]:
    root = ElementTree.fromstring(payload)
    root_name = root.tag.rsplit("}", 1)[-1]
    locations = [
        element.text.strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text
    ]
    return root_name, locations


def discover_from_sitemaps(
    session: requests.Session,
    index_url: str = DEFAULT_SITEMAP_INDEX,
    *,
    max_sitemaps: int = 10_000,
) -> set[str]:
    pending = [index_url]
    visited: set[str] = set()
    companies: set[str] = set()
    while pending:
        url = pending.pop()
        if url in visited:
            continue
        if len(visited) >= max_sitemaps:
            raise RuntimeError(f"Sitemap traversal exceeded {max_sitemaps} documents")
        visited.add(url)
        root_name, locations = xml_locations(request_bytes(session, url))
        if root_name == "sitemapindex":
            pending.extend(location for location in locations if location not in visited)
            continue
        companies.update(slug for location in locations if (slug := extract_company_slug(location)))
    LOGGER.info("Sitemap discovery found %d companies across %d documents", len(companies), len(visited))
    return companies


def discover_from_job_url_source(
    session: requests.Session,
    source_url: str = DEFAULT_JOB_URL_SOURCE,
) -> set[str]:
    response = session.get(source_url, timeout=30, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    companies = {slug for line in response.text.splitlines() if (slug := extract_company_slug(line))}
    LOGGER.info("Current job URL source contains %d companies", len(companies))
    return companies


def parse_cdx_records(payload: str) -> set[str]:
    payload = payload.strip()
    if not payload:
        return set()
    records: list[object]
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            if parsed and isinstance(parsed[0], list) and parsed[0] and parsed[0][0] in {"original", "url"}:
                records = [{parsed[0][0]: row[0]} for row in parsed[1:] if row]
            else:
                records = parsed
        else:
            records = [parsed]
    except json.JSONDecodeError:
        records = []
        for line in payload.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    companies: set[str] = set()
    for record in records:
        if isinstance(record, dict):
            url = record.get("url") or record.get("original")
        elif isinstance(record, list) and record:
            url = record[0]
        else:
            url = None
        if isinstance(url, str) and (slug := extract_company_slug(url)):
            companies.add(slug)
    return companies


parse_common_crawl_records = parse_cdx_records


def discover_from_cdx(session: requests.Session, cdx_url: str = DEFAULT_CDX_URL) -> set[str]:
    response = session.get(cdx_url, timeout=90, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    companies = parse_cdx_records(response.text)
    LOGGER.info("Internet Archive CDX found %d companies", len(companies))
    return companies


def common_crawl_params(*, page: int | None = None, show_num_pages: bool = False) -> list[tuple[str, str]]:
    params = [
        ("url", "join.com/companies/"),
        ("matchType", "prefix"),
        ("output", "json"),
        ("filter", "status:200"),
        ("collapse", "urlkey"),
    ]
    if page is not None:
        params.append(("page", str(page)))
    if show_num_pages:
        params.append(("showNumPages", "true"))
    return params


def discover_from_common_crawl(
    session: requests.Session,
    *,
    collection_count: int = 4,
    timeout: int = 180,
    index_url: str | None = None,
) -> set[str]:
    if collection_count < 1:
        raise ValueError("collection_count must be at least 1")

    if index_url:
        collections = [{"id": "configured", "cdx-api": index_url}]
    else:
        response = session.get(COMMON_CRAWL_COLLECTIONS_URL, timeout=timeout)
        response.raise_for_status()
        collections = response.json()
        if not isinstance(collections, list) or not collections:
            raise ValueError("Common Crawl collection metadata is invalid")

    companies: set[str] = set()
    successful = 0
    for collection in collections[:collection_count]:
        collection_id = str(collection.get("id", "unknown"))
        api_url = str(collection.get("cdx-api", ""))
        if not api_url:
            LOGGER.warning("Skipping Common Crawl collection %s without a CDX API", collection_id)
            continue
        try:
            count_response = session.get(
                api_url,
                params=common_crawl_params(show_num_pages=True),
                timeout=timeout,
            )
            count_response.raise_for_status()
            page_count = int(count_response.json().get("pages", 0))
            collection_companies: set[str] = set()
            for page in range(page_count):
                page_response = session.get(
                    api_url,
                    params=common_crawl_params(page=page),
                    timeout=timeout,
                )
                page_response.raise_for_status()
                collection_companies.update(parse_common_crawl_records(page_response.text))
            companies.update(collection_companies)
            successful += 1
            LOGGER.info(
                "Common Crawl %s found %d companies across %d page(s)",
                collection_id,
                len(collection_companies),
                page_count,
            )
        except (requests.RequestException, ValueError, TypeError) as error:
            LOGGER.warning("Common Crawl %s failed: %s", collection_id, error)

    if successful == 0:
        raise RuntimeError("No Common Crawl collection could be queried successfully")
    LOGGER.info("Common Crawl union found %d unique companies", len(companies))
    return companies


def load_existing(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.exists():
        return [], set()
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = [item for item in data if isinstance(item, dict)]
    slugs = {entry["company_name"] for entry in normalized_entries(entries)}
    return entries, slugs


def discover_from_google_cse(session: requests.Session) -> set[str]:
    api_keys = json.loads(os.getenv("API_KEYS", "[]"))
    configurations = json.loads(os.getenv("CSE_CONFIG", "[]"))
    if not api_keys or not configurations:
        LOGGER.info("Google CSE fallback is not configured")
        return set()
    companies: set[str] = set()
    key_cycle = itertools.cycle(api_keys)
    for configuration in configurations:
        for start in range(1, 101, 10):
            response = session.get(
                GOOGLE_CSE_URL,
                params={
                    "q": configuration["query"],
                    "cx": configuration["cse_id"],
                    "key": next(key_cycle),
                    "start": start,
                    "num": 10,
                },
                timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
            if response.status_code == 429:
                LOGGER.warning("Google CSE quota exceeded for one request")
                break
            response.raise_for_status()
            payload = response.json()
            companies.update(
                slug
                for item in payload.get("items", [])
                if (slug := extract_company_slug(item.get("link", "")))
            )
            if not payload.get("queries", {}).get("nextPage"):
                break
            time.sleep(0.2)
    LOGGER.info("Google CSE fallback found %d companies", len(companies))
    return companies


def write_catalog(
    path: Path,
    values: Iterable[str | dict[str, str]],
    *,
    dry_run: bool = False,
) -> bool:
    entries = normalized_entries(values)
    rendered = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    if dry_run:
        return True
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return True


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    output_path = Path(os.getenv("OUTPUT_FILE", "websites.json"))
    collection_count = int(os.getenv("CC_CRAWL_COUNT", "4"))
    discovery_timeout = int(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "180"))
    minimum_count = int(os.getenv("MIN_COMPANY_COUNT", "6000"))
    maximum_count = int(os.getenv("MAX_COMPANY_COUNT", "50000"))
    _, existing_slugs = load_existing(output_path)
    discovered = set(existing_slugs)
    LOGGER.info("Loaded %d existing companies", len(existing_slugs))
    session = build_session()

    sources = (
        ("sitemap", lambda: discover_from_sitemaps(session, os.getenv("JOIN_SITEMAP_INDEX", DEFAULT_SITEMAP_INDEX))),
        (
            "common_crawl",
            lambda: discover_from_common_crawl(
                session,
                collection_count=collection_count,
                timeout=discovery_timeout,
                index_url=os.getenv("COMMON_CRAWL_INDEX_URL") or None,
            ),
        ),
        ("job_urls", lambda: discover_from_job_url_source(session, os.getenv("JOB_URL_SOURCE", DEFAULT_JOB_URL_SOURCE))),
        ("archive_cdx", lambda: discover_from_cdx(session, os.getenv("JOIN_CDX_URL", DEFAULT_CDX_URL))),
        ("google_cse", lambda: discover_from_google_cse(session)),
    )
    source_counts: dict[str, int] = {}
    try:
        for name, discover in sources:
            try:
                values = discover()
            except (requests.RequestException, ElementTree.ParseError, OSError, ValueError, RuntimeError) as error:
                LOGGER.warning("%s discovery failed: %s", name, error)
                values = set()
            source_counts[name] = len(values)
            discovered.update(values)
    finally:
        session.close()

    if len(discovered) < minimum_count:
        raise RuntimeError(f"Refusing undersized catalog: {len(discovered)} companies; minimum is {minimum_count}")
    if len(discovered) > maximum_count:
        raise RuntimeError(f"Refusing oversized catalog: {len(discovered)} companies; maximum is {maximum_count}")
    changed = write_catalog(output_path, discovered)
    LOGGER.info(
        "%s %d unique companies in %s; source counts=%s",
        "Saved" if changed else "Retained",
        len(discovered),
        output_path,
        source_counts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
