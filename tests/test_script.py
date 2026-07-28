import gzip
import tempfile
import unittest
from pathlib import Path

import script


class FakeResponse:
    def __init__(self, content: bytes, *, encoding: str = ""):
        self.content = content
        self.headers = {"content-encoding": encoding}
        self.status_code = 200
        self.text = content.decode("utf-8", errors="replace")

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **kwargs):
        return self.responses[url]


class DiscoveryTests(unittest.TestCase):
    def test_company_slug_from_url(self):
        self.assertEqual(script.company_slug_from_url("https://join.com/companies/acme/123-engineer"), "acme")
        self.assertEqual(script.company_slug_from_url("https://www.join.com/companies/ACME?page=2"), "acme")
        self.assertIsNone(script.company_slug_from_url("https://example.com/companies/acme"))
        self.assertIsNone(script.company_slug_from_url("https://join.com/jobs/123"))
        self.assertIsNone(script.company_slug_from_url("https://join.com/companies/sitemap-jobs-index.xml"))

    def test_recursive_sitemap_discovery_supports_gzip(self):
        index = b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://join.com/jobs-1.xml.gz</loc></sitemap><sitemap><loc>https://join.com/jobs-2.xml</loc></sitemap></sitemapindex>'
        jobs_one = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://join.com/companies/acme/123-engineer</loc></url></urlset>'
        jobs_two = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://join.com/companies/beta</loc></url><url><loc>https://join.com/jobs/ignored</loc></url></urlset>'
        session = FakeSession({
            "https://join.com/index.xml": FakeResponse(index),
            "https://join.com/jobs-1.xml.gz": FakeResponse(gzip.compress(jobs_one)),
            "https://join.com/jobs-2.xml": FakeResponse(jobs_two),
        })
        self.assertEqual(script.discover_from_sitemaps(session, "https://join.com/index.xml"), {"acme", "beta"})

    def test_common_crawl_records_extract_company_and_job_slugs(self):
        payload = "\n".join(
            [
                '{"url":"https://join.com/companies/acme"}',
                '{"url":"https://join.com/companies/acme/123-engineer"}',
                '{"url":"https://join.com/companies/beta/456-analyst"}',
                '{"url":"https://join.com/companies/gamma"}',
                '{"url":"https://join.com/de"}',
            ]
        )
        self.assertEqual(script.parse_common_crawl_records(payload), {"acme", "beta", "gamma"})
        self.assertEqual(script.parse_cdx_records(payload, require_job_url=True), {"acme", "beta"})

    def test_catalog_is_sorted_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "websites.json"
            script.write_catalog(path, ["beta", "acme", "beta"])
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '[\n  {\n    "link": "https://join.com/companies/acme",\n    "company_name": "acme"\n  },\n  {\n    "link": "https://join.com/companies/beta",\n    "company_name": "beta"\n  }\n]\n',
            )


    def test_merge_company_catalog_never_drops_existing(self):
        merged = script.merge_company_catalog(
            {"historic-company", "active-company"},
            [{"active-company", "new-company"}, set()],
        )
        self.assertEqual(
            merged,
            {"historic-company", "active-company", "new-company"},
        )


if __name__ == "__main__":
    unittest.main()
