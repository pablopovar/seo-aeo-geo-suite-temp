from __future__ import annotations

import unittest
from unittest.mock import patch

from app import app
from audit_engine import build_report, normalize_url


SAMPLE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Pablo Povarchik — AI Systems Auditor</title>
<meta name="description" content="Evidence-grounded audits of AI systems, websites, and technical workflows for teams that need defensible results.">
<link rel="canonical" href="https://example.com/">
<meta property="og:title" content="Pablo Povarchik — AI Systems Auditor"><meta property="og:description" content="Evidence-grounded audits.">
<meta name="twitter:card" content="summary"><link rel="icon" href="/favicon.ico">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Pablo Povarchik","url":"https://example.com","sameAs":["https://linkedin.com/in/example"]}</script>
</head><body><a href="#main">Skip to main content</a><header><nav><a href="/about">About</a><a href="/contact">Contact</a><a href="/privacy">Privacy</a></nav></header>
<main id="main"><h1>Evidence-grounded AI systems audits</h1><p>AI systems auditing is a structured examination of claims, evidence, behavior, and controls.</p>
<h2 id="method">How does the audit work?</h2><p>First, we capture the evidence. Then we test the claim. Finally, we document the result according to a published methodology.</p>
<ol><li>Capture the system</li><li>Test the behavior</li><li>Report the evidence</li></ol>
<h2 id="evidence">What evidence is included?</h2><p>According to the published 2026 methodology, each finding includes a source, a reproducible observation, and a recommendation.</p>
<p>For example, a case study can show how a team corrected 12 unsupported claims and improved review coverage by 40%.</p>
<a href="https://developers.google.com/search/docs/fundamentals/ai-optimization-guide">Google guidance</a>
<a href="https://www.w3.org/WAI/WCAG22/quickref/">W3C guidance</a>
<img src="audit.png" alt="Audit evidence table"></main><footer><a href="/services">Services</a><a href="/work">Work</a></footer></body></html>"""


class FakeResponse:
    def __init__(self, url: str, text: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        self.url = url
        self.text = text
        self.status_code = status
        self.headers = {"content-type": content_type, "content-encoding": "gzip", "cache-control": "max-age=300", "strict-transport-security": "max-age=31536000"}
        self.history: list[object] = []
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def fake_get(url: str, **_: object) -> FakeResponse:
    if url.endswith("/robots.txt"):
        return FakeResponse(url, "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml", content_type="text/plain")
    if url.endswith("/sitemap.xml"):
        return FakeResponse(url, "<?xml version='1.0'?><urlset></urlset>", content_type="application/xml")
    return FakeResponse("https://example.com/", SAMPLE_HTML)


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_normalize_url(self) -> None:
        self.assertEqual(normalize_url("example.com"), "https://example.com")
        with self.assertRaises(ValueError):
            normalize_url("ftp://example.com")

    @patch("audit_engine.requests.get", side_effect=fake_get)
    def test_rulebook_and_summary_integrity(self, _: object) -> None:
        report = build_report("example.com", "homepage")
        self.assertEqual(len(report["findings"]), 116)
        self.assertEqual(report["geo"]["total"], 56)
        self.assertEqual(report["aeo"]["total"], 60)
        for summary in (report["geo"], report["aeo"], report["combined"]):
            counted = sum(summary[key] for key in ("pass", "partial", "fail", "unknown", "manual", "not_applicable"))
            self.assertEqual(counted, summary["total"])
            self.assertEqual(summary["evaluated"], summary["pass"] + summary["partial"] + summary["fail"])

    @patch("audit_engine.requests.get", side_effect=fake_get)
    def test_utf8_title_is_preserved(self, _: object) -> None:
        report = build_report("https://example.com", "homepage")
        self.assertEqual(report["capture"]["title"], "Pablo Povarchik — AI Systems Auditor")

    @patch("audit_engine.requests.get", side_effect=fake_get)
    def test_full_report_renders(self, _: object) -> None:
        response = self.client.post("/audit", data={"url": "example.com", "page_type": "homepage"})
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("116-rule readiness framework", body)
        self.assertIn("GEO checklist", body)
        self.assertIn("AEO checklist", body)
        self.assertIn("Print / Save PDF", body)

    def test_home_page_renders(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Run full audit", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
