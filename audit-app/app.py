from __future__ import annotations

import os
import csv
import io
import zipfile
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, abort, redirect, render_template, request, send_file, send_from_directory, url_for
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import gzip
import re
import json
import threading
import urllib.error


APP_DIR = Path(__file__).resolve().parent
SEO_DB = Path(os.environ.get("SEO_DB", "/data/opengsc/prod.db"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "/data/reports"))
RESEARCH_DB = Path(os.environ.get("RESEARCH_DB", "/data/dashboard/research.db"))
from audits.geo_aeo.service import run_audit as run_geo_aeo_audit

app = Flask(__name__)


def db():
    if not SEO_DB.exists():
        raise RuntimeError(f"SEO database not found: {SEO_DB}")
    con = sqlite3.connect(f"file:{SEO_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con



def sqlite_regexp(pattern, value):
    if value is None:
        return 0
    try:
        return 1 if re.search(pattern or "", str(value), re.IGNORECASE) else 0
    except re.error:
        return 0


def research_db():
    RESEARCH_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(RESEARCH_DB)
    con.row_factory = sqlite3.Row
    con.create_function("REGEXP", 2, sqlite_regexp)
    con.execute("""
        CREATE TABLE IF NOT EXISTS research_keyword (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            keyword TEXT NOT NULL COLLATE NOCASE,
            avg_monthly_searches INTEGER,
            competition TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(domain, keyword)
        )
    """)
    columns = {row["name"] for row in con.execute("PRAGMA table_info(research_keyword)").fetchall()}
    if "avg_monthly_searches" not in columns:
        con.execute("ALTER TABLE research_keyword ADD COLUMN avg_monthly_searches INTEGER")
    if "competition" not in columns:
        con.execute("ALTER TABLE research_keyword ADD COLUMN competition TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_research_keyword_domain_keyword ON research_keyword(domain, keyword)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS wanted_keyword (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            keyword TEXT NOT NULL COLLATE NOCASE,
            avg_monthly_searches INTEGER,
            competition TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(domain, keyword)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_wanted_keyword_domain_keyword ON wanted_keyword(domain, keyword)")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("""CREATE TABLE IF NOT EXISTS keyword_tag (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL, UNIQUE(domain,name))""")
    con.execute("""CREATE TABLE IF NOT EXISTS research_keyword_tag (research_keyword_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY(research_keyword_id,tag_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS wanted_keyword_tag (wanted_keyword_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY(wanted_keyword_id,tag_id))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_keyword_tag_domain_name ON keyword_tag(domain,name)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_research_keyword_tag_tag ON research_keyword_tag(tag_id,research_keyword_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_wanted_keyword_tag_tag ON wanted_keyword_tag(tag_id,wanted_keyword_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS site_page (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            url TEXT NOT NULL,
            path TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'sitemap',
            discovered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(domain, url)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS page_note (
            page_id INTEGER PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(page_id) REFERENCES site_page(id) ON DELETE CASCADE
        )
    """)
    wanted_columns = {row["name"] for row in con.execute("PRAGMA table_info(wanted_keyword)").fetchall()}
    if "page_id" not in wanted_columns:
        con.execute("ALTER TABLE wanted_keyword ADD COLUMN page_id INTEGER")
    con.execute("CREATE INDEX IF NOT EXISTS idx_site_page_domain_path ON site_page(domain, path)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_wanted_keyword_page ON wanted_keyword(page_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS keyword_note (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            keyword TEXT NOT NULL COLLATE NOCASE,
            content TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(domain, keyword)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_keyword_note_domain_keyword
        ON keyword_note(domain, keyword)
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS keyword_tag_assignment (
            domain TEXT NOT NULL,
            keyword TEXT NOT NULL COLLATE NOCASE,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY(domain, keyword, tag_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS page_tag (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE,
            created_at TEXT NOT NULL,
            UNIQUE(domain, name)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS site_page_tag (
            page_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY(page_id, tag_id)
        )
    """)
    con.execute("""
        INSERT OR IGNORE INTO keyword_tag_assignment(domain,keyword,tag_id)
        SELECT r.domain,r.keyword,rt.tag_id
        FROM research_keyword r
        JOIN research_keyword_tag rt ON rt.research_keyword_id=r.id
    """)
    con.execute("""
        INSERT OR IGNORE INTO keyword_tag_assignment(domain,keyword,tag_id)
        SELECT w.domain,w.keyword,wt.tag_id
        FROM wanted_keyword w
        JOIN wanted_keyword_tag wt ON wt.wanted_keyword_id=w.id
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_run (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            error TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_page (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_run_id INTEGER NOT NULL,
            page_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            page_type TEXT,
            geo_score INTEGER,
            aeo_score INTEGER,
            combined_score INTEGER,
            geo_json TEXT NOT NULL DEFAULT '{}',
            aeo_json TEXT NOT NULL DEFAULT '{}',
            combined_json TEXT NOT NULL DEFAULT '{}',
            capture_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(audit_run_id, page_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_signal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_page_id INTEGER NOT NULL,
            family TEXT NOT NULL,
            signal_key TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            observed_status TEXT NOT NULL,
            severity TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            evidence TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            source_title TEXT,
            source_url TEXT,
            UNIQUE(audit_page_id, family, signal_key)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_signal_state (
            domain TEXT NOT NULL,
            page_id INTEGER NOT NULL,
            family TEXT NOT NULL,
            signal_key TEXT NOT NULL,
            workflow_status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT '',
            user_note TEXT NOT NULL DEFAULT '',
            override_status TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(domain, page_id, family, signal_key)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_domain_summary (
            audit_run_id INTEGER PRIMARY KEY,
            domain TEXT NOT NULL,
            pages_audited INTEGER NOT NULL DEFAULT 0,
            geo_score INTEGER,
            aeo_score INTEGER,
            combined_score INTEGER,
            fail_count INTEGER NOT NULL DEFAULT 0,
            partial_count INTEGER NOT NULL DEFAULT 0,
            pass_count INTEGER NOT NULL DEFAULT 0,
            unknown_count INTEGER NOT NULL DEFAULT 0,
            manual_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_domain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL COLLATE NOCASE UNIQUE,
            base_url TEXT NOT NULL,
            gsc_site_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_dashboard_domain_gsc
        ON dashboard_domain(gsc_site_id)
    """)
    con.commit()
    return con


def research_state_from_request(source):
    return {"q":source.get("q","").strip(),"sort":source.get("sort","keyword"),"dir":source.get("dir","asc"),"per_page":source.get("per_page","50"),"page":source.get("page","1"),"show_tag":source.getlist("show_tag"),"hide_tag":source.getlist("hide_tag")}

def research_redirect(domain,message,source):
    return redirect(url_for("research",domain=domain,message=message,**research_state_from_request(source)))

def normalize_tag_name(name):
    return " ".join((name or "").strip().split())

def move_research_keyword_to_wanted(con,domain,row,now):
    existing=con.execute("SELECT id FROM wanted_keyword WHERE domain=? AND keyword=? COLLATE NOCASE",(domain,row["keyword"])).fetchone()
    if existing:
        wanted_id=existing["id"]
        con.execute("UPDATE wanted_keyword SET avg_monthly_searches=COALESCE(?,avg_monthly_searches),competition=COALESCE(?,competition),updated_at=? WHERE id=?",(row["avg_monthly_searches"],row["competition"],now,wanted_id))
    else:
        cur=con.execute("INSERT INTO wanted_keyword(domain,keyword,avg_monthly_searches,competition,source,created_at,updated_at) VALUES (?,?,?,?,'research',?,?)",(domain,row["keyword"],row["avg_monthly_searches"],row["competition"],now,now)); wanted_id=cur.lastrowid
    con.execute("INSERT OR IGNORE INTO wanted_keyword_tag(wanted_keyword_id,tag_id) SELECT ?,tag_id FROM research_keyword_tag WHERE research_keyword_id=?",(wanted_id,row["id"]))
    con.execute("DELETE FROM research_keyword_tag WHERE research_keyword_id=?",(row["id"],))
    con.execute("DELETE FROM research_keyword WHERE id=? AND domain=?",(row["id"],domain))
    return wanted_id



def normalize_site_page_url(raw_url, domain):
    if not raw_url:
        return None
    raw_url = raw_url.strip()
    if raw_url.startswith("/"):
        raw_url = f"https://{domain}{raw_url}"
    try:
        parsed = urllib.parse.urlsplit(raw_url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    wanted = domain.lower().split(":")[0]
    if host != wanted:
        return None
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return f"https://{wanted}{path}"


def site_page_path(url):
    try:
        return urllib.parse.urlsplit(url).path or "/"
    except Exception:
        return "/"


def _fetch_sitemap_urls(url, domain, seen=None, depth=0):
    seen = seen or set()
    if depth > 5 or url in seen:
        return set()
    seen.add(url)
    req = urllib.request.Request(url, headers={"User-Agent": "SEO-GEO-AEO-Auditor/1.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
    if raw[:2] == b"\x1f\x8b" or url.lower().endswith(".gz"):
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    urls = set()
    if root_name == "sitemapindex":
        for loc in root.findall(".//{*}loc"):
            child = (loc.text or "").strip()
            if child:
                try:
                    urls.update(_fetch_sitemap_urls(child, domain, seen, depth + 1))
                except Exception:
                    pass
        return urls
    for loc in root.findall(".//{*}loc"):
        normalized = normalize_site_page_url((loc.text or "").strip(), domain)
        if normalized:
            urls.add(normalized)
    return urls


def sitemap_pages(domain):
    errors = []
    for sitemap_url in (f"https://{domain}/sitemap.xml", f"https://{domain}/sitemap_index.xml"):
        try:
            urls = _fetch_sitemap_urls(sitemap_url, domain)
            if urls:
                return urls, sitemap_url
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Could not retrieve sitemap: " + " | ".join(errors))


def _site_id_value(site):
    for key in ("id", "site_id"):
        try:
            value = site[key]
            if value:
                return value
        except Exception:
            pass
        try:
            value = getattr(site, key)
            if value:
                return value
        except Exception:
            pass
    return None


def gsc_rows_for_domain(domain):
    site = get_site(domain)
    if site.get("gsc_missing"):
        return []
    site_id = _site_id_value(site)
    con = sqlite3.connect(f"file:{SEO_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cols = {row["name"] for row in con.execute("PRAGMA table_info(gsc_keyword_inventory)").fetchall()}
        if not cols:
            return []
        keyword_col = "query" if "query" in cols else "keyword" if "keyword" in cols else None
        if not keyword_col or "page" not in cols:
            return []
        latest_col = "latest_position" if "latest_position" in cols else None
        impressions_col = "total_impressions" if "total_impressions" in cols else "impressions" if "impressions" in cols else None
        clicks_col = "total_clicks" if "total_clicks" in cols else "clicks" if "clicks" in cols else None
        parts = [
            f"{keyword_col} AS keyword",
            "page AS page",
            f"{latest_col} AS latest_position" if latest_col else "NULL AS latest_position",
            f"{impressions_col} AS impressions" if impressions_col else "0 AS impressions",
            f"{clicks_col} AS clicks" if clicks_col else "0 AS clicks",
        ]
        sql = "SELECT " + ", ".join(parts) + " FROM gsc_keyword_inventory"
        params = []
        if "site_id" in cols and site_id:
            sql += " WHERE site_id = ?"
            params.append(site_id)
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def sync_site_pages(domain):
    sitemap_urls, sitemap_source = sitemap_pages(domain)
    ranking_rows = gsc_rows_for_domain(domain)
    ranking_urls = {
        u for row in ranking_rows
        for u in [normalize_site_page_url(row["page"], domain)]
        if u
    }
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        for url in sorted(sitemap_urls):
            con.execute("""
                INSERT INTO site_page(domain,url,path,source,discovered_at,updated_at)
                VALUES (?,?,?,'sitemap',?,?)
                ON CONFLICT(domain,url) DO UPDATE SET
                    path=excluded.path,
                    source=CASE WHEN site_page.source='ranking' THEN 'sitemap+ranking' ELSE site_page.source END,
                    updated_at=excluded.updated_at
            """, (domain, url, site_page_path(url), now, now))
        for url in sorted(ranking_urls):
            con.execute("""
                INSERT INTO site_page(domain,url,path,source,discovered_at,updated_at)
                VALUES (?,?,?,'ranking',?,?)
                ON CONFLICT(domain,url) DO UPDATE SET
                    source=CASE WHEN site_page.source='sitemap' THEN 'sitemap+ranking' ELSE site_page.source END,
                    updated_at=excluded.updated_at
            """, (domain, url, site_page_path(url), now, now))
        con.commit()
    return len(sitemap_urls), len(ranking_urls), sitemap_source



def canonical_keyword_tags(con, domain, keyword):
    return con.execute(
        """SELECT t.id,t.name
           FROM keyword_tag_assignment a
           JOIN keyword_tag t ON t.id=a.tag_id
           WHERE a.domain=? AND a.keyword=? COLLATE NOCASE
           ORDER BY t.name COLLATE NOCASE""",
        (domain, keyword),
    ).fetchall()


def ensure_keyword_tag(con, domain, name):
    name=" ".join((name or "").strip().split())
    if not name: return None
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("INSERT OR IGNORE INTO keyword_tag(domain,name,created_at) VALUES (?,?,?)",(domain,name,now))
    row=con.execute("SELECT id FROM keyword_tag WHERE domain=? AND name=? COLLATE NOCASE",(domain,name)).fetchone()
    return row["id"] if row else None


def ensure_page_tag(con, domain, name):
    name=" ".join((name or "").strip().split())
    if not name: return None
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("INSERT OR IGNORE INTO page_tag(domain,name,created_at) VALUES (?,?,?)",(domain,name,now))
    row=con.execute("SELECT id FROM page_tag WHERE domain=? AND name=? COLLATE NOCASE",(domain,name)).fetchone()
    return row["id"] if row else None

def parse_keyword_csv_enriched(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:8192])
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []

    names = {n: n.strip().lower().replace("_", " ") for n in reader.fieldnames if n}
    def find(candidates):
        for original, norm in names.items():
            if norm in candidates:
                return original
        return None

    kcol = find({"keyword","keywords","query","queries","search term","search terms"}) or reader.fieldnames[0]
    scol = find({"avg. monthly searches","avg monthly searches","average monthly searches","monthly searches","search volume"})
    ccol = find({"competition","competition level"})
    out = []

    for row in reader:
        keyword = (row.get(kcol) or "").strip()
        if not keyword:
            continue
        searches = None
        if scol:
            rawv = (row.get(scol) or "").strip().replace(",", "")
            if rawv:
                try:
                    searches = int(float(rawv))
                except ValueError:
                    pass
        competition = None
        if ccol:
            rawc = (row.get(ccol) or "").strip()
            if rawc:
                low = rawc.lower()
                competition = "Medium" if low == "mid" else (low.title() if low in {"low","medium","high"} else rawc)
        out.append({"keyword": keyword, "avg_monthly_searches": searches, "competition": competition})
    return out

def parse_keyword_csv(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip(): return []
    try: dialect = csv.Sniffer().sniff(text[:4096])
    except csv.Error: dialect = csv.excel
    rows=[r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows: return []
    accepted={"keyword","keywords","query","queries","search term","search terms","search_term","search_terms"}
    first=[c.strip().lower() for c in rows[0]]
    idx=None; has_header=False
    for i,v in enumerate(first):
        if v in accepted: idx=i; has_header=True; break
    if idx is None: idx=0
    data=rows[1:] if has_header else rows
    out=[]
    for row in data:
        if idx < len(row):
            kw=row[idx].strip()
            if kw: out.append(kw)
    return out

def host_from_site(site_id: str, url: str) -> str:
    raw = site_id or url or ""
    if raw.startswith("sc-domain:"):
        return raw.split(":", 1)[1].lower()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        return (urlparse(raw).hostname or raw).lower()
    except Exception:
        return raw.lower()


def _opengsc_get_sites():
    with db() as con:
        rows = con.execute("""
            SELECT id, url, siteId, archivedAt
            FROM Site
            WHERE archivedAt IS NULL
            ORDER BY url
        """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["domain"] = host_from_site(d["siteId"], d["url"])
        out.append(d)
    return out


def _opengsc_get_site(domain: str):
    for site in get_sites():
        if site["domain"] == domain:
            return site
    abort(404)


def table_exists(con, name: str, kind: str | None = None) -> bool:
    if kind:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE name=? AND type=? LIMIT 1",
            (name, kind),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE name=? LIMIT 1", (name,)
        ).fetchone()
    return bool(row)


def domain_seo(site_id: str):
    with db() as con:
        if table_exists(con, "gsc_keyword_observation", "table"):
            summary = con.execute("""
                SELECT
                    COUNT(*) AS observations,
                    COUNT(DISTINCT query) AS keywords,
                    COUNT(DISTINCT page) AS pages,
                    COALESCE(SUM(impressions), 0) AS impressions,
                    COALESCE(SUM(clicks), 0) AS clicks,
                    ROUND(MIN(position), 1) AS best_position,
                    ROUND(MAX(position), 1) AS worst_position
                FROM gsc_keyword_observation
                WHERE site_id = ?
            """, (site_id,)).fetchone()
        else:
            summary = None

        recent = []
        if table_exists(con, "gsc_keyword_inventory", "view"):
            recent = con.execute("""
                SELECT
                    query,
                    page,
                    impressions,
                    clicks,
                    ROUND(best_position, 1) AS best_position,
                    ROUND(latest_position, 1) AS latest_position,
                    status,
                    first_seen,
                    last_seen
                FROM gsc_keyword_inventory
                WHERE site_id = ?
                ORDER BY
                    CASE status
                        WHEN 'active_7d' THEN 1
                        WHEN 'active_30d' THEN 2
                        WHEN 'stale_90d' THEN 3
                        ELSE 4
                    END,
                    impressions DESC,
                    best_position ASC
                LIMIT 20
            """, (site_id,)).fetchall()

    return summary, recent


def page_rows(site_id: str, q: str = ""):
    with db() as con:
        if not table_exists(con, "gsc_keyword_inventory", "view"):
            return []
        sql = """
            SELECT
                page,
                COUNT(DISTINCT query) AS keywords,
                COALESCE(SUM(impressions),0) AS impressions,
                COALESCE(SUM(clicks),0) AS clicks,
                ROUND(MIN(best_position),1) AS best_position,
                ROUND(AVG(avg_position),1) AS avg_position,
                MAX(last_seen) AS last_seen
            FROM gsc_keyword_inventory
            WHERE site_id = ?
        """
        params = [site_id]
        if q:
            sql += " AND (page LIKE ? OR query LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like])
        sql += """
            GROUP BY page
            ORDER BY impressions DESC, best_position ASC, page
        """
        return con.execute(sql, params).fetchall()


def page_detail(site_id: str, page_url: str):
    with db() as con:
        if not table_exists(con, "gsc_keyword_inventory", "view"):
            return [], None

        kws = con.execute("""
            SELECT
                query,
                observations,
                impressions,
                clicks,
                ROUND(best_position,1) AS best_position,
                ROUND(avg_position,1) AS avg_position,
                ROUND(latest_position,1) AS latest_position,
                ROUND(worst_position,1) AS worst_position,
                status,
                first_seen,
                last_seen
            FROM gsc_keyword_inventory
            WHERE site_id=? AND page=?
            ORDER BY impressions DESC, best_position ASC
        """, (site_id, page_url)).fetchall()

        summary = con.execute("""
            SELECT
                COUNT(DISTINCT query) AS keywords,
                COALESCE(SUM(impressions),0) AS impressions,
                COALESCE(SUM(clicks),0) AS clicks,
                ROUND(MIN(best_position),1) AS best_position,
                ROUND(AVG(avg_position),1) AS avg_position,
                MAX(last_seen) AS last_seen
            FROM gsc_keyword_inventory
            WHERE site_id=? AND page=?
        """, (site_id, page_url)).fetchone()

    return kws, summary



def keyword_rows(site_id: str):
    with db() as con:
        if not table_exists(con, "gsc_keyword_inventory", "view"):
            return []

        return con.execute("""
            SELECT
                query,
                page,
                ROUND(latest_position, 1) AS ranking
            FROM gsc_keyword_inventory
            WHERE site_id = ?
            ORDER BY
                CASE WHEN latest_position IS NULL THEN 1 ELSE 0 END,
                latest_position ASC,
                query COLLATE NOCASE ASC,
                page ASC
        """, (site_id,)).fetchall()


def build_domain_export(site):
    with db() as con:
        if not table_exists(con, "gsc_keyword_inventory", "view"):
            raise RuntimeError("gsc_keyword_inventory view is not available")

        keywords = con.execute("""
            SELECT
                query AS keyword,
                ROUND(MIN(best_position), 1) AS best_ranking,
                ROUND(MIN(latest_position), 1) AS latest_ranking,
                SUM(impressions) AS impressions,
                SUM(clicks) AS clicks,
                MIN(first_seen) AS first_seen,
                MAX(last_seen) AS last_seen,
                COUNT(DISTINCT page) AS landing_pages
            FROM gsc_keyword_inventory
            WHERE site_id = ?
            GROUP BY query
            ORDER BY
                CASE WHEN MIN(latest_position) IS NULL THEN 1 ELSE 0 END,
                MIN(latest_position) ASC,
                query COLLATE NOCASE ASC
        """, (site["id"],)).fetchall()

        landing_pages = con.execute("""
            SELECT
                page AS landing_page,
                COUNT(DISTINCT query) AS keywords,
                SUM(impressions) AS impressions,
                SUM(clicks) AS clicks,
                ROUND(MIN(best_position), 1) AS best_ranking,
                ROUND(AVG(avg_position), 1) AS avg_ranking,
                MIN(first_seen) AS first_seen,
                MAX(last_seen) AS last_seen
            FROM gsc_keyword_inventory
            WHERE site_id = ?
            GROUP BY page
            ORDER BY impressions DESC, best_ranking ASC, landing_page ASC
        """, (site["id"],)).fetchall()

        page_keywords = con.execute("""
            SELECT
                page AS landing_page,
                query AS keyword,
                ROUND(best_position, 1) AS best_ranking,
                ROUND(avg_position, 1) AS avg_ranking,
                ROUND(latest_position, 1) AS latest_ranking,
                ROUND(worst_position, 1) AS worst_ranking,
                impressions,
                clicks,
                status,
                first_seen,
                last_seen
            FROM gsc_keyword_inventory
            WHERE site_id = ?
            ORDER BY landing_page ASC, latest_ranking ASC, keyword COLLATE NOCASE ASC
        """, (site["id"],)).fetchall()

    def csv_bytes(rows):
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))
        return buf.getvalue().encode("utf-8-sig")

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("keywords.csv", csv_bytes(keywords))
        zf.writestr("landing-pages.csv", csv_bytes(landing_pages))
        zf.writestr("landing-page-keywords.csv", csv_bytes(page_keywords))

    archive.seek(0)
    return archive

def geo_engine_audit(url, page_type="auto"):
    return run_geo_aeo_audit(url, page_type)


def latest_audit_page(con,page_id):
    return con.execute(
        """SELECT ap.*,ar.id AS run_id,ar.completed_at,ar.status AS run_status
           FROM audit_page ap JOIN audit_run ar ON ar.id=ap.audit_run_id
           WHERE ap.page_id=? AND ar.status IN ('completed','partial')
           ORDER BY ar.id DESC LIMIT 1""",(page_id,)
    ).fetchone()


def recompute_domain_summary(con,run_id,domain):
    pages=con.execute("SELECT geo_score,aeo_score,combined_score FROM audit_page WHERE audit_run_id=?",(run_id,)).fetchall()
    def avg(name):
        vals=[r[name] for r in pages if r[name] is not None]
        return round(sum(vals)/len(vals)) if vals else None
    c=con.execute(
        """SELECT
        SUM(CASE WHEN s.observed_status='FAIL' THEN 1 ELSE 0 END) fail_count,
        SUM(CASE WHEN s.observed_status='PARTIAL' THEN 1 ELSE 0 END) partial_count,
        SUM(CASE WHEN s.observed_status='PASS' THEN 1 ELSE 0 END) pass_count,
        SUM(CASE WHEN s.observed_status='UNKNOWN' THEN 1 ELSE 0 END) unknown_count,
        SUM(CASE WHEN s.observed_status='MANUAL_REVIEW' THEN 1 ELSE 0 END) manual_count
        FROM audit_signal s JOIN audit_page p ON p.id=s.audit_page_id WHERE p.audit_run_id=?""",(run_id,)
    ).fetchone()
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute(
        """INSERT INTO audit_domain_summary(
        audit_run_id,domain,pages_audited,geo_score,aeo_score,combined_score,
        fail_count,partial_count,pass_count,unknown_count,manual_count,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(audit_run_id) DO UPDATE SET
        pages_audited=excluded.pages_audited,geo_score=excluded.geo_score,aeo_score=excluded.aeo_score,
        combined_score=excluded.combined_score,fail_count=excluded.fail_count,partial_count=excluded.partial_count,
        pass_count=excluded.pass_count,unknown_count=excluded.unknown_count,manual_count=excluded.manual_count""",
        (run_id,domain,len(pages),avg("geo_score"),avg("aeo_score"),avg("combined_score"),
         c["fail_count"] or 0,c["partial_count"] or 0,c["pass_count"] or 0,c["unknown_count"] or 0,c["manual_count"] or 0,now)
    )


def store_structured_audit(domain,run_id,page_id,url,report):
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    capture=report.get("capture",{}); geo=report.get("geo",{}); aeo=report.get("aeo",{}); combined=report.get("combined",{})
    with research_db() as con:
        con.execute(
            """INSERT OR REPLACE INTO audit_page(
            audit_run_id,page_id,url,page_type,geo_score,aeo_score,combined_score,
            geo_json,aeo_json,combined_json,capture_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id,page_id,url,capture.get("page_type"),geo.get("score"),aeo.get("score"),combined.get("score"),
             json.dumps(geo),json.dumps(aeo),json.dumps(combined),json.dumps(capture),now)
        )
        ap=con.execute("SELECT id FROM audit_page WHERE audit_run_id=? AND page_id=?",(run_id,page_id)).fetchone()
        audit_page_id=ap["id"]
        con.execute("DELETE FROM audit_signal WHERE audit_page_id=?",(audit_page_id,))
        for item in report.get("findings",[]):
            con.execute(
                """INSERT INTO audit_signal(
                audit_page_id,family,signal_key,category,title,observed_status,severity,
                weight,evidence,recommendation,source_title,source_url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (audit_page_id,item.get("family",""),item.get("id",""),item.get("category",""),item.get("title",""),
                 item.get("status",""),item.get("severity",""),item.get("weight",0) or 0,item.get("evidence",""),
                 item.get("recommendation",""),item.get("source_title",""),item.get("source_url",""))
            )
        recompute_domain_summary(con,run_id,domain)
        con.commit()


def audit_worker(domain,run_id,pages):
    errors=[]
    for page in pages:
        try:
            store_structured_audit(domain,run_id,page["id"],page["url"],geo_engine_audit(page["url"]))
        except Exception as exc:
            errors.append(f'{page["url"]}: {exc}')
    status="completed" if not errors else ("partial" if len(errors)<len(pages) else "failed")
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        con.execute("UPDATE audit_run SET status=?,completed_at=?,error=? WHERE id=?",(status,now,"\n".join(errors),run_id))
        recompute_domain_summary(con,run_id,domain)
        con.commit()


def start_audit_run(domain,pages,scope):
    # PB_AUDIT_ZERO_PAGE_FALLBACK
    if not pages:
        try:
            sync_site_pages(domain)
            with research_db() as _con:
                pages = _con.execute("SELECT id,url,path FROM site_page WHERE domain=? ORDER BY path COLLATE NOCASE", (domain,)).fetchall()
        except Exception:
            pages = pages or []
    if not pages:
        pages = [{"id": None, "url": "https://" + domain.rstrip("/") + "/", "path": "/"}]
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        cur=con.execute("INSERT INTO audit_run(domain,scope,status,started_at) VALUES (?,?,?,?)",(domain,scope,"running",now))
        run_id=cur.lastrowid
        con.commit()
    threading.Thread(target=audit_worker,args=(domain,run_id,[dict(x) for x in pages]),daemon=True).start()
    return run_id

def report_files(domain: str | None = None):
    if not REPORTS_DIR.exists():
        return []
    out = []
    for p in REPORTS_DIR.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".pdf", ".md", ".json", ".html", ".txt"}:
            continue
        if domain and domain.lower() not in p.name.lower():
            # Keep generic GEO/AEO reports visible too; domain matching gets priority.
            score = 1
        else:
            score = 0
        out.append((score, p.stat().st_mtime, p.name))
    out.sort(key=lambda x: (x[0], -x[1], x[2].lower()))
    return [x[2] for x in out]



def normalize_dashboard_domain(value):
    value=(value or "").strip()
    if not value:
        return None,None
    if value.lower().startswith("sc-domain:"):
        value=value.split(":",1)[1].strip()
    if "://" not in value:
        value="https://"+value
    try:
        parsed=urllib.parse.urlsplit(value)
    except Exception:
        return None,None
    host=(parsed.hostname or "").strip().lower()
    if not host or "." not in host:
        return None,None
    return host,"https://"+host


def _site_value(site,key,default=None):
    try:
        return site[key]
    except Exception:
        pass
    try:
        return getattr(site,key)
    except Exception:
        return default


def _opengsc_site_domain(site):
    for key in ("domain","url","siteUrl","site_url","property"):
        value=_site_value(site,key)
        if not value:
            continue
        domain,_=normalize_dashboard_domain(str(value))
        if domain:
            return domain
    return None


def sync_dashboard_domains_from_opengsc():
    with research_db() as _suppression_con:
        _suppression_con.execute("""
            CREATE TABLE IF NOT EXISTS domain_suppression (
                domain TEXT PRIMARY KEY COLLATE NOCASE,
                suppressed_at TEXT NOT NULL
            )
        """)
        _suppressed_domains = {
            r["domain"].lower()
            for r in _suppression_con.execute("SELECT domain FROM domain_suppression").fetchall()
        }
        _suppression_con.commit()
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        gsc_sites=_opengsc_get_sites()
    except Exception:
        gsc_sites=[]
    with research_db() as con:
        for gsc in gsc_sites:
            domain=_opengsc_site_domain(gsc)
            gsc_id=_site_value(gsc,"id")
            if not domain or not gsc_id:
                continue
            if domain.lower() in _suppressed_domains:
                continue
            con.execute(
                """INSERT INTO dashboard_domain(domain,base_url,gsc_site_id,created_at,updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(domain) DO UPDATE SET
                     gsc_site_id=excluded.gsc_site_id,
                     base_url=excluded.base_url,
                     updated_at=excluded.updated_at""",
                (domain,"https://"+domain,str(gsc_id),now,now)
            )
        con.commit()


def get_sites():
    sync_dashboard_domains_from_opengsc()
    with research_db() as con:
        rows=con.execute(
            "SELECT id,domain,base_url,gsc_site_id FROM dashboard_domain ORDER BY domain COLLATE NOCASE"
        ).fetchall()
    return [{
        "id":r["gsc_site_id"] or f"__GSC_MISSING__:{r['domain']}",
        "dashboard_domain_id":r["id"],
        "domain":r["domain"],
        "base_url":r["base_url"],
        "gsc_site_id":r["gsc_site_id"],
        "gsc_missing":not bool(r["gsc_site_id"]),
    } for r in rows]


def get_site(domain):
    wanted,_=normalize_dashboard_domain(domain)
    if not wanted:
        abort(404)
    sync_dashboard_domains_from_opengsc()
    with research_db() as con:
        r=con.execute(
            "SELECT id,domain,base_url,gsc_site_id FROM dashboard_domain WHERE domain=? COLLATE NOCASE",
            (wanted,)
        ).fetchone()
    if not r:
        abort(404)
    return {
        "id":r["gsc_site_id"] or f"__GSC_MISSING__:{r['domain']}",
        "dashboard_domain_id":r["id"],
        "domain":r["domain"],
        "base_url":r["base_url"],
        "gsc_site_id":r["gsc_site_id"],
        "gsc_missing":not bool(r["gsc_site_id"]),
    }


def create_dashboard_domain(value):
    domain,base_url=normalize_dashboard_domain(value)
    if not domain:
        raise ValueError("Enter a valid domain, for example example.com.")
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    sync_dashboard_domains_from_opengsc()
    with research_db() as con:
        existing=con.execute(
            "SELECT id FROM dashboard_domain WHERE domain=? COLLATE NOCASE",(domain,)
        ).fetchone()
        if existing:
            return domain,False
        con.execute(
            "INSERT INTO dashboard_domain(domain,base_url,gsc_site_id,created_at,updated_at) VALUES (?,?,NULL,?,?)",
            (domain,base_url,now,now)
        )
        con.commit()
    sync_dashboard_domains_from_opengsc()
    return domain,True


@app.route("/")
def index():
    sites = get_sites()
    if not sites:
        return render_template("empty.html")
    return redirect(url_for("overview", domain=sites[0]["domain"]))



@app.route("/domains/new",methods=["GET","POST"])
def domain_new():
    message=request.args.get("message","").strip()

    if request.method=="POST":
        raw=request.form.get("domain","").strip()
        try:
            domain,created=create_dashboard_domain(raw)
        except ValueError as exc:
            return render_template("domain_new.html",sites=get_sites(),site=None,message=str(exc))

        sitemap_message=""
        if created:
            try:
                sitemap_count,ranking_count,source=sync_site_pages(domain)
                sitemap_message=f" Added {sitemap_count} sitemap page(s)."
            except Exception as exc:
                sitemap_message=f" Domain added; sitemap discovery failed: {exc}"

        site=get_site(domain)
        gsc_message=" GSC linked." if not site["gsc_missing"] else " GSC missing; SEO ranking fields will remain empty."

        return redirect(url_for(
            "pages",
            domain=domain,
            message=("Domain added." if created else "Domain already exists.")+sitemap_message+gsc_message
        ))

    return render_template("domain_new.html",sites=get_sites(),site=None,message=message)


@app.route("/d/<domain>/")
def overview(domain):
    site = get_site(domain)
    summary, recent = domain_seo(site["id"])
    return render_template(
        "overview.html",
        sites=get_sites(),
        site=site,
        summary=summary,
        recent=recent,
        reports=report_files(domain)[:5],
    )


@app.route("/d/<domain>/pages")
def pages(domain):
    site = get_site(domain)
    q = request.args.get("q", "").strip()
    message = request.args.get("message", "").strip()

    with research_db() as con:
        count = con.execute("SELECT COUNT(*) AS n FROM site_page WHERE domain=?", (domain,)).fetchone()["n"]
    if count == 0:
        try:
            sync_site_pages(domain)
        except Exception as exc:
            if not message:
                message = f"Initial sitemap sync failed: {exc}"

    ranking_counts = {}
    for row in gsc_rows_for_domain(domain):
        normalized = normalize_site_page_url(row["page"], domain)
        if normalized:
            ranking_counts[normalized] = ranking_counts.get(normalized, 0) + 1

    with research_db() as con:
        wanted_counts = {row["page_id"]: row["n"] for row in con.execute(
            "SELECT page_id,COUNT(*) AS n FROM wanted_keyword WHERE page_id IS NOT NULL GROUP BY page_id"
        ).fetchall()}
        note_ids = {row["page_id"] for row in con.execute(
            "SELECT page_id FROM page_note WHERE TRIM(content)<>''"
        ).fetchall()}
        where = "WHERE domain=?"
        params = [domain]
        if q:
            where += " AND (path REGEXP ? OR url REGEXP ?)"
            params += [f"%{q}%", f"%{q}%"]
        page_rows = con.execute(f"SELECT id,url,path,source FROM site_page {where} ORDER BY path COLLATE NOCASE", params).fetchall()
        total = con.execute("SELECT COUNT(*) AS n FROM site_page WHERE domain=?", (domain,)).fetchone()["n"]

    rows = [{
        "id": r["id"], "url": r["url"], "path": r["path"], "source": r["source"],
        "ranking_count": ranking_counts.get(r["url"], 0),
        "wanted_count": wanted_counts.get(r["id"], 0),
        "has_note": r["id"] in note_ids,
    } for r in page_rows]

    return render_template("pages.html", sites=get_sites(), site=site, rows=rows, total=total, q=q, message=message)



@app.route("/d/<domain>/page")
def page(domain):
    site = get_site(domain)
    page_url = request.args.get("url", "").strip()
    if not page_url:
        abort(400)
    keywords, summary = page_detail(site["id"], page_url)
    return render_template(
        "page.html",
        sites=get_sites(),
        site=site,
        page_url=page_url,
        keywords=keywords,
        summary=summary,
    )



@app.route("/d/<domain>/keywords-dashboard")
def keywords_dashboard(domain):
    site = get_site(domain)
    return render_template("keywords_dashboard.html", sites=get_sites(), site=site)



@app.route("/d/<domain>/keywords")
def keywords(domain):
    site = get_site(domain)
    return render_template(
        "keywords.html",
        sites=get_sites(),
        site=site,
        rows=keyword_rows(site["id"]),
    )


@app.route("/d/<domain>/export")
def export_domain(domain):
    site = get_site(domain)

    try:
        archive = build_domain_export(site)
    except RuntimeError as exc:
        return str(exc), 409

    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{domain}-seo-export.zip",
    )




@app.route("/d/<domain>/wanted")
def wanted(domain):
    site = get_site(domain)
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "keyword")
    direction = request.args.get("dir", "asc").lower()
    show_tags = request.args.getlist("show_tag")
    hide_tags = request.args.getlist("hide_tag")

    try:
        per_page = int(request.args.get("per_page", "50"))
    except ValueError:
        per_page = 50
    if per_page not in {25, 50, 100, 250, 1000}:
        per_page = 50

    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    if sort not in {"keyword", "searches", "competition"}:
        sort = "keyword"
    if direction not in {"asc", "desc"}:
        direction = "asc"

    order_map = {
        "keyword": "w.keyword COLLATE NOCASE",
        "searches": "w.avg_monthly_searches",
        "competition": "CASE LOWER(COALESCE(w.competition,'')) WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'mid' THEN 2 WHEN 'high' THEN 3 ELSE 4 END",
    }

    conditions = ["w.domain = ?"]
    params = [domain]

    if q:
        conditions.append("w.keyword REGEXP ?")
        params.append(q)

    show_real = [x for x in show_tags if x != "__untagged__" and x.isdigit()]
    hide_real = [x for x in hide_tags if x != "__untagged__" and x.isdigit()]
    show_untagged = "__untagged__" in show_tags
    hide_untagged = "__untagged__" in hide_tags

    if show_real or show_untagged:
        parts = []
        if show_real:
            ph = ",".join("?" for _ in show_real)
            parts.append(
                f"EXISTS (SELECT 1 FROM wanted_keyword_tag wt WHERE wt.wanted_keyword_id=w.id AND wt.tag_id IN ({ph}))"
            )
            params.extend(int(x) for x in show_real)
        if show_untagged:
            parts.append(
                "NOT EXISTS (SELECT 1 FROM wanted_keyword_tag wt WHERE wt.wanted_keyword_id=w.id)"
            )
        conditions.append("(" + " OR ".join(parts) + ")")

    if hide_real:
        ph = ",".join("?" for _ in hide_real)
        conditions.append(
            f"NOT EXISTS (SELECT 1 FROM wanted_keyword_tag wt WHERE wt.wanted_keyword_id=w.id AND wt.tag_id IN ({ph}))"
        )
        params.extend(int(x) for x in hide_real)

    if hide_untagged:
        conditions.append(
            "EXISTS (SELECT 1 FROM wanted_keyword_tag wt WHERE wt.wanted_keyword_id=w.id)"
        )

    where = "WHERE " + " AND ".join(conditions)
    order = order_map[sort]
    direction_sql = "ASC" if direction == "asc" else "DESC"

    tags_expr = "(SELECT GROUP_CONCAT(tag_name,'||') FROM (SELECT t.name AS tag_name FROM wanted_keyword_tag wt JOIN keyword_tag t ON t.id=wt.tag_id WHERE wt.wanted_keyword_id=w.id ORDER BY t.name COLLATE NOCASE))"

    with research_db() as con:
        tags = con.execute(
            "SELECT id,name FROM keyword_tag WHERE domain=? ORDER BY name COLLATE NOCASE",
            (domain,),
        ).fetchall()

        page_options = con.execute(
            "SELECT id,path,url FROM site_page WHERE domain=? ORDER BY path COLLATE NOCASE",
            (domain,),
        ).fetchall()

        total = con.execute(
            "SELECT COUNT(*) AS n FROM wanted_keyword WHERE domain=?",
            (domain,),
        ).fetchone()["n"]

        filtered = con.execute(
            f"SELECT COUNT(*) AS n FROM wanted_keyword w {where}",
            params,
        ).fetchone()["n"]

        pages = max(1, (filtered + per_page - 1) // per_page)
        page = min(page, pages)
        offset = (page - 1) * per_page

        rows = con.execute(
            f"SELECT w.id,w.keyword,w.avg_monthly_searches,w.competition,w.source,w.page_id,w.created_at,w.updated_at,{tags_expr} AS tags FROM wanted_keyword w {where} ORDER BY {order} {direction_sql},w.keyword COLLATE NOCASE ASC LIMIT ? OFFSET ?",
            [*params, per_page, offset],
        ).fetchall()

    return render_template(
        "wanted.html",
        sites=get_sites(),
        site=site,
        rows=rows,
        page_options=page_options,
        tags=tags,
        show_tags=show_tags,
        hide_tags=hide_tags,
        total=total,
        filtered_total=filtered,
        q=q,
        sort=sort,
        direction=direction,
        page=page,
        pages=pages,
        per_page=per_page,
        start_index=(offset + 1 if filtered else 0),
        message=request.args.get("message", "").strip(),
    )



@app.post("/d/<domain>/wanted/add")
def wanted_add(domain):
    get_site(domain); keyword=request.form.get("keyword","").strip()
    if not keyword: return redirect(url_for("wanted",domain=domain,message="Keyword cannot be empty."))
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        try:
            con.execute("INSERT INTO wanted_keyword(domain,keyword,source,created_at,updated_at) VALUES (?,?,'manual',?,?)",(domain,keyword,now,now)); con.commit(); msg="Wanted keyword added."
        except sqlite3.IntegrityError: msg="That keyword is already in Wanted."
    return redirect(url_for("wanted",domain=domain,message=msg))

@app.post("/d/<domain>/wanted/<int:keyword_id>/delete")
def wanted_delete(domain, keyword_id):
    get_site(domain)
    with research_db() as con:
        con.execute("DELETE FROM wanted_keyword WHERE id=? AND domain=?",(keyword_id,domain)); con.commit()
    return redirect(url_for("wanted",domain=domain,message="Wanted keyword deleted."))

@app.post("/d/<domain>/research/<int:keyword_id>/save")
def research_save(domain,keyword_id):
    get_site(domain); now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        row=con.execute("SELECT id,keyword,avg_monthly_searches,competition FROM research_keyword WHERE id=? AND domain=?",(keyword_id,domain)).fetchone()
        if not row: return research_redirect(domain,"Research keyword not found.",request.form)
        move_research_keyword_to_wanted(con,domain,row,now); con.commit()
    return research_redirect(domain,f'Moved "{row["keyword"]}" to Wanted. Tags preserved.',request.form)


@app.route("/d/<domain>/research")
def research(domain):
    site=get_site(domain); q=request.args.get("q","").strip(); sort=request.args.get("sort","keyword"); direction=request.args.get("dir","asc").lower(); show_tags=request.args.getlist("show_tag"); hide_tags=request.args.getlist("hide_tag")
    try: per_page=int(request.args.get("per_page","50"))
    except ValueError: per_page=50
    if per_page not in {25,50,100,250,1000}: per_page=50
    try: page=max(1,int(request.args.get("page","1")))
    except ValueError: page=1
    if sort not in {"keyword","searches","competition","tag"}: sort="keyword"
    if direction not in {"asc","desc"}: direction="asc"
    conditions=["k.domain=?"]; params=[domain]
    if q: conditions.append("k.keyword REGEXP ?"); params.append(q)
    show_real=[x for x in show_tags if x!="__untagged__" and x.isdigit()]; hide_real=[x for x in hide_tags if x!="__untagged__" and x.isdigit()]
    if show_real or "__untagged__" in show_tags:
        parts=[]
        if show_real:
            ph=','.join('?' for _ in show_real); parts.append(f"EXISTS (SELECT 1 FROM research_keyword_tag rt WHERE rt.research_keyword_id=k.id AND rt.tag_id IN ({ph}))"); params.extend(int(x) for x in show_real)
        if "__untagged__" in show_tags: parts.append("NOT EXISTS (SELECT 1 FROM research_keyword_tag rt WHERE rt.research_keyword_id=k.id)")
        conditions.append('('+ ' OR '.join(parts) +')')
    if hide_real:
        ph=','.join('?' for _ in hide_real); conditions.append(f"NOT EXISTS (SELECT 1 FROM research_keyword_tag rt WHERE rt.research_keyword_id=k.id AND rt.tag_id IN ({ph}))"); params.extend(int(x) for x in hide_real)
    if "__untagged__" in hide_tags: conditions.append("EXISTS (SELECT 1 FROM research_keyword_tag rt WHERE rt.research_keyword_id=k.id)")
    where='WHERE '+' AND '.join(conditions)
    competition="CASE LOWER(COALESCE(k.competition,'')) WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'mid' THEN 2 WHEN 'high' THEN 3 ELSE 4 END"
    first_tag="COALESCE((SELECT MIN(t.name) FROM research_keyword_tag rt JOIN keyword_tag t ON t.id=rt.tag_id WHERE rt.research_keyword_id=k.id),'')"
    order={"keyword":"k.keyword COLLATE NOCASE","searches":"k.avg_monthly_searches","competition":competition,"tag":first_tag+" COLLATE NOCASE"}[sort]; direction_sql='ASC' if direction=='asc' else 'DESC'
    tags_expr="(SELECT GROUP_CONCAT(tag_name,'||') FROM (SELECT t.name AS tag_name FROM research_keyword_tag rt JOIN keyword_tag t ON t.id=rt.tag_id WHERE rt.research_keyword_id=k.id ORDER BY t.name COLLATE NOCASE))"
    with research_db() as con:
        tags=con.execute("SELECT id,name FROM keyword_tag WHERE domain=? ORDER BY name COLLATE NOCASE",(domain,)).fetchall(); total=con.execute("SELECT COUNT(*) n FROM research_keyword WHERE domain=?",(domain,)).fetchone()["n"]; filtered=con.execute(f"SELECT COUNT(*) n FROM research_keyword k {where}",params).fetchone()["n"]
        pages=max(1,(filtered+per_page-1)//per_page); page=min(page,pages); offset=(page-1)*per_page
        rows=con.execute(f"SELECT k.id,k.keyword,k.avg_monthly_searches,k.competition,k.created_at,k.updated_at,{tags_expr} AS tags FROM research_keyword k {where} ORDER BY {order} {direction_sql},k.keyword COLLATE NOCASE ASC,k.id ASC LIMIT ? OFFSET ?",[*params,per_page,offset]).fetchall()
    return render_template("research.html",sites=get_sites(),site=site,rows=rows,tags=tags,total=total,filtered_total=filtered,q=q,sort=sort,direction=direction,show_tags=show_tags,hide_tags=hide_tags,page=page,pages=pages,per_page=per_page,start_index=(offset+1 if filtered else 0),end_index=min(offset+len(rows),filtered),message=request.args.get("message","").strip(),next_keyword_dir=("desc" if sort=="keyword" and direction=="asc" else "asc"),next_searches_dir=("desc" if sort=="searches" and direction=="asc" else "asc"),next_competition_dir=("desc" if sort=="competition" and direction=="asc" else "asc"),next_tag_dir=("desc" if sort=="tag" and direction=="asc" else "asc"))


@app.post("/d/<domain>/research/bulk-save")
def research_bulk_save(domain):
    get_site(domain); scope=request.form.get("scope","selected")
    with research_db() as con:
        if scope=="all": rows=con.execute("SELECT id,keyword,avg_monthly_searches,competition FROM research_keyword WHERE domain=? ORDER BY id",(domain,)).fetchall()
        else:
            ids=[]
            for value in request.form.getlist("keyword_ids"):
                try: ids.append(int(value))
                except (TypeError,ValueError): pass
            if not ids: return research_redirect(domain,"No keywords were selected.",request.form)
            ph=','.join('?' for _ in ids); rows=con.execute(f"SELECT id,keyword,avg_monthly_searches,competition FROM research_keyword WHERE domain=? AND id IN ({ph}) ORDER BY id",[domain,*ids]).fetchall()
        now=datetime.now(timezone.utc).isoformat(timespec="seconds")
        for row in rows: move_research_keyword_to_wanted(con,domain,row,now)
        con.commit()
    return research_redirect(domain,f"Moved {len(rows)} keyword(s) to Wanted. Tags preserved.",request.form)


@app.post("/d/<domain>/research/bulk-tag")
def research_bulk_tag(domain):
    get_site(domain); scope=request.form.get("scope","selected"); action=request.form.get("tag_action","add"); tag_id_raw=request.form.get("tag_id","").strip(); new_tag=normalize_tag_name(request.form.get("new_tag",""))
    with research_db() as con:
        if scope=="all": ids=[r["id"] for r in con.execute("SELECT id FROM research_keyword WHERE domain=?",(domain,)).fetchall()]
        else:
            ids=[]
            for value in request.form.getlist("keyword_ids"):
                try: ids.append(int(value))
                except (TypeError,ValueError): pass
        if not ids: return research_redirect(domain,"No keywords were selected.",request.form)
        tag_id=None
        if action=="add" and new_tag:
            now=datetime.now(timezone.utc).isoformat(timespec="seconds"); con.execute("INSERT OR IGNORE INTO keyword_tag(domain,name,created_at) VALUES (?,?,?)",(domain,new_tag,now)); tag_id=con.execute("SELECT id FROM keyword_tag WHERE domain=? AND name=? COLLATE NOCASE",(domain,new_tag)).fetchone()["id"]
        elif tag_id_raw.isdigit():
            row=con.execute("SELECT id FROM keyword_tag WHERE id=? AND domain=?",(int(tag_id_raw),domain)).fetchone(); tag_id=row["id"] if row else None
        if tag_id is None: return research_redirect(domain,"Choose an existing tag or enter a new tag.",request.form)
        if action=="add": con.executemany("INSERT OR IGNORE INTO research_keyword_tag(research_keyword_id,tag_id) VALUES (?,?)",[(i,tag_id) for i in ids]); verb='Added'
        else: con.executemany("DELETE FROM research_keyword_tag WHERE research_keyword_id=? AND tag_id=?",[(i,tag_id) for i in ids]); verb='Removed'
        name=con.execute("SELECT name FROM keyword_tag WHERE id=?",(tag_id,)).fetchone()["name"]; con.commit()
    return research_redirect(domain,f'{verb} tag "{name}" for {len(ids)} keyword(s).',request.form)

@app.post("/d/<domain>/research/bulk-delete")
def research_bulk_delete(domain):
    get_site(domain); scope=request.form.get("scope","selected")
    with research_db() as con:
        if scope=="all": ids=[r["id"] for r in con.execute("SELECT id FROM research_keyword WHERE domain=?",(domain,)).fetchall()]
        else:
            ids=[]
            for value in request.form.getlist("keyword_ids"):
                try: ids.append(int(value))
                except (TypeError,ValueError): pass
        if not ids: return research_redirect(domain,"No keywords were selected.",request.form)
        ph=','.join('?' for _ in ids); con.execute(f"DELETE FROM research_keyword_tag WHERE research_keyword_id IN ({ph})",ids); con.execute(f"DELETE FROM research_keyword WHERE domain=? AND id IN ({ph})",[domain,*ids]); con.commit()
    return research_redirect(domain,f"Deleted {len(ids)} research keyword(s).",request.form)


@app.post("/d/<domain>/research/upload")
def research_upload(domain):
    get_site(domain)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return redirect(url_for("research", domain=domain, message="Choose a CSV file first."))

    records = parse_keyword_csv_enriched(upload.read())
    if not records:
        return redirect(url_for("research", domain=domain, message="No keywords were found in that CSV."))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = updated = 0
    with research_db() as con:
        for record in records:
            existing = con.execute(
                "SELECT id FROM research_keyword WHERE domain=? AND keyword=? COLLATE NOCASE",
                (domain, record["keyword"]),
            ).fetchone()
            if existing:
                con.execute(
                    """UPDATE research_keyword
                       SET avg_monthly_searches=COALESCE(?,avg_monthly_searches),
                           competition=COALESCE(?,competition),
                           updated_at=?
                       WHERE id=?""",
                    (record["avg_monthly_searches"], record["competition"], now, existing["id"]),
                )
                updated += 1
            else:
                con.execute(
                    """INSERT INTO research_keyword
                       (domain,keyword,avg_monthly_searches,competition,created_at,updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (domain, record["keyword"], record["avg_monthly_searches"], record["competition"], now, now),
                )
                added += 1
        con.commit()

    return redirect(url_for("research", domain=domain, message=f"Imported {added} new keyword(s). Updated {updated} existing keyword(s)."))


@app.post("/d/<domain>/research/<int:keyword_id>/edit")
def research_edit(domain,keyword_id):
    get_site(domain); keyword=request.form.get("keyword","").strip()
    if not keyword: return redirect(url_for("research",domain=domain,message="Keyword cannot be empty."))
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        try:
            cur=con.execute("UPDATE research_keyword SET keyword = ?, updated_at = ? WHERE id = ? AND domain = ?",(keyword,now,keyword_id,domain)); con.commit(); msg="Keyword updated." if cur.rowcount else "Keyword not found."
        except sqlite3.IntegrityError: msg="That keyword already exists."
    return redirect(url_for("research",domain=domain,message=msg))

@app.post("/d/<domain>/research/<int:keyword_id>/delete")
def research_delete(domain,keyword_id):
    get_site(domain)
    with research_db() as con:
        con.execute("DELETE FROM research_keyword_tag WHERE research_keyword_id=?",(keyword_id,)); con.execute("DELETE FROM research_keyword WHERE id=? AND domain=?",(keyword_id,domain)); con.commit()
    return research_redirect(domain,"Research keyword deleted.",request.form)


@app.post("/d/<domain>/pages/sync")
def pages_sync(domain):
    get_site(domain)
    try:
        sitemap_count, ranking_count, source = sync_site_pages(domain)
        message = f"Pages refreshed: {sitemap_count} sitemap page(s), {ranking_count} ranking landing page(s). Source: {source}"
    except Exception as exc:
        message = f"Page refresh failed: {exc}"
    return redirect(url_for("pages", domain=domain, message=message))


@app.route("/d/<domain>/pages/<int:page_id>")
def page_workspace(domain, page_id):
    site = get_site(domain)
    with research_db() as con:
        page = con.execute("SELECT id,domain,url,path FROM site_page WHERE id=? AND domain=?", (page_id, domain)).fetchone()
        if not page:
            abort(404)
        wanted_rows = con.execute("""
            SELECT w.id,w.keyword,w.avg_monthly_searches,w.competition,
              (SELECT GROUP_CONCAT(tag_name,'||') FROM (
                 SELECT t.name AS tag_name
                 FROM wanted_keyword_tag wt JOIN keyword_tag t ON t.id=wt.tag_id
                 WHERE wt.wanted_keyword_id=w.id ORDER BY t.name COLLATE NOCASE
              )) AS tags
            FROM wanted_keyword w
            WHERE w.domain=? AND w.page_id=?
            ORDER BY w.keyword COLLATE NOCASE
        """, (domain, page_id)).fetchall()
        note_row = con.execute("SELECT content FROM page_note WHERE page_id=?", (page_id,)).fetchone()

    ranking_rows = []
    for row in gsc_rows_for_domain(domain):
        if normalize_site_page_url(row["page"], domain) == page["url"]:
            ranking_rows.append({
                "keyword": row["keyword"],
                "latest_position": row["latest_position"],
                "impressions": row["impressions"] or 0,
                "clicks": row["clicks"] or 0,
            })
    ranking_rows.sort(key=lambda r: (r["latest_position"] is None, r["latest_position"] if r["latest_position"] is not None else 999999, r["keyword"].lower()))
    return render_template("page_workspace.html", sites=get_sites(), site=site, page=page, ranking_rows=ranking_rows, wanted_rows=wanted_rows, note=note_row["content"] if note_row else "", message=request.args.get("message", "").strip())


@app.post("/d/<domain>/pages/<int:page_id>/wanted/add")
def page_wanted_add(domain, page_id):
    get_site(domain)
    keyword = request.form.get("keyword", "").strip()

    if not keyword:
        return redirect(url_for(
            "page_workspace",
            domain=domain,
            page_id=page_id,
            message="Keyword cannot be empty.",
        ))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with research_db() as con:
        page_row = con.execute(
            "SELECT id FROM site_page WHERE id=? AND domain=?",
            (page_id, domain),
        ).fetchone()

        if not page_row:
            abort(404)

        existing = con.execute(
            "SELECT id FROM wanted_keyword WHERE domain=? AND keyword=? COLLATE NOCASE",
            (domain, keyword),
        ).fetchone()

        if existing:
            con.execute(
                "UPDATE wanted_keyword SET page_id=?,updated_at=? WHERE id=?",
                (page_id, now, existing["id"]),
            )
            message = "Existing Wanted keyword assigned to this page."
        else:
            con.execute(
                "INSERT INTO wanted_keyword(domain,keyword,page_id,source,created_at,updated_at) VALUES (?,?,?,'manual-page',?,?)",
                (domain, keyword, page_id, now, now),
            )
            message = "Wanted keyword added to this page."

        con.commit()

    return redirect(url_for(
        "page_workspace",
        domain=domain,
        page_id=page_id,
        message=message,
    ))


@app.post("/d/<domain>/pages/<int:page_id>/note")
def page_note_save(domain, page_id):
    get_site(domain)
    content = request.form.get("content", "")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        if not con.execute("SELECT id FROM site_page WHERE id=? AND domain=?", (page_id, domain)).fetchone():
            abort(404)
        con.execute("""
            INSERT INTO page_note(page_id,content,updated_at) VALUES (?,?,?)
            ON CONFLICT(page_id) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at
        """, (page_id, content, now))
        con.commit()
    return redirect(url_for("page_workspace", domain=domain, page_id=page_id, message="Note saved."))


@app.post("/d/<domain>/wanted/<int:keyword_id>/page")
def wanted_assign_page(domain, keyword_id):
    get_site(domain)
    raw = request.form.get("page_id", "").strip()
    page_id = int(raw) if raw.isdigit() else None
    with research_db() as con:
        if page_id is not None and not con.execute("SELECT id FROM site_page WHERE id=? AND domain=?", (page_id, domain)).fetchone():
            page_id = None
        con.execute("UPDATE wanted_keyword SET page_id=?,updated_at=? WHERE id=? AND domain=?", (page_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), keyword_id, domain))
        con.commit()
    return redirect(url_for("wanted", domain=domain, q=request.form.get("q", ""), sort=request.form.get("sort", "keyword"), dir=request.form.get("dir", "asc"), per_page=request.form.get("per_page", "50"), page=request.form.get("page", "1"), message="Page assignment updated."))


@app.get("/d/<domain>/keyword-note")
def keyword_note_get(domain):
    get_site(domain)
    keyword = request.args.get("keyword", "").strip()
    if not keyword:
        return {"content": ""}

    with research_db() as con:
        row = con.execute(
            "SELECT content FROM keyword_note WHERE domain=? AND keyword=? COLLATE NOCASE",
            (domain, keyword),
        ).fetchone()

    return {"content": row["content"] if row else ""}


@app.post("/d/<domain>/keyword-note")
def keyword_note_save(domain):
    get_site(domain)
    keyword = request.form.get("keyword", "").strip()
    content = request.form.get("content", "")
    if not keyword:
        abort(400)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with research_db() as con:
        con.execute(
            """
            INSERT INTO keyword_note(domain,keyword,content,updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(domain,keyword) DO UPDATE SET
              content=excluded.content,
              updated_at=excluded.updated_at
            """,
            (domain, keyword, content, now),
        )
        con.commit()

    return {"ok": True}



@app.post("/d/<domain>/taxonomy/lookup")
def taxonomy_lookup(domain):
    get_site(domain)
    kind=request.form.get("kind","keyword")
    values=[v for v in request.form.getlist("values") if v]
    out={}
    with research_db() as con:
        if kind=="page":
            tags=con.execute("SELECT id,name FROM page_tag WHERE domain=? ORDER BY name COLLATE NOCASE",(domain,)).fetchall()
            for v in values:
                if not v.isdigit(): continue
                rows=con.execute("""SELECT t.id,t.name FROM site_page_tag pt JOIN page_tag t ON t.id=pt.tag_id JOIN site_page p ON p.id=pt.page_id WHERE pt.page_id=? AND p.domain=? ORDER BY t.name COLLATE NOCASE""",(int(v),domain)).fetchall()
                out[v]=[dict(r) for r in rows]
        else:
            tags=con.execute("SELECT id,name FROM keyword_tag WHERE domain=? ORDER BY name COLLATE NOCASE",(domain,)).fetchall()
            for kw in values:
                out[kw]=[dict(r) for r in canonical_keyword_tags(con,domain,kw)]
    return {"tags":[dict(t) for t in tags],"assignments":out}


@app.post("/d/<domain>/taxonomy/apply")
def taxonomy_apply(domain):
    get_site(domain)
    kind=request.form.get("kind","keyword")
    action=request.form.get("action","add")
    values=[v for v in request.form.getlist("values") if v]
    tag_id_raw=request.form.get("tag_id","").strip()
    new_tag=request.form.get("new_tag","").strip()
    if action not in {"add","remove"} or not values: abort(400)

    with research_db() as con:
        if kind=="page":
            tag_id=ensure_page_tag(con,domain,new_tag) if new_tag and action=="add" else None
            if tag_id is None and tag_id_raw.isdigit():
                row=con.execute("SELECT id FROM page_tag WHERE id=? AND domain=?",(int(tag_id_raw),domain)).fetchone()
                tag_id=row["id"] if row else None
            if tag_id is None: abort(400)
            ids=[int(v) for v in values if v.isdigit()]
            if action=="add":
                con.executemany("INSERT OR IGNORE INTO site_page_tag(page_id,tag_id) VALUES (?,?)",[(i,tag_id) for i in ids])
            else:
                con.executemany("DELETE FROM site_page_tag WHERE page_id=? AND tag_id=?",[(i,tag_id) for i in ids])
        else:
            tag_id=ensure_keyword_tag(con,domain,new_tag) if new_tag and action=="add" else None
            if tag_id is None and tag_id_raw.isdigit():
                row=con.execute("SELECT id FROM keyword_tag WHERE id=? AND domain=?",(int(tag_id_raw),domain)).fetchone()
                tag_id=row["id"] if row else None
            if tag_id is None: abort(400)
            if action=="add":
                con.executemany("INSERT OR IGNORE INTO keyword_tag_assignment(domain,keyword,tag_id) VALUES (?,?,?)",[(domain,v,tag_id) for v in values])
            else:
                con.executemany("DELETE FROM keyword_tag_assignment WHERE domain=? AND keyword=? COLLATE NOCASE AND tag_id=?",[(domain,v,tag_id) for v in values])
        con.commit()
    return {"ok":True}


@app.route("/d/<domain>/keyword")
def keyword_workspace(domain):
    site=get_site(domain)
    keyword=request.args.get("keyword","").strip()
    if not keyword: abort(400)
    raw=[r for r in gsc_rows_for_domain(domain) if (r["keyword"] or "").casefold()==keyword.casefold()]
    with research_db() as con:
        pages=con.execute("SELECT id,path,url FROM site_page WHERE domain=? ORDER BY path COLLATE NOCASE",(domain,)).fetchall()
        by_url={p["url"]:p for p in pages}
        rankings=[]
        for r in raw:
            n=normalize_site_page_url(r["page"],domain)
            p=by_url.get(n)
            rankings.append({"page":r["page"],"page_id":p["id"] if p else None,"path":p["path"] if p else r["page"],"latest_position":r["latest_position"],"impressions":r["impressions"] or 0,"clicks":r["clicks"] or 0})
        wanted=con.execute("""SELECT w.id,w.page_id,p.path AS page_path FROM wanted_keyword w LEFT JOIN site_page p ON p.id=w.page_id WHERE w.domain=? AND w.keyword=? COLLATE NOCASE""",(domain,keyword)).fetchone()
        tags=canonical_keyword_tags(con,domain,keyword)
        all_keyword_tags=con.execute("SELECT id,name FROM keyword_tag WHERE domain=? ORDER BY name COLLATE NOCASE",(domain,)).fetchall()
        note_row=con.execute("SELECT content FROM keyword_note WHERE domain=? AND keyword=? COLLATE NOCASE",(domain,keyword)).fetchone()
    rankings.sort(key=lambda r:(r["latest_position"] is None,r["latest_position"] if r["latest_position"] is not None else 999999))
    return render_template("keyword_workspace.html",sites=get_sites(),site=site,keyword=keyword,rankings=rankings,pages=pages,wanted=wanted,tags=tags,all_keyword_tags=all_keyword_tags,note=note_row["content"] if note_row else "",message=request.args.get("message","").strip())


@app.post("/d/<domain>/keyword/wanted")
def keyword_wanted_set(domain):
    get_site(domain)
    keyword=request.form.get("keyword","").strip()
    raw=request.form.get("page_id","").strip()
    page_id=int(raw) if raw.isdigit() else None
    if not keyword: abort(400)
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        existing=con.execute("SELECT id FROM wanted_keyword WHERE domain=? AND keyword=? COLLATE NOCASE",(domain,keyword)).fetchone()
        if existing:
            con.execute("UPDATE wanted_keyword SET page_id=?,updated_at=? WHERE id=?",(page_id,now,existing["id"]))
        else:
            con.execute("INSERT INTO wanted_keyword(domain,keyword,page_id,source,created_at,updated_at) VALUES (?,?,?,'keyword-workspace',?,?)",(domain,keyword,page_id,now,now))
        con.commit()
    return redirect(url_for("keyword_workspace",domain=domain,keyword=keyword,message="Wanted target updated."))


@app.post("/d/<domain>/keyword/tag")
def keyword_tag_change(domain):
    get_site(domain)
    keyword=request.form.get("keyword","").strip()
    action=request.form.get("action","add")
    tag_id_raw=request.form.get("tag_id","").strip()
    new_tag=request.form.get("new_tag","").strip()
    with research_db() as con:
        tag_id=ensure_keyword_tag(con,domain,new_tag) if new_tag and action=="add" else None
        if tag_id is None and tag_id_raw.isdigit():
            row=con.execute("SELECT id FROM keyword_tag WHERE id=? AND domain=?",(int(tag_id_raw),domain)).fetchone()
            tag_id=row["id"] if row else None
        if tag_id is None:
            return redirect(url_for("keyword_workspace",domain=domain,keyword=keyword,message="Choose a tag."))
        if action=="add":
            con.execute("INSERT OR IGNORE INTO keyword_tag_assignment(domain,keyword,tag_id) VALUES (?,?,?)",(domain,keyword,tag_id))
        else:
            con.execute("DELETE FROM keyword_tag_assignment WHERE domain=? AND keyword=? COLLATE NOCASE AND tag_id=?",(domain,keyword,tag_id))
        con.commit()
    return redirect(url_for("keyword_workspace",domain=domain,keyword=keyword,message="Keyword tags updated."))


@app.post("/d/<domain>/keyword/note")
def keyword_note_page_save(domain):
    get_site(domain)
    keyword=request.form.get("keyword","").strip()
    content=request.form.get("content","")
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        con.execute("""INSERT INTO keyword_note(domain,keyword,content,updated_at) VALUES (?,?,?,?) ON CONFLICT(domain,keyword) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at""",(domain,keyword,content,now))
        con.commit()
    return redirect(url_for("keyword_workspace",domain=domain,keyword=keyword,message="Note saved."))

@app.route("/d/<domain>/geo-aeo")
def geo_aeo_integrated(domain):
    site=get_site(domain)
    with research_db() as con:
        latest_run=con.execute("SELECT * FROM audit_run WHERE domain=? ORDER BY id DESC LIMIT 1",(domain,)).fetchone()
        summary={}; page_rows=[]; priority_signals=[]
        if latest_run:
            summary=con.execute("SELECT * FROM audit_domain_summary WHERE audit_run_id=?",(latest_run["id"],)).fetchone() or {}
            page_rows=con.execute(
                """SELECT ap.page_id,p.path,ap.geo_score,ap.aeo_score,ap.combined_score,
                SUM(CASE WHEN s.observed_status='FAIL' THEN 1 ELSE 0 END) fail_count,
                SUM(CASE WHEN s.observed_status='PARTIAL' THEN 1 ELSE 0 END) partial_count
                FROM audit_page ap JOIN site_page p ON p.id=ap.page_id
                LEFT JOIN audit_signal s ON s.audit_page_id=ap.id
                WHERE ap.audit_run_id=? GROUP BY ap.id ORDER BY p.path COLLATE NOCASE""",(latest_run["id"],)
            ).fetchall()
            priority_signals=con.execute(
                """SELECT p.path,s.family,s.signal_key,s.category,s.title,s.observed_status,s.severity,
                COALESCE(st.workflow_status,'open') workflow_status,COALESCE(st.priority,'') priority,
                COALESCE(st.user_note,'') user_note
                FROM audit_signal s JOIN audit_page ap ON ap.id=s.audit_page_id JOIN site_page p ON p.id=ap.page_id
                LEFT JOIN audit_signal_state st ON st.domain=? AND st.page_id=ap.page_id AND st.family=s.family AND st.signal_key=s.signal_key
                WHERE ap.audit_run_id=? AND s.observed_status IN ('FAIL','PARTIAL','UNKNOWN','MANUAL_REVIEW')
                ORDER BY CASE s.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END,p.path LIMIT 100""",
                (domain,latest_run["id"])
            ).fetchall()
    return render_template("geo_aeo_integrated.html",sites=get_sites(),site=site,latest_run=latest_run,summary=summary,
                           page_rows=page_rows,priority_signals=priority_signals,message=request.args.get("message","").strip())


@app.post("/d/<domain>/geo-aeo/run")
def geo_aeo_run_domain(domain):
    get_site(domain)
    with research_db() as con:
        pages=con.execute("SELECT id,url,path FROM site_page WHERE domain=? ORDER BY path COLLATE NOCASE",(domain,)).fetchall()
    if not pages:
        return redirect(url_for("geo_aeo_integrated",domain=domain,message="No site pages are available to audit."))
    run_id=start_audit_run(domain,pages,"whole_site")
    return redirect(url_for("geo_aeo_integrated",domain=domain,message=f"Audit run #{run_id} started."))


@app.post("/d/<domain>/pages/<int:page_id>/geo-aeo/run")
def geo_aeo_run_page(domain,page_id):
    get_site(domain)
    with research_db() as con:
        page=con.execute("SELECT id,url,path FROM site_page WHERE id=? AND domain=?",(page_id,domain)).fetchone()
    if not page: abort(404)
    return {"ok":True,"run_id":start_audit_run(domain,[page],"single_page")}


@app.get("/d/<domain>/pages/<int:page_id>/geo-aeo.json")
def geo_aeo_page_json(domain,page_id):
    get_site(domain)
    with research_db() as con:
        audit=latest_audit_page(con,page_id)
        if not audit: return {"audit":None,"signals":[]}
        rows=con.execute(
            """SELECT s.family,s.signal_key,s.category,s.title,s.observed_status,s.severity,s.evidence,s.recommendation,
            s.source_title,s.source_url,COALESCE(st.workflow_status,'open') workflow_status,
            COALESCE(st.priority,'') priority,COALESCE(st.user_note,'') user_note,COALESCE(st.override_status,'') override_status
            FROM audit_signal s LEFT JOIN audit_signal_state st
            ON st.domain=? AND st.page_id=? AND st.family=s.family AND st.signal_key=s.signal_key
            WHERE s.audit_page_id=? ORDER BY s.family,s.category,s.signal_key""",(domain,page_id,audit["id"])
        ).fetchall()
    return {"audit":{"run_id":audit["run_id"],"geo_score":audit["geo_score"],"aeo_score":audit["aeo_score"],
                     "combined_score":audit["combined_score"],"page_type":audit["page_type"],"completed_at":audit["completed_at"]},
            "signals":[dict(x) for x in rows]}


@app.post("/d/<domain>/pages/<int:page_id>/geo-aeo/state")
def geo_aeo_signal_state(domain,page_id):
    get_site(domain)
    family=request.form.get("family","").strip(); signal_key=request.form.get("signal_key","").strip()
    workflow_status=request.form.get("workflow_status","open").strip(); priority=request.form.get("priority","").strip()
    user_note=request.form.get("user_note",""); override_status=request.form.get("override_status","").strip()
    if workflow_status not in {"open","accepted","fixed","ignore"}: workflow_status="open"
    if priority not in {"","low","medium","high","critical"}: priority=""
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with research_db() as con:
        con.execute(
            """INSERT INTO audit_signal_state(domain,page_id,family,signal_key,workflow_status,priority,user_note,override_status,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(domain,page_id,family,signal_key) DO UPDATE SET
            workflow_status=excluded.workflow_status,priority=excluded.priority,user_note=excluded.user_note,
            override_status=excluded.override_status,updated_at=excluded.updated_at""",
            (domain,page_id,family,signal_key,workflow_status,priority,user_note,override_status,now)
        )
        con.commit()
    return {"ok":True}


@app.route("/d/<domain>/geo-aeo/report/<int:run_id>")
def geo_aeo_report(domain,run_id):
    site=get_site(domain)
    with research_db() as con:
        run=con.execute("SELECT * FROM audit_run WHERE id=? AND domain=?",(run_id,domain)).fetchone()
        if not run: abort(404)
        summary=con.execute("SELECT * FROM audit_domain_summary WHERE audit_run_id=?",(run_id,)).fetchone() or {}
        page_rows=con.execute("SELECT ap.*,p.path FROM audit_page ap JOIN site_page p ON p.id=ap.page_id WHERE ap.audit_run_id=? ORDER BY p.path COLLATE NOCASE",(run_id,)).fetchall()
        pages=[]
        for p in page_rows:
            sig=con.execute(
                """SELECT s.*,COALESCE(st.workflow_status,'open') workflow_status,COALESCE(st.priority,'') priority,
                COALESCE(st.user_note,'') user_note FROM audit_signal s LEFT JOIN audit_signal_state st
                ON st.domain=? AND st.page_id=? AND st.family=s.family AND st.signal_key=s.signal_key
                WHERE s.audit_page_id=? ORDER BY s.family,s.category,s.signal_key""",(domain,p["page_id"],p["id"])
            ).fetchall()
            item=dict(p); item["signals"]=[dict(x) for x in sig]; pages.append(item)
    return render_template("geo_aeo_report.html",site=site,run=run,summary=summary,pages=pages)

@app.route("/d/<domain>/reports")
def reports(domain):
    site = get_site(domain)
    return render_template(
        "reports.html",
        sites=get_sites(),
        site=site,
        reports=report_files(domain),
    )


@app.post("/d/<domain>/audit")
def run_audit(domain):
    return geo_aeo_run_domain(domain)



@app.route("/reports/<path:filename>")
def download_report(filename):
    if ".." in filename or filename.startswith("/"):
        abort(400)
    return send_from_directory(REPORTS_DIR, filename, as_attachment=False)


@app.context_processor
def helpers():
    def pct(clicks, impressions):
        try:
            return round((float(clicks) / float(impressions)) * 100, 2) if impressions else 0
        except Exception:
            return 0
    return {"pct": pct}



# PB SEO crawler
from crawlers.seo import register_crawler
app.config["PB_GET_SITES"] = get_sites
register_crawler(app, research_db, get_site)


# PB delete-domain feature
from services.domains import register_delete_domain
register_delete_domain(app, research_db, get_site, get_sites)


# PB extension: DataForSEO
from integrations.dataforseo.extension import register_dataforseo_extension
register_dataforseo_extension(app, research_db, get_site, get_sites)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "4018")), debug=False)
