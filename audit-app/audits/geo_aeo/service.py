from __future__ import annotations

from .engine import build_report


def structured_report(report):
    capture = report.get("capture", {})

    capture_out = {
        "requested_url": capture.get("requested_url"),
        "final_url": capture.get("final_url"),
        "status_code": capture.get("status_code"),
        "title": capture.get("title"),
        "description": capture.get("description"),
        "canonical": capture.get("canonical"),
        "page_type": capture.get("page_type"),
        "word_count": capture.get("word_count"),
        "robots_status": capture.get("robots_status"),
        "robots_blocks": capture.get("robots_blocks"),
        "sitemap_url": capture.get("sitemap_url"),
        "sitemap_status": capture.get("sitemap_status"),
        "schema_types": sorted(
            str(x) for x in capture.get("schema_types", [])
        ),
        "lang": capture.get("lang"),
        "fetched_at": capture.get("fetched_at"),
    }

    findings = []
    for item in report.get("findings", []):
        findings.append(
            {
                "id": str(item.get("id", "")),
                "family": str(item.get("family", "")),
                "category": str(item.get("category", "")),
                "title": str(item.get("title", "")),
                "status": str(item.get("status", "")),
                "evidence": str(item.get("evidence", "")),
                "recommendation": str(item.get("recommendation", "")),
                "severity": str(item.get("severity", "")),
                "weight": float(item.get("weight", 0) or 0),
                "source_title": str(item.get("source_title", "")),
                "source_url": str(item.get("source_url", "")),
            }
        )

    return {
        "capture": capture_out,
        "findings": findings,
        "geo": report.get("geo", {}),
        "aeo": report.get("aeo", {}),
        "combined": report.get("combined", {}),
        "compliance": report.get("compliance"),
    }


def run_audit(url: str, page_type: str = "auto"):
    return structured_report(build_report(url, page_type))
