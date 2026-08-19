from __future__ import annotations

import gzip
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Iterable


USER_AGENT = "PB-SEO-Crawler/0.1 (+website audit; single-domain; polite)"
ROBOTS_USER_AGENT = "PB-SEO-Crawler"
DEFAULT_PAGE_CAP = 100
DEFAULT_DELAY_MS = 250
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 15


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    port = p.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = p.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    return urllib.parse.urlunsplit((scheme, netloc, path, p.query, ""))


def same_site(url: str, domain: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    domain = domain.lower().lstrip(".")
    return host == domain


def path_for_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    path = p.path or "/"
    if p.query:
        path += "?" + p.query
    return path


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.in_title = False
        self.text_parts = []
        self.in_script = False
        self.in_style = False
        self.meta = {}
        self.links = []
        self.images = []
        self.h1 = []
        self.h2 = []
        self._heading = None
        self._heading_parts = []
        self.canonical = None
        self.hreflang = []
        self.lang = None
        self.schema_types = set()
        self.jsonld_parts = []
        self.in_jsonld = False

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): (v or "") for k, v in attrs}
        tag = tag.lower()

        if tag == "html":
            self.lang = attrs.get("lang") or self.lang
        elif tag == "title":
            self.in_title = True
        elif tag == "script":
            self.in_script = True
            if "ld+json" in attrs.get("type", "").lower():
                self.in_jsonld = True
                self.jsonld_parts = []
        elif tag == "style":
            self.in_style = True
        elif tag in ("h1", "h2"):
            self._heading = tag
            self._heading_parts = []
        elif tag == "meta":
            name = (attrs.get("name") or attrs.get("property") or "").strip().lower()
            if name:
                self.meta.setdefault(name, attrs.get("content", "").strip())
        elif tag == "a":
            href = attrs.get("href", "").strip()
            if href:
                self.links.append((href, attrs.get("rel", "")))
        elif tag == "img":
            self.images.append({
                "src": attrs.get("src", "").strip(),
                "alt": attrs.get("alt", None),
            })
        elif tag == "link":
            rel = attrs.get("rel", "").lower()
            href = attrs.get("href", "").strip()
            if "canonical" in rel and href:
                self.canonical = href
            if "alternate" in rel and attrs.get("hreflang") and href:
                self.hreflang.append({
                    "lang": attrs.get("hreflang"),
                    "href": href,
                })

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        elif tag == "script":
            if self.in_jsonld:
                raw = "".join(self.jsonld_parts).strip()
                if raw:
                    self._extract_schema(raw)
            self.in_jsonld = False
            self.in_script = False
        elif tag == "style":
            self.in_style = False
        elif tag in ("h1", "h2") and self._heading == tag:
            text = " ".join("".join(self._heading_parts).split())
            if text:
                (self.h1 if tag == "h1" else self.h2).append(text)
            self._heading = None
            self._heading_parts = []

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.in_jsonld:
            self.jsonld_parts.append(data)
        if self._heading:
            self._heading_parts.append(data)
        if not self.in_script and not self.in_style:
            self.text_parts.append(data)

    def _extract_schema(self, raw):
        try:
            value = json.loads(raw)
        except Exception:
            return

        def walk(obj):
            if isinstance(obj, dict):
                t = obj.get("@type")
                if isinstance(t, str):
                    self.schema_types.add(t)
                elif isinstance(t, list):
                    self.schema_types.update(str(x) for x in t if x)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        walk(value)

    def result(self):
        text = " ".join(" ".join(self.text_parts).split())
        words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
        return {
            "title": " ".join("".join(self.title_parts).split()),
            "description": self.meta.get("description", ""),
            "robots_meta": self.meta.get("robots", ""),
            "viewport": self.meta.get("viewport", ""),
            "og_title": self.meta.get("og:title", ""),
            "og_description": self.meta.get("og:description", ""),
            "og_image": self.meta.get("og:image", ""),
            "canonical": self.canonical,
            "lang": self.lang,
            "h1": self.h1,
            "h2": self.h2,
            "word_count": len(words),
            "links": self.links,
            "images": self.images,
            "hreflang": self.hreflang,
            "schema_types": sorted(self.schema_types),
        }


def fetch(url: str, method="GET"):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip",
        },
        method=method,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(MAX_BYTES + 1)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if len(body) > MAX_BYTES:
                body = body[:MAX_BYTES]
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "final_url": resp.geturl(),
                "content_type": resp.headers.get("Content-Type", ""),
                "content_length": len(body),
                "elapsed_ms": elapsed_ms,
                "headers": dict(resp.headers.items()),
                "body": body,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = exc.read(MAX_BYTES)
        except Exception:
            body = b""
        return {
            "ok": False,
            "status": exc.code,
            "final_url": exc.geturl(),
            "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
            "content_length": len(body),
            "elapsed_ms": elapsed_ms,
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "body": body,
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status": None,
            "final_url": url,
            "content_type": "",
            "content_length": 0,
            "elapsed_ms": elapsed_ms,
            "headers": {},
            "body": b"",
            "error": str(exc),
        }


def decode_body(body: bytes, content_type: str):
    charset = "utf-8"
    m = re.search(r"charset=([^\s;]+)", content_type or "", flags=re.I)
    if m:
        charset = m.group(1).strip("\"'")
    try:
        return body.decode(charset, errors="replace")
    except Exception:
        return body.decode("utf-8", errors="replace")


def parse_sitemap(url: str, domain: str, seen=None, depth=0):
    if seen is None:
        seen = set()
    if depth > 4 or url in seen:
        return []
    seen.add(url)

    result = fetch(url)
    if not result["body"] or result["status"] not in (200, 201):
        return []

    try:
        root = ET.fromstring(result["body"])
    except Exception:
        return []

    tag = root.tag.rsplit("}", 1)[-1].lower()
    locs = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() == "loc" and el.text:
            locs.append(el.text.strip())

    if tag == "sitemapindex":
        urls = []
        for loc in locs:
            if same_site(loc, domain):
                urls.extend(parse_sitemap(loc, domain, seen, depth + 1))
        return urls

    return [normalize_url(loc) for loc in locs if same_site(loc, domain)]


UTILITY_SEGMENTS = {
    "privacy","privacy-policy","terms","terms-of-service","login","signin","sign-in",
    "register","account","cart","checkout","wp-admin","feed","search","sitemap",
    "404","cookie-policy","cookies"
}

def _candidate_reason(url, base_url, homepage_links, sitemap_urls):
    if normalize_url(url).rstrip("/") == normalize_url(base_url).rstrip("/"):
        return "Homepage"
    p = urllib.parse.urlsplit(url)
    segments = [x for x in p.path.split("/") if x]
    if url in homepage_links:
        return "Linked from homepage"
    if len(segments) == 1:
        return "Top-level page"
    if url in sitemap_urls:
        return "Sitemap page"
    return "Discovered page"

def _candidate_score(url, base_url, homepage_links, sitemap_urls):
    normalized = normalize_url(url)
    if normalized.rstrip("/") == normalize_url(base_url).rstrip("/"):
        return 10000
    p = urllib.parse.urlsplit(normalized)
    segments = [x.lower() for x in p.path.split("/") if x]
    if any(seg in UTILITY_SEGMENTS for seg in segments):
        return -10000
    score = 0
    if normalized in homepage_links:
        score += 1000
    if normalized in sitemap_urls:
        score += 300
    score += max(0, 220 - len(segments) * 45)
    important = {
        "about","services","service","products","product","contact","faq","pricing",
        "locations","location","team","company","solutions","industries","portfolio",
        "projects","resources"
    }
    if any(seg in important for seg in segments):
        score += 180
    if any(re.fullmatch(r"20\d{2}", seg) for seg in segments):
        score -= 80
    if len(segments) >= 4:
        score -= 100
    return score

def ten_page_candidates(base_url: str, domain: str, limit_candidates=60):
    base_url = normalize_url(base_url.rstrip("/") + "/")
    sitemap_urls = [normalize_url(x) for x in discover_seed_urls(base_url, domain)]
    sitemap_set = set(sitemap_urls)

    homepage_links = set()
    result = fetch(base_url)
    ctype = result.get("content_type","").lower()
    if result.get("body") and ("text/html" in ctype or "application/xhtml+xml" in ctype or not ctype):
        parser = PageParser()
        try:
            parser.feed(decode_body(result["body"], result["content_type"]))
            for href, _rel in parser.result().get("links", []):
                try:
                    target = normalize_url(urllib.parse.urljoin(base_url, href))
                except Exception:
                    continue
                if same_site(target, domain):
                    homepage_links.add(target)
        except Exception:
            pass

    pool = {base_url}
    pool.update(homepage_links)
    pool.update(sitemap_set)

    ranked = []
    for url in pool:
        score = _candidate_score(url, base_url, homepage_links, sitemap_set)
        if score > -1000:
            ranked.append((score, url))
    ranked.sort(key=lambda x: (-x[0], x[1]))

    rows = []
    for idx, (score, url) in enumerate(ranked[:limit_candidates]):
        rows.append({
            "url": url,
            "path": path_for_url(url),
            "score": score,
            "reason": _candidate_reason(url, base_url, homepage_links, sitemap_set),
            "selected": idx < 10,
        })
    return rows

def discover_seed_urls(base_url: str, domain: str):
    candidates = [
        urllib.parse.urljoin(base_url.rstrip("/") + "/", "sitemap.xml"),
        urllib.parse.urljoin(base_url.rstrip("/") + "/", "sitemap_index.xml"),
    ]
    out = []
    for sm in candidates:
        urls = parse_sitemap(sm, domain)
        if urls:
            out.extend(urls)
            break
    if not out:
        out = [normalize_url(base_url.rstrip("/") + "/")]
    seen = set()
    unique = []
    for u in out:
        u = normalize_url(u)
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def build_robot_parser(base_url: str):
    p = urllib.parse.urlsplit(base_url)
    robots_url = urllib.parse.urlunsplit((p.scheme, p.netloc, "/robots.txt", "", ""))
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp, robots_url, None
    except Exception as exc:
        return None, robots_url, str(exc)


def ensure_schema(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS crawl_run (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL,
        base_url TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        page_cap INTEGER NOT NULL,
        delay_ms INTEGER NOT NULL,
        obey_robots INTEGER NOT NULL DEFAULT 1,
        report_scope TEXT NOT NULL DEFAULT 'full',
        selected_urls_json TEXT,
        started_at TEXT,
        completed_at TEXT,
        pages_discovered INTEGER NOT NULL DEFAULT 0,
        pages_crawled INTEGER NOT NULL DEFAULT 0,
        pages_failed INTEGER NOT NULL DEFAULT 0,
        robots_url TEXT,
        error TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_crawl_run_domain
    ON crawl_run(domain, id DESC);

    CREATE TABLE IF NOT EXISTS crawl_page (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        crawl_run_id INTEGER NOT NULL,
        url TEXT NOT NULL,
        final_url TEXT,
        path TEXT,
        status_code INTEGER,
        content_type TEXT,
        content_bytes INTEGER,
        response_ms INTEGER,
        title TEXT,
        description TEXT,
        canonical TEXT,
        robots_meta TEXT,
        lang TEXT,
        viewport TEXT,
        h1_json TEXT,
        h2_json TEXT,
        word_count INTEGER,
        internal_links INTEGER NOT NULL DEFAULT 0,
        external_links INTEGER NOT NULL DEFAULT 0,
        image_count INTEGER NOT NULL DEFAULT 0,
        images_missing_alt INTEGER NOT NULL DEFAULT 0,
        schema_types_json TEXT,
        hreflang_json TEXT,
        og_title TEXT,
        og_description TEXT,
        og_image TEXT,
        indexable INTEGER,
        robots_allowed INTEGER,
        error TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(crawl_run_id, url)
    );

    CREATE INDEX IF NOT EXISTS idx_crawl_page_run
    ON crawl_page(crawl_run_id, status_code);

    CREATE TABLE IF NOT EXISTS crawl_link (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        crawl_run_id INTEGER NOT NULL,
        source_url TEXT NOT NULL,
        target_url TEXT NOT NULL,
        internal INTEGER NOT NULL,
        rel TEXT,
        UNIQUE(crawl_run_id, source_url, target_url, rel)
    );

    CREATE INDEX IF NOT EXISTS idx_crawl_link_target
    ON crawl_link(crawl_run_id, target_url);

    CREATE TABLE IF NOT EXISTS crawl_issue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        crawl_run_id INTEGER NOT NULL,
        page_url TEXT,
        issue_key TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT,
        UNIQUE(crawl_run_id, page_url, issue_key, detail)
    );

    CREATE INDEX IF NOT EXISTS idx_crawl_issue_run
    ON crawl_issue(crawl_run_id, severity);
    """)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(crawl_run)").fetchall()}
    if "report_scope" not in cols:
        con.execute("ALTER TABLE crawl_run ADD COLUMN report_scope TEXT NOT NULL DEFAULT 'full'")
    if "selected_urls_json" not in cols:
        con.execute("ALTER TABLE crawl_run ADD COLUMN selected_urls_json TEXT")
    con.commit()


def add_issue(con, run_id, page_url, key, severity, title, detail=""):
    con.execute(
        """INSERT OR IGNORE INTO crawl_issue
           (crawl_run_id,page_url,issue_key,severity,title,detail)
           VALUES (?,?,?,?,?,?)""",
        (run_id, page_url, key, severity, title, detail),
    )


def best_effort_site_page(con, domain, url):
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(site_page)").fetchall()}
    except Exception:
        return
    if not {"domain", "url", "path"}.issubset(cols):
        return
    exists = con.execute(
        "SELECT id FROM site_page WHERE domain=? AND url=? LIMIT 1",
        (domain, url),
    ).fetchone()
    if not exists:
        try:
            con.execute(
                "INSERT INTO site_page(domain,path,url) VALUES (?,?,?)",
                (domain, path_for_url(url), url),
            )
        except Exception:
            pass


def persist_page(con, run_id, domain, requested_url, robots_allowed, result, parsed):
    status = result["status"]
    final_url = result["final_url"] or requested_url
    robots_meta = (parsed.get("robots_meta") or "").lower()
    indexable = int(
        bool(status and 200 <= status < 300)
        and robots_allowed
        and "noindex" not in robots_meta
    )

    links = []
    internal_count = 0
    external_count = 0
    for href, rel in parsed.get("links", []):
        try:
            target = normalize_url(urllib.parse.urljoin(final_url, href))
        except Exception:
            continue
        scheme = urllib.parse.urlsplit(target).scheme
        if scheme not in ("http", "https"):
            continue
        internal = int(same_site(target, domain))
        internal_count += internal
        external_count += 1 - internal
        links.append((target, internal, rel))

    images = parsed.get("images", [])
    missing_alt = sum(1 for x in images if x.get("alt") is None or not str(x.get("alt")).strip())

    con.execute(
        """INSERT OR REPLACE INTO crawl_page(
            crawl_run_id,url,final_url,path,status_code,content_type,content_bytes,response_ms,
            title,description,canonical,robots_meta,lang,viewport,h1_json,h2_json,word_count,
            internal_links,external_links,image_count,images_missing_alt,schema_types_json,
            hreflang_json,og_title,og_description,og_image,indexable,robots_allowed,error,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, requested_url, final_url, path_for_url(final_url), status,
            result["content_type"], result["content_length"], result["elapsed_ms"],
            parsed.get("title",""), parsed.get("description",""), parsed.get("canonical"),
            parsed.get("robots_meta",""), parsed.get("lang"), parsed.get("viewport",""),
            json.dumps(parsed.get("h1",[]), ensure_ascii=False),
            json.dumps(parsed.get("h2",[]), ensure_ascii=False),
            parsed.get("word_count",0), internal_count, external_count,
            len(images), missing_alt,
            json.dumps(parsed.get("schema_types",[]), ensure_ascii=False),
            json.dumps(parsed.get("hreflang",[]), ensure_ascii=False),
            parsed.get("og_title",""), parsed.get("og_description",""), parsed.get("og_image",""),
            indexable, int(bool(robots_allowed)), result.get("error"), utcnow()
        ),
    )

    for target, internal, rel in links:
        con.execute(
            """INSERT OR IGNORE INTO crawl_link(crawl_run_id,source_url,target_url,internal,rel)
               VALUES (?,?,?,?,?)""",
            (run_id, requested_url, target, internal, rel or ""),
        )

    # Core crawl-derived SEO issues.
    title = parsed.get("title","").strip()
    desc = parsed.get("description","").strip()
    h1s = parsed.get("h1",[])
    canonical = parsed.get("canonical")

    if not status or status >= 400:
        add_issue(con, run_id, requested_url, "http_error", "high", "Page returned an HTTP error", str(status or result.get("error") or "fetch failed"))
    if status and 300 <= status < 400:
        add_issue(con, run_id, requested_url, "redirect", "medium", "Page redirects", final_url)
    if status and 200 <= status < 300:
        if not title:
            add_issue(con, run_id, requested_url, "missing_title", "high", "Missing title tag")
        elif len(title) < 20:
            add_issue(con, run_id, requested_url, "short_title", "low", "Very short title tag", f"{len(title)} characters")
        elif len(title) > 70:
            add_issue(con, run_id, requested_url, "long_title", "low", "Long title tag", f"{len(title)} characters")
        if not desc:
            add_issue(con, run_id, requested_url, "missing_description", "medium", "Missing meta description")
        if not h1s:
            add_issue(con, run_id, requested_url, "missing_h1", "medium", "Missing H1")
        elif len(h1s) > 1:
            add_issue(con, run_id, requested_url, "multiple_h1", "low", "Multiple H1 headings", str(len(h1s)))
        if not canonical:
            add_issue(con, run_id, requested_url, "missing_canonical", "medium", "Missing canonical URL")
        if not parsed.get("viewport"):
            add_issue(con, run_id, requested_url, "missing_viewport", "medium", "Missing viewport meta tag")
        if missing_alt:
            add_issue(con, run_id, requested_url, "missing_alt", "low", "Images missing alt text", f"{missing_alt} of {len(images)}")
        if not parsed.get("schema_types"):
            add_issue(con, run_id, requested_url, "no_schema", "low", "No JSON-LD schema detected")
        if parsed.get("word_count", 0) < 150:
            add_issue(con, run_id, requested_url, "thin_content", "low", "Low visible word count", str(parsed.get("word_count",0)))
        if "noindex" in robots_meta:
            add_issue(con, run_id, requested_url, "noindex", "medium", "Page has noindex directive")
        if not robots_allowed:
            add_issue(con, run_id, requested_url, "robots_blocked", "medium", "Blocked by robots.txt")

    best_effort_site_page(con, domain, final_url)
    con.commit()
    return [target for target, internal, _ in links if internal]


def crawl_worker(run_id, domain, base_url, page_cap, delay_ms, obey_robots, db_factory, selected_urls=None, follow_links=True):
    rp, robots_url, robots_error = build_robot_parser(base_url)
    with db_factory() as con:
        ensure_schema(con)
        con.execute(
            """UPDATE crawl_run SET status='running',started_at=?,robots_url=? WHERE id=?""",
            (utcnow(), robots_url, run_id),
        )
        con.commit()

    seeds = list(selected_urls) if selected_urls else discover_seed_urls(base_url, domain)
    queue = list(seeds)
    queued = set(queue)
    crawled = set()
    failed = 0

    try:
        while queue and len(crawled) < page_cap:
            url = queue.pop(0)
            if url in crawled:
                continue
            crawled.add(url)

            allowed = True
            if obey_robots and rp is not None:
                try:
                    allowed = rp.can_fetch(ROBOTS_USER_AGENT, url)
                except Exception:
                    allowed = True

            if not allowed:
                result = {
                    "ok": False, "status": None, "final_url": url, "content_type": "",
                    "content_length": 0, "elapsed_ms": 0, "headers": {}, "body": b"",
                    "error": "Blocked by robots.txt",
                }
                parsed = {}
                failed += 1
            else:
                result = fetch(url)
                content_type = result.get("content_type", "").lower()
                parsed = {}
                if result["body"] and ("text/html" in content_type or "application/xhtml+xml" in content_type or not content_type):
                    parser = PageParser()
                    try:
                        parser.feed(decode_body(result["body"], result["content_type"]))
                        parsed = parser.result()
                    except Exception as exc:
                        parsed = {}
                        result["error"] = (result.get("error") + "; " if result.get("error") else "") + f"HTML parse: {exc}"
                if not result["ok"] and (not result["status"] or result["status"] >= 400):
                    failed += 1

            with db_factory() as con:
                ensure_schema(con)
                new_links = persist_page(con, run_id, domain, url, allowed, result, parsed)

                if follow_links:
                    for target in new_links:
                        if len(queued) >= page_cap * 5:
                            break
                        if target not in queued and target not in crawled and same_site(target, domain):
                            queued.add(target)
                            queue.append(target)

                con.execute(
                    """UPDATE crawl_run
                       SET pages_discovered=?,pages_crawled=?,pages_failed=?
                       WHERE id=?""",
                    (len(queued), len(crawled), failed, run_id),
                )
                con.commit()

            if delay_ms:
                time.sleep(delay_ms / 1000.0)

        with db_factory() as con:
            # Flag internal broken links based on crawled target status.
            broken = con.execute(
                """SELECT DISTINCT l.source_url,l.target_url,p.status_code
                   FROM crawl_link l
                   LEFT JOIN crawl_page p
                     ON p.crawl_run_id=l.crawl_run_id AND p.url=l.target_url
                   WHERE l.crawl_run_id=? AND l.internal=1
                     AND p.status_code >= 400""",
                (run_id,),
            ).fetchall()
            for row in broken:
                add_issue(
                    con, run_id, row["source_url"], "broken_internal_link", "high",
                    "Broken internal link", f"{row['target_url']} → HTTP {row['status_code']}"
                )

            status = "partial" if failed else "completed"
            con.execute(
                """UPDATE crawl_run
                   SET status=?,completed_at=?,pages_discovered=?,pages_crawled=?,pages_failed=?
                   WHERE id=?""",
                (status, utcnow(), len(queued), len(crawled), failed, run_id),
            )
            con.commit()
    except Exception as exc:
        with db_factory() as con:
            ensure_schema(con)
            con.execute(
                "UPDATE crawl_run SET status='failed',completed_at=?,error=? WHERE id=?",
                (utcnow(), str(exc), run_id),
            )
            con.commit()


def register_crawler(app, research_db: Callable, get_site: Callable):
    from flask import abort, jsonify, redirect, render_template, request, url_for

    with research_db() as con:
        ensure_schema(con)

    @app.route("/d/<domain>/crawl")
    def seo_crawl(domain):
        site = get_site(domain)
        with research_db() as con:
            ensure_schema(con)
            runs = con.execute(
                """SELECT * FROM crawl_run WHERE domain=? ORDER BY id DESC LIMIT 20""",
                (site["domain"],),
            ).fetchall()
            latest = runs[0] if runs else None
            pages = []
            issues = []
            if latest:
                pages = con.execute(
                    """SELECT * FROM crawl_page WHERE crawl_run_id=?
                       ORDER BY CASE WHEN status_code IS NULL THEN 1 ELSE 0 END,status_code DESC,url
                       LIMIT 500""",
                    (latest["id"],),
                ).fetchall()
                issues = con.execute(
                    """SELECT severity,title,COUNT(*) AS count
                       FROM crawl_issue WHERE crawl_run_id=?
                       GROUP BY severity,title
                       ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,count DESC""",
                    (latest["id"],),
                ).fetchall()
        return render_template(
            "crawl.html",
            sites=get_sites_for_template(get_site, site),
            site=site,
            runs=runs,
            latest=latest,
            pages=pages,
            issues=issues,
        )

    # The dashboard's templates normally expect a sites collection. Since the
    # crawler is registered from app.py, we attach a callable there below.
    def get_sites_for_template(_get_site, _site):
        try:
            # Flask app config receives the dashboard's real get_sites callable.
            fn = app.config.get("PB_GET_SITES")
            return fn() if fn else [_site]
        except Exception:
            return [_site]


    @app.get("/d/<domain>/crawl/ten-page")
    def ten_page_select(domain):
        site = get_site(domain)
        base_url = "https://" + site["domain"]
        candidates = ten_page_candidates(base_url, site["domain"])
        return render_template(
            "ten_page_select.html",
            sites=get_sites_for_template(get_site, site),
            site=site,
            candidates=candidates,
        )

    @app.post("/d/<domain>/crawl/ten-page/run")
    def ten_page_run(domain):
        site = get_site(domain)
        urls = []
        seen = set()
        for raw in request.form.getlist("urls"):
            try:
                url = normalize_url(raw)
            except Exception:
                continue
            if not same_site(url, site["domain"]):
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)

        if not urls:
            return redirect(url_for("ten_page_select", domain=site["domain"]))

        urls = urls[:10]

        try:
            delay_ms = int(request.form.get("delay_ms", 2000))
        except Exception:
            delay_ms = 2000
        delay_ms = max(0, min(delay_ms, 10000))

        obey_robots = request.form.get("obey_robots", "1") != "0"
        base_url = "https://" + site["domain"]

        with research_db() as con:
            ensure_schema(con)
            cur = con.execute(
                """INSERT INTO crawl_run(
                    domain,base_url,status,page_cap,delay_ms,obey_robots,
                    report_scope,selected_urls_json
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    site["domain"], base_url, "queued", len(urls), delay_ms,
                    int(obey_robots), "ten-page", json.dumps(urls)
                ),
            )
            run_id = cur.lastrowid
            con.commit()

        t = threading.Thread(
            target=crawl_worker,
            args=(run_id, site["domain"], base_url, len(urls), delay_ms,
                  obey_robots, research_db, urls, False),
            daemon=True,
            name=f"seo-crawl-ten-page-{run_id}",
        )
        t.start()
        return redirect(url_for("seo_crawl", domain=site["domain"]))

    @app.post("/d/<domain>/crawl/run")
    def seo_crawl_run(domain):
        site = get_site(domain)
        try:
            page_cap = int(request.form.get("page_cap", DEFAULT_PAGE_CAP))
        except Exception:
            page_cap = DEFAULT_PAGE_CAP
        page_cap = max(1, min(page_cap, 5000))

        try:
            delay_ms = int(request.form.get("delay_ms", DEFAULT_DELAY_MS))
        except Exception:
            delay_ms = DEFAULT_DELAY_MS
        delay_ms = max(0, min(delay_ms, 10000))

        obey_robots = request.form.get("obey_robots", "1") != "0"
        base_url = "https://" + site["domain"]

        with research_db() as con:
            ensure_schema(con)
            cur = con.execute(
                """INSERT INTO crawl_run(
                    domain,base_url,status,page_cap,delay_ms,obey_robots
                   ) VALUES (?,?,?,?,?,?)""",
                (site["domain"], base_url, "queued", page_cap, delay_ms, int(obey_robots)),
            )
            run_id = cur.lastrowid
            con.commit()

        t = threading.Thread(
            target=crawl_worker,
            args=(run_id, site["domain"], base_url, page_cap, delay_ms, obey_robots, research_db),
            daemon=True,
            name=f"seo-crawl-{run_id}",
        )
        t.start()
        return redirect(url_for("seo_crawl", domain=site["domain"]))

    @app.get("/d/<domain>/crawl/<int:run_id>.json")
    def seo_crawl_status(domain, run_id):
        site = get_site(domain)
        with research_db() as con:
            ensure_schema(con)
            run = con.execute(
                "SELECT * FROM crawl_run WHERE id=? AND domain=?",
                (run_id, site["domain"]),
            ).fetchone()
            if not run:
                abort(404)
            counts = con.execute(
                """SELECT severity,COUNT(*) AS count
                   FROM crawl_issue WHERE crawl_run_id=?
                   GROUP BY severity""",
                (run_id,),
            ).fetchall()
        payload = dict(run)
        payload["issues"] = {r["severity"]: r["count"] for r in counts}
        return jsonify(payload)
