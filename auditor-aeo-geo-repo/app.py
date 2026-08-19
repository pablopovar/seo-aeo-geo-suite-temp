from __future__ import annotations

import requests
from flask import Flask, render_template, request, jsonify

from audit_engine import build_report

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
        "schema_types": sorted(str(x) for x in capture.get("schema_types", [])),
        "lang": capture.get("lang"),
        "fetched_at": capture.get("fetched_at"),
    }
    findings = []
    for item in report.get("findings", []):
        findings.append({
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
        })
    return {
        "capture": capture_out,
        "findings": findings,
        "geo": report.get("geo", {}),
        "aeo": report.get("aeo", {}),
        "combined": report.get("combined", {}),
        "compliance": report.get("compliance"),
    }


app = Flask(__name__)


@app.get("/")
def index() -> str:
    return render_template("index.html", report=None, error=None, submitted_url="", selected_page_type="auto")


@app.post("/audit")
def run_audit() -> tuple[str, int] | str:
    submitted_url = request.form.get("url", "")
    selected_page_type = request.form.get("page_type", "auto")
    if selected_page_type not in {"auto", "homepage", "article", "product", "service", "local", "about", "webpage"}:
        selected_page_type = "auto"
    try:
        report = build_report(submitted_url, selected_page_type)
    except (ValueError, requests.RequestException) as exc:
        return render_template(
            "index.html", report=None, error=str(exc), submitted_url=submitted_url,
            selected_page_type=selected_page_type,
        ), 400
    return render_template(
        "index.html", report=report, error=None, submitted_url=submitted_url,
        selected_page_type=selected_page_type,
    )

@app.post("/api/audit")
def api_audit_json():
    payload = request.get_json(silent=True) or request.form
    submitted_url = str(payload.get("url", "")).strip()
    selected_page_type = str(payload.get("page_type", "auto")).strip()
    if selected_page_type not in {"auto", "homepage", "article", "product", "service", "local", "about", "webpage"}:
        selected_page_type = "auto"
    try:
        report = build_report(submitted_url, selected_page_type)
    except (ValueError, requests.RequestException) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "report": structured_report(report)})



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
