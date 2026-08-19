from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#111827")
BLUE = colors.HexColor("#2563EB")
SLATE = colors.HexColor("#475569")
LIGHT = colors.HexColor("#F8FAFC")
BORDER = colors.HexColor("#CBD5E1")
GREEN = colors.HexColor("#15803D")
AMBER = colors.HexColor("#B45309")
RED = colors.HexColor("#B91C1C")
MUTED = colors.HexColor("#64748B")


def _connect(path: Path, readonly: bool = False):
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _exists(con, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE name=? LIMIT 1", (name,)
        ).fetchone()
    )


def _rows(con, sql: str, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def _row(con, sql: str, params=()):
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None


def _safe_json(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _short(value: Any, limit: int = 220) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _pct(num, den):
    try:
        return round((float(num) / float(den)) * 100, 1) if den else 0.0
    except Exception:
        return 0.0


def collect_report_data(domain: str, site_id: str | None, seo_db: Path, research_db: Path):
    now = datetime.now(timezone.utc)
    data: dict[str, Any] = {
        "domain": domain,
        "site_id": site_id,
        "generated_at": now,
        "seo": None,
        "keywords": [],
        "wanted": [],
        "crawl": None,
        "crawl_issues": [],
        "crawl_pages": [],
        "audit": None,
        "audit_summary": None,
        "audit_pages": [],
        "audit_signals": [],
    }

    if seo_db.exists():
        with _connect(seo_db, readonly=True) as con:
            if site_id and _exists(con, "gsc_keyword_observation"):
                data["seo"] = _row(
                    con,
                    """
                    SELECT
                        COUNT(DISTINCT query) AS keywords,
                        COUNT(DISTINCT page) AS pages,
                        COALESCE(SUM(impressions),0) AS impressions,
                        COALESCE(SUM(clicks),0) AS clicks,
                        ROUND(MIN(position),1) AS best_position,
                        ROUND(MAX(position),1) AS worst_position,
                        MIN(date) AS first_date,
                        MAX(date) AS last_date
                    FROM gsc_keyword_observation
                    WHERE site_id=?
                    """,
                    (site_id,),
                )
            if site_id and _exists(con, "gsc_keyword_inventory"):
                data["keywords"] = _rows(
                    con,
                    """
                    SELECT query,page,
                           COALESCE(impressions,0) impressions,
                           COALESCE(clicks,0) clicks,
                           ROUND(best_position,1) best_position,
                           ROUND(latest_position,1) latest_position,
                           status,first_seen,last_seen
                    FROM gsc_keyword_inventory
                    WHERE site_id=?
                    ORDER BY impressions DESC, best_position ASC, query
                    LIMIT 100
                    """,
                    (site_id,),
                )

    if research_db.exists():
        with _connect(research_db) as con:
            if _exists(con, "wanted_keyword"):
                data["wanted"] = _rows(
                    con,
                    """
                    SELECT w.keyword,w.avg_monthly_searches,w.competition,w.source,
                           p.path AS target_path
                    FROM wanted_keyword w
                    LEFT JOIN site_page p ON p.id=w.page_id
                    WHERE w.domain=?
                    ORDER BY w.keyword COLLATE NOCASE
                    """,
                    (domain,),
                )

            if _exists(con, "crawl_run"):
                data["crawl"] = _row(
                    con,
                    """
                    SELECT *
                    FROM crawl_run
                    WHERE domain=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (domain,),
                )
                if data["crawl"]:
                    run_id = data["crawl"]["id"]
                    data["crawl_issues"] = _rows(
                        con,
                        """
                        SELECT severity,title,detail,page_url
                        FROM crawl_issue
                        WHERE crawl_run_id=?
                        ORDER BY
                          CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                                        WHEN 'medium' THEN 3 ELSE 4 END,
                          title,page_url
                        """,
                        (run_id,),
                    )
                    data["crawl_pages"] = _rows(
                        con,
                        """
                        SELECT path,url,status_code,title,description,canonical,
                               word_count,internal_links,external_links,image_count,
                               images_missing_alt,schema_types_json,indexable,
                               response_ms,error
                        FROM crawl_page
                        WHERE crawl_run_id=?
                        ORDER BY path
                        """,
                        (run_id,),
                    )

            if _exists(con, "audit_run"):
                data["audit"] = _row(
                    con,
                    """
                    SELECT *
                    FROM audit_run
                    WHERE domain=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (domain,),
                )
                if data["audit"]:
                    audit_run_id = data["audit"]["id"]
                    if _exists(con, "audit_domain_summary"):
                        data["audit_summary"] = _row(
                            con,
                            "SELECT * FROM audit_domain_summary WHERE audit_run_id=?",
                            (audit_run_id,),
                        )
                    if _exists(con, "audit_page"):
                        data["audit_pages"] = _rows(
                            con,
                            """
                            SELECT ap.*,p.path
                            FROM audit_page ap
                            JOIN site_page p ON p.id=ap.page_id
                            WHERE ap.audit_run_id=?
                            ORDER BY p.path
                            """,
                            (audit_run_id,),
                        )
                    if _exists(con, "audit_signal"):
                        data["audit_signals"] = _rows(
                            con,
                            """
                            SELECT p.path,s.family,s.signal_key,s.category,s.title,
                                   s.observed_status,s.severity,s.weight,
                                   s.evidence,s.recommendation,s.source_title,s.source_url,
                                   COALESCE(st.workflow_status,'open') workflow_status,
                                   COALESCE(st.priority,'') priority,
                                   COALESCE(st.user_note,'') user_note,
                                   COALESCE(st.override_status,'') override_status
                            FROM audit_signal s
                            JOIN audit_page ap ON ap.id=s.audit_page_id
                            JOIN site_page p ON p.id=ap.page_id
                            LEFT JOIN audit_signal_state st
                              ON st.domain=? AND st.page_id=ap.page_id
                             AND st.family=s.family AND st.signal_key=s.signal_key
                            WHERE ap.audit_run_id=?
                            ORDER BY p.path,s.family,s.category,s.signal_key
                            """,
                            (domain, audit_run_id),
                        )
    return data


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=25, leading=29, textColor=NAVY, alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"], fontSize=11, leading=16,
            textColor=SLATE, spaceAfter=16,
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=17, leading=21, textColor=NAVY, spaceBefore=8, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12, leading=15, textColor=NAVY, spaceBefore=8, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontSize=8.6, leading=12,
            textColor=NAVY, spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["BodyText"], fontSize=7.1, leading=9.4,
            textColor=SLATE, spaceAfter=2,
        ),
        "tiny": ParagraphStyle(
            "Tiny", parent=base["BodyText"], fontSize=6.2, leading=8,
            textColor=SLATE,
        ),
        "metric": ParagraphStyle(
            "Metric", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=18, leading=20, textColor=NAVY, alignment=TA_CENTER,
        ),
        "metric_label": ParagraphStyle(
            "MetricLabel", parent=base["Normal"], fontSize=7, leading=9,
            textColor=MUTED, alignment=TA_CENTER,
        ),
    }


def _P(text, style):
    text = _clean(text)
    text = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return Paragraph(text or "—", style)


def _table(rows, col_widths=None, header=True, font_size=7.1):
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    for i in range(1 if header else 0, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    t.setStyle(TableStyle(style))
    return t


def _metric_cards(items, S):
    cells = []
    for label, value in items:
        cells.append([_P(str(value), S["metric"]), _P(label, S["metric_label"])])
    t = Table([cells], colWidths=[(PAGE_W - 36 * mm) / len(cells)] * len(cells))
    t.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOX", (0,0), (-1,-1), 0.5, BORDER),
        ("INNERGRID", (0,0), (-1,-1), 0.5, BORDER),
        ("BACKGROUND", (0,0), (-1,-1), colors.white),
        ("TOPPADDING", (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ]))
    return t


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.4)
    canvas.line(18*mm, 15*mm, PAGE_W-18*mm, 15*mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(18*mm, 9*mm, "SEO / GEO / AEO Domain Audit")
    canvas.drawRightString(PAGE_W-18*mm, 9*mm, f"Page {doc.page}")
    canvas.restoreState()


def build_full_report_pdf(data: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    S = _styles()
    story = []

    domain = data["domain"]
    generated_value = data.get("generated_at")
    if hasattr(generated_value, "strftime"):
        generated = generated_value.strftime("%Y-%m-%d %H:%M UTC")
    else:
        generated = str(generated_value or "")

    story.append(Spacer(1, 28*mm))
    story.append(_P("SEO / GEO / AEO", S["subtitle"]))
    story.append(_P("Domain Audit Report", S["title"]))
    story.append(_P(domain, ParagraphStyle(
        "Domain", parent=S["subtitle"], fontSize=17, leading=21, textColor=BLUE
    )))
    story.append(Spacer(1, 8*mm))
    story.append(_P(
        "Client-facing audit combining observed Google Search Console visibility, "
        "technical crawl evidence, and structured GEO/AEO findings.",
        S["subtitle"],
    ))
    story.append(Spacer(1, 55*mm))
    story.append(_P(f"Generated {generated}", S["small"]))
    if data.get("audit"):
        a = data["audit"]
        story.append(_P(
            f"Latest GEO/AEO audit run #{a['id']} · status: {a['status']} · "
            f"{a.get('completed_at') or a.get('started_at') or ''}",
            S["small"],
        ))
    story.append(PageBreak())

    story.append(_P("Executive Summary", S["h1"]))
    seo = data.get("seo") or {}
    aud = data.get("audit_summary") or {}
    crawl = data.get("crawl") or {}

    story.append(_metric_cards([
        ("Observed keywords", seo.get("keywords", "—")),
        ("Ranking pages", seo.get("pages", "—")),
        ("GEO score", aud.get("geo_score", "—")),
        ("AEO score", aud.get("aeo_score", "—")),
        ("Combined", aud.get("combined_score", "—")),
    ], S))
    story.append(Spacer(1, 5*mm))

    bullets = []
    if seo:
        bullets.append(
            f"Organic visibility: {seo.get('keywords',0)} observed keywords across "
            f"{seo.get('pages',0)} ranking pages, with {seo.get('impressions',0)} impressions "
            f"and {seo.get('clicks',0)} clicks in the stored discovery period."
        )
    if crawl:
        bullets.append(
            f"Technical crawl: {crawl.get('pages_crawled',0)} pages crawled, "
            f"{crawl.get('pages_failed',0)} failed, latest crawl status {crawl.get('status','unknown')}."
        )
    if aud:
        bullets.append(
            f"GEO/AEO readiness: GEO {aud.get('geo_score','—')}, AEO {aud.get('aeo_score','—')}, "
            f"combined {aud.get('combined_score','—')} across {aud.get('pages_audited',0)} audited pages."
        )
    for b in bullets:
        story.append(_P("• " + b, S["body"]))

    story.append(_P("Priority Findings", S["h2"]))
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    signals = [
        x for x in data.get("audit_signals", [])
        if x.get("observed_status") in {"FAIL", "PARTIAL", "UNKNOWN", "MANUAL_REVIEW"}
    ]
    signals.sort(key=lambda x: (
        severity_order.get(str(x.get("severity","")).upper(), 9),
        str(x.get("path","")),
        str(x.get("title","")),
    ))
    issue_rows = []
    for s in signals[:20]:
        issue_rows.append([
            _P(s.get("severity",""), S["tiny"]),
            _P(s.get("family",""), S["tiny"]),
            _P(s.get("path",""), S["tiny"]),
            _P(s.get("title",""), S["tiny"]),
            _P(s.get("observed_status",""), S["tiny"]),
            _P(_short(s.get("recommendation",""), 150), S["tiny"]),
        ])
    if issue_rows:
        story.append(_table(
            [[_P(x, S["tiny"]) for x in ["Severity","Family","Page","Finding","Status","Recommendation"]]] + issue_rows,
            col_widths=[14*mm,11*mm,28*mm,38*mm,17*mm,64*mm],
            font_size=6.2,
        ))
    else:
        story.append(_P("No stored GEO/AEO priority findings are available.", S["body"]))

    crawl_issues = data.get("crawl_issues", [])
    if crawl_issues:
        story.append(Spacer(1, 4*mm))
        story.append(_P("Technical Issues", S["h2"]))
        counts = Counter(i.get("severity","unknown") for i in crawl_issues)
        story.append(_P(
            ", ".join(f"{k}: {v}" for k,v in sorted(counts.items())),
            S["body"],
        ))
        rows = [[_P(x,S["tiny"]) for x in ["Severity","Issue","Page","Detail"]]]
        for i in crawl_issues[:25]:
            rows.append([
                _P(i.get("severity",""), S["tiny"]),
                _P(i.get("title",""), S["tiny"]),
                _P(_short(i.get("page_url",""), 80), S["tiny"]),
                _P(_short(i.get("detail",""), 130), S["tiny"]),
            ])
        story.append(_table(rows, [18*mm,48*mm,55*mm,55*mm], font_size=6.2))

    story.append(PageBreak())
    story.append(_P("Organic Search Visibility", S["h1"]))
    if seo:
        period = ""
        if seo.get("first_date") or seo.get("last_date"):
            period = f"{seo.get('first_date','?')} to {seo.get('last_date','?')}"
        story.append(_P(
            f"Observed GSC discovery period: {period or 'stored observation history'}. "
            f"CTR: {_pct(seo.get('clicks'), seo.get('impressions'))}%. "
            f"Observed position range: {seo.get('best_position','—')}–{seo.get('worst_position','—')}.",
            S["body"],
        ))
        rows = [[_P(x,S["tiny"]) for x in ["Query","Landing page","Impressions","Clicks","Best","Latest","Status"]]]
        for k in data.get("keywords", [])[:60]:
            rows.append([
                _P(_short(k.get("query",""), 75), S["tiny"]),
                _P(_short(k.get("page",""), 80), S["tiny"]),
                _P(k.get("impressions",0), S["tiny"]),
                _P(k.get("clicks",0), S["tiny"]),
                _P(k.get("best_position",""), S["tiny"]),
                _P(k.get("latest_position",""), S["tiny"]),
                _P(k.get("status",""), S["tiny"]),
            ])
        if len(rows) > 1:
            story.append(_table(rows, [45*mm,55*mm,18*mm,14*mm,14*mm,14*mm,20*mm], font_size=6.0))
    else:
        story.append(_P("No GSC keyword-discovery dataset is currently available.", S["body"]))

    if data.get("wanted"):
        story.append(Spacer(1, 4*mm))
        story.append(_P("Wanted / Intended Keyword Targets", S["h2"]))
        rows = [[_P(x,S["tiny"]) for x in ["Keyword","Target page","Monthly searches","Competition","Source"]]]
        for w in data["wanted"][:80]:
            rows.append([
                _P(w.get("keyword",""), S["tiny"]),
                _P(w.get("target_path") or "Unassigned", S["tiny"]),
                _P(w.get("avg_monthly_searches") if w.get("avg_monthly_searches") is not None else "—", S["tiny"]),
                _P(w.get("competition") or "—", S["tiny"]),
                _P(w.get("source") or "", S["tiny"]),
            ])
        story.append(_table(rows, [65*mm,48*mm,22*mm,22*mm,22*mm], font_size=6.2))

    story.append(PageBreak())
    story.append(_P("Technical SEO Crawl", S["h1"]))
    if crawl:
        story.append(_metric_cards([
            ("Discovered", crawl.get("pages_discovered","—")),
            ("Crawled", crawl.get("pages_crawled","—")),
            ("Failed", crawl.get("pages_failed","—")),
            ("Status", crawl.get("status","—")),
        ], S))
        story.append(Spacer(1, 5*mm))

        rows = [[_P(x,S["tiny"]) for x in ["Page","HTTP","Indexable","Words","Internal links","Schema","ms"]]]
        for p in data.get("crawl_pages", [])[:100]:
            schema = ", ".join(_safe_json(p.get("schema_types_json"), [])) or "—"
            rows.append([
                _P(_short(p.get("path") or p.get("url"), 65), S["tiny"]),
                _P(p.get("status_code",""), S["tiny"]),
                _P("yes" if p.get("indexable") else "no", S["tiny"]),
                _P(p.get("word_count",0), S["tiny"]),
                _P(p.get("internal_links",0), S["tiny"]),
                _P(_short(schema, 45), S["tiny"]),
                _P(p.get("response_ms",""), S["tiny"]),
            ])
        if len(rows) > 1:
            story.append(_table(rows, [68*mm,14*mm,18*mm,16*mm,20*mm,34*mm,14*mm], font_size=6.1))
    else:
        story.append(_P("No technical crawl has been stored for this domain.", S["body"]))

    story.append(PageBreak())
    story.append(_P("GEO / AEO Readiness", S["h1"]))
    if aud:
        story.append(_metric_cards([
            ("GEO", aud.get("geo_score","—")),
            ("AEO", aud.get("aeo_score","—")),
            ("Combined", aud.get("combined_score","—")),
            ("Pages", aud.get("pages_audited","—")),
            ("Fails", aud.get("fail_count","—")),
        ], S))
        story.append(Spacer(1, 5*mm))
        story.append(_P(
            f"Pass: {aud.get('pass_count',0)} · Partial: {aud.get('partial_count',0)} · "
            f"Fail: {aud.get('fail_count',0)} · Manual review: {aud.get('manual_count',0)}.",
            S["body"],
        ))
        rows = [[_P(x,S["tiny"]) for x in ["Page","GEO","AEO","Combined"]]]
        for p in data.get("audit_pages", []):
            rows.append([
                _P(p.get("path",""), S["tiny"]),
                _P(p.get("geo_score",""), S["tiny"]),
                _P(p.get("aeo_score",""), S["tiny"]),
                _P(p.get("combined_score",""), S["tiny"]),
            ])
        if len(rows) > 1:
            story.append(_table(rows, [120*mm,20*mm,20*mm,20*mm], font_size=6.4))
    else:
        story.append(_P("No completed GEO/AEO audit is currently stored.", S["body"]))

    if data.get("audit_pages"):
        story.append(PageBreak())
        story.append(_P("GEO / AEO Findings Appendix", S["h1"]))
        by_page = defaultdict(list)
        for s in data.get("audit_signals", []):
            by_page[s.get("path","")].append(s)

        for idx, page in enumerate(data["audit_pages"]):
            path = page.get("path","")
            story.append(_P(path or "/", S["h2"]))
            story.append(_P(
                f"GEO {page.get('geo_score','—')} · AEO {page.get('aeo_score','—')} · "
                f"Combined {page.get('combined_score','—')}",
                S["small"],
            ))
            rows = [[_P(x,S["tiny"]) for x in ["Family","Finding","Status","Severity","Evidence / Recommendation"]]]
            for s in by_page.get(path, []):
                combined = _short(s.get("evidence",""), 180)
                rec = _short(s.get("recommendation",""), 180)
                if rec:
                    combined = f"{combined}  Recommendation: {rec}".strip()
                rows.append([
                    _P(s.get("family",""), S["tiny"]),
                    _P(s.get("title",""), S["tiny"]),
                    _P(s.get("observed_status",""), S["tiny"]),
                    _P(s.get("severity",""), S["tiny"]),
                    _P(combined, S["tiny"]),
                ])
            if len(rows) > 1:
                story.append(_table(rows, [13*mm,44*mm,20*mm,18*mm,85*mm], font_size=5.8))
            story.append(Spacer(1, 5*mm))
            if idx and idx % 3 == 0:
                story.append(PageBreak())

    story.append(PageBreak())
    story.append(_P("Scope, Methodology and Limitations", S["h1"]))
    story.append(_P(
        "This report is generated from evidence currently stored by the audit application. "
        "It combines Google Search Console keyword/page observations, the latest technical crawl, "
        "and the latest structured GEO/AEO audit. Scores are evidence-based outputs of the current "
        "rule set and should be interpreted alongside the underlying findings.",
        S["body"],
    ))
    story.append(_P(
        "Not yet measured in this report: inbound backlink authority, cross-platform entity consistency, "
        "live AI citations/mentions, AI Overview visibility, LLM share of voice, and the future AIO scoring "
        "layer. These are intentionally shown as out of scope rather than inferred.",
        S["body"],
    ))
    if data.get("audit") and data["audit"].get("status") == "partial":
        story.append(_P(
            "The latest GEO/AEO run is partial. Reported page scores include successfully audited pages; "
            "the stored audit error is: " + _short(data["audit"].get("error",""), 500),
            S["body"],
        ))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18*mm,
        rightMargin=18*mm,
        topMargin=18*mm,
        bottomMargin=20*mm,
        title=f"SEO GEO AEO Domain Audit - {domain}",
        author="SEO / GEO / AEO Audit App",
        subject=f"Domain audit report for {domain}",
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return output_path
