from __future__ import annotations

import json
import secrets
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _ensure(con):
    con.execute(
        '''
        CREATE TABLE IF NOT EXISTS report_session (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            created_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
        '''
    )
    con.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_report_session_domain_created
        ON report_session(domain, created_at DESC)
        '''
    )
    con.commit()


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def create_report_session(db_path: Path, domain: str, snapshot: dict[str, Any]) -> str:
    report_id = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = json.dumps(snapshot, default=_json_default, separators=(",", ":"))
    with _connect(db_path) as con:
        _ensure(con)
        con.execute(
            "INSERT INTO report_session(id,domain,created_at,snapshot_json) VALUES (?,?,?,?)",
            (report_id, domain, now, payload),
        )
        con.commit()
    return report_id


def load_report_session(db_path: Path, domain: str, report_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as con:
        _ensure(con)
        row = con.execute(
            '''
            SELECT id,domain,created_at,snapshot_json
            FROM report_session
            WHERE id=? AND domain=? COLLATE NOCASE
            ''',
            (report_id, domain),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "domain": row["domain"],
        "created_at": row["created_at"],
        "snapshot": json.loads(row["snapshot_json"]),
    }


def list_report_sessions(db_path: Path, domain: str, limit: int = 12):
    with _connect(db_path) as con:
        _ensure(con)
        rows = con.execute(
            '''
            SELECT id,domain,created_at
            FROM report_session
            WHERE domain=? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT ?
            ''',
            (domain, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _severity_rank(value: str):
    return {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(
        (value or "").upper(), 9
    )


def prepare_report_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    data = dict(snapshot)
    signals = list(data.get("audit_signals") or [])

    actionable = [
        s for s in signals
        if (s.get("observed_status") or "").upper()
        in {"FAIL", "PARTIAL", "UNKNOWN", "MANUAL_REVIEW"}
    ]

    grouped = {}
    for s in actionable:
        key = (
            s.get("family") or "",
            s.get("title") or "",
            s.get("recommendation") or "",
            (s.get("severity") or "").upper(),
        )
        item = grouped.setdefault(
            key,
            {
                "family": key[0],
                "title": key[1],
                "recommendation": key[2],
                "severity": key[3],
                "pages": set(),
                "statuses": Counter(),
                "evidence_examples": [],
            },
        )
        item["pages"].add(s.get("path") or "/")
        item["statuses"][(s.get("observed_status") or "").upper()] += 1
        if s.get("evidence") and len(item["evidence_examples"]) < 2:
            item["evidence_examples"].append(s["evidence"])

    priority_actions = []
    for item in grouped.values():
        item["pages"] = sorted(item["pages"])
        item["page_count"] = len(item["pages"])
        item["status_summary"] = ", ".join(
            f"{k}: {v}" for k, v in item["statuses"].most_common()
        )
        del item["statuses"]
        priority_actions.append(item)

    priority_actions.sort(
        key=lambda x: (
            _severity_rank(x["severity"]),
            -x["page_count"],
            x["family"],
            x["title"],
        )
    )
    data["priority_actions"] = priority_actions

    crawl_issues = list(data.get("crawl_issues") or [])
    technical_grouped = {}
    for issue in crawl_issues:
        key = ((issue.get("severity") or "").lower(), issue.get("title") or "")
        item = technical_grouped.setdefault(
            key,
            {
                "severity": key[0],
                "title": key[1],
                "pages": set(),
                "details": [],
            },
        )
        if issue.get("page_url"):
            item["pages"].add(issue["page_url"])
        if issue.get("detail") and issue["detail"] not in item["details"]:
            item["details"].append(issue["detail"])

    technical_actions = []
    for item in technical_grouped.values():
        item["page_count"] = len(item["pages"])
        item["pages"] = sorted(item["pages"])
        technical_actions.append(item)

    tech_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    technical_actions.sort(
        key=lambda x: (tech_rank.get(x["severity"], 9), -x["page_count"], x["title"])
    )
    data["technical_actions"] = technical_actions

    by_page = defaultdict(list)
    for s in signals:
        by_page[s.get("path") or "/"].append(s)
    data["signals_by_page"] = dict(by_page)

    seo = data.get("seo") or {}
    try:
        impressions = float(seo.get("impressions") or 0)
        clicks = float(seo.get("clicks") or 0)
        seo["ctr"] = round((clicks / impressions) * 100, 2) if impressions else 0.0
    except Exception:
        seo["ctr"] = 0.0
    data["seo"] = seo
    return data
