from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

STATUS_VALUES = {"PASS": 1.0, "PARTIAL": 0.5, "FAIL": 0.0}
SEVERITY_WEIGHTS = {"CRITICAL": 3.0, "HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.5}

SOURCES = {
    "ai": ("Google: Generative AI optimization", "https://developers.google.com/search/docs/fundamentals/ai-optimization-guide"),
    "search": ("Google Search Essentials", "https://developers.google.com/search/docs/essentials"),
    "seo": ("Google SEO Starter Guide", "https://developers.google.com/search/docs/fundamentals/seo-starter-guide"),
    "helpful": ("Google: Helpful, reliable, people-first content", "https://developers.google.com/search/docs/fundamentals/creating-helpful-content"),
    "schema": ("Google structured-data guidelines", "https://developers.google.com/search/docs/appearance/structured-data/sd-policies"),
    "schemaorg": ("Schema.org validator", "https://validator.schema.org/"),
    "article": ("Google Article structured data", "https://developers.google.com/search/docs/appearance/structured-data/article"),
    "product": ("Google Product structured data", "https://developers.google.com/search/docs/appearance/structured-data/product"),
    "organization": ("Google Organization structured data", "https://developers.google.com/search/docs/appearance/structured-data/organization"),
    "local": ("Google LocalBusiness structured data", "https://developers.google.com/search/docs/appearance/structured-data/local-business"),
    "breadcrumb": ("Google Breadcrumb structured data", "https://developers.google.com/search/docs/appearance/structured-data/breadcrumb"),
    "wcag": ("W3C WCAG 2.2 quick reference", "https://www.w3.org/WAI/WCAG22/quickref/"),
}


def normalize_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("Enter a website URL.")
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid HTTP or HTTPS URL.")
    return url


def _text(tag: Any) -> str:
    return tag.get_text(" ", strip=True) if tag else ""


def _meta(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"name": re.compile(rf"^{re.escape(name)}$", re.I)})
    return str(tag.get("content", "")).strip() if isinstance(tag, Tag) else ""


def _property(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"property": re.compile(rf"^{re.escape(name)}$", re.I)})
    return str(tag.get("content", "")).strip() if isinstance(tag, Tag) else ""


def _decode_response(response: requests.Response) -> str:
    encoding = (response.encoding or "").lower()
    if not encoding or encoding in {"iso-8859-1", "latin-1"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _schema_objects(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], list[str]]:
    objects: list[dict[str, Any]] = []
    errors: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            objects.append(value)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for index, script in enumerate(soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}), 1):
        raw = script.string or script.get_text()
        try:
            collect(json.loads(raw))
        except (json.JSONDecodeError, TypeError) as exc:
            errors.append(f"JSON-LD block {index}: {exc.msg if isinstance(exc, json.JSONDecodeError) else exc}")
    return objects, errors


def _schema_types(objects: list[dict[str, Any]]) -> set[str]:
    types: set[str] = set()
    for item in objects:
        value = item.get("@type")
        if isinstance(value, str):
            types.add(value)
        elif isinstance(value, list):
            types.update(str(part) for part in value)
    return types


def _page_type(selected: str, final_url: str, types: set[str], text: str) -> str:
    if selected != "auto":
        return selected
    path = urlparse(final_url).path.rstrip("/")
    if path == "":
        return "homepage"
    if types & {"Article", "BlogPosting", "NewsArticle"} or re.search(r"/(blog|news|articles?)/", path, re.I):
        return "article"
    if "Product" in types or re.search(r"/(products?|shop)/", path, re.I):
        return "product"
    if types & {"LocalBusiness", "Store", "Restaurant"} or re.search(r"/(locations?|stores?)/", path, re.I):
        return "local"
    if "AboutPage" in types or re.search(r"/(about|team|company)/", path, re.I):
        return "about"
    if "Service" in types or "our services" in text.lower():
        return "service"
    return "webpage"


def _supporting_file(url: str, user_agent: str) -> tuple[int | None, str]:
    try:
        response = requests.get(url, headers={"User-Agent": user_agent}, timeout=(4, 10), allow_redirects=True)
        return response.status_code, _decode_response(response)
    except requests.RequestException:
        return None, ""


def _robots_blocks(robots_text: str, path: str) -> bool:
    applies = False
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key.lower() == "user-agent":
            applies = value == "*"
        elif applies and key.lower() == "disallow" and value and path.startswith(value):
            return True
    return False


def capture_page(raw_url: str, selected_page_type: str = "auto") -> dict[str, Any]:
    url = normalize_url(raw_url)
    user_agent = "PersonalGeoAeoAuditor/1.0 (+local evidence-grounded audit)"
    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=(5, 25), allow_redirects=True)
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower():
        raise ValueError(f"The URL did not return HTML ({content_type or 'unknown type'}).")

    html = _decode_response(response)
    soup = BeautifulSoup(html, "html.parser")
    final_url = response.url
    parsed_final = urlparse(final_url)
    origin = f"{parsed_final.scheme}://{parsed_final.netloc}"

    title = _text(soup.title)
    description = _meta(soup, "description")
    canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    canonical = urljoin(final_url, str(canonical_tag.get("href", ""))) if isinstance(canonical_tag, Tag) else ""
    robots_meta = " ".join(filter(None, [_meta(soup, "robots"), response.headers.get("x-robots-tag", "")])).lower()

    schema_objects, schema_errors = _schema_objects(soup)
    schema_types = _schema_types(schema_objects)

    links: list[dict[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(final_url, str(anchor.get("href", "")).strip())
        links.append({"url": href, "text": _text(anchor)})
    internal_links = [item for item in links if urlparse(item["url"]).netloc == parsed_final.netloc]
    external_links = [item for item in links if urlparse(item["url"]).scheme in {"http", "https"} and urlparse(item["url"]).netloc != parsed_final.netloc]
    link_texts = [item["text"] for item in links]
    generic_links = [text for text in link_texts if text.lower().strip() in {"click here", "here", "read more", "learn more", "more"}]

    images = soup.find_all("img")
    missing_alt = [image for image in images if image.get("alt") is None]
    empty_alt = [image for image in images if image.get("alt") == ""]
    lazy_images = [image for image in images if str(image.get("loading", "")).lower() == "lazy"]

    headings = [(int(tag.name[1]), _text(tag)) for tag in soup.find_all(re.compile(r"^h[1-6]$"))]
    heading_levels = [level for level, _ in headings]
    heading_jumps = sum(1 for before, after in zip(heading_levels, heading_levels[1:]) if after > before + 1)
    h1s = [text for level, text in headings if level == 1]
    h2s = [text for level, text in headings if level == 2]
    question_headings = [text for _, text in headings if text.rstrip().endswith("?")]

    visible = BeautifulSoup(html, "html.parser")
    for tag in visible(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    visible_text = " ".join(visible.stripped_strings)
    text_lower = visible_text.lower()
    words = re.findall(r"\b[\w’'-]+\b", visible_text)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", visible_text) if part.strip()]
    paragraphs = [_text(tag) for tag in visible.find_all("p") if _text(tag)]
    paragraph_words = [len(re.findall(r"\b[\w’'-]+\b", part)) for part in paragraphs]
    first_substantive = next((part for part in paragraphs if len(part.split()) >= 8), "")

    form_controls = visible.find_all(["input", "select", "textarea"])
    unlabeled_controls = []
    for control in form_controls:
        control_id = str(control.get("id", ""))
        has_label = bool(control_id and visible.find("label", attrs={"for": control_id}))
        if not (has_label or control.get("aria-label") or control.get("aria-labelledby") or control.get("type") == "hidden"):
            unlabeled_controls.append(control)
    unnamed_buttons = [button for button in visible.find_all("button") if not (_text(button) or button.get("aria-label"))]

    html_tag = soup.find("html")
    lang = str(html_tag.get("lang", "")).strip() if isinstance(html_tag, Tag) else ""
    viewport = _meta(soup, "viewport")
    charset_tag = soup.find("meta", charset=True)
    charset = str(charset_tag.get("charset", "")).strip() if isinstance(charset_tag, Tag) else ""
    if not charset:
        content_meta = soup.find("meta", attrs={"http-equiv": re.compile(r"content-type", re.I)})
        charset = str(content_meta.get("content", "")) if isinstance(content_meta, Tag) else ""

    robots_url = urljoin(origin, "/robots.txt")
    robots_status, robots_text = _supporting_file(robots_url, user_agent)
    sitemap_candidates = re.findall(r"(?im)^sitemap:\s*(\S+)", robots_text)
    sitemap_url = sitemap_candidates[0] if sitemap_candidates else urljoin(origin, "/sitemap.xml")
    sitemap_status, sitemap_text = _supporting_file(sitemap_url, user_agent)

    page_type = _page_type(selected_page_type, final_url, schema_types, visible_text)
    schema_names = [str(item.get("name", "")).strip() for item in schema_objects if item.get("name")]
    schema_descriptions = [str(item.get("description", "")).strip() for item in schema_objects if item.get("description")]
    social_hosts = {"facebook.com", "instagram.com", "linkedin.com", "youtube.com", "x.com", "twitter.com", "tiktok.com", "pinterest.com"}
    social_links = [item for item in external_links if any(host in urlparse(item["url"]).netloc.lower() for host in social_hosts)]

    return {
        "requested_url": url,
        "final_url": final_url,
        "origin": origin,
        "status_code": response.status_code,
        "redirects": len(response.history),
        "content_type": content_type,
        "headers": {key.lower(): value for key, value in response.headers.items()},
        "html": html,
        "title": title,
        "description": description,
        "canonical": canonical,
        "robots_meta": robots_meta,
        "robots_status": robots_status,
        "robots_text": robots_text,
        "robots_blocks": _robots_blocks(robots_text, parsed_final.path or "/"),
        "sitemap_url": sitemap_url,
        "sitemap_status": sitemap_status,
        "sitemap_looks_valid": "<urlset" in sitemap_text.lower() or "<sitemapindex" in sitemap_text.lower(),
        "schema_objects": schema_objects,
        "schema_types": schema_types,
        "schema_errors": schema_errors,
        "schema_names": schema_names,
        "schema_descriptions": schema_descriptions,
        "headings": headings,
        "h1s": h1s,
        "h2s": h2s,
        "heading_jumps": heading_jumps,
        "question_headings": question_headings,
        "visible_text": visible_text,
        "text_lower": text_lower,
        "word_count": len(words),
        "sentences": sentences,
        "paragraphs": paragraphs,
        "paragraph_words": paragraph_words,
        "first_substantive": first_substantive,
        "links": links,
        "internal_links": internal_links,
        "external_links": external_links,
        "generic_links": generic_links,
        "images": images,
        "missing_alt": missing_alt,
        "empty_alt": empty_alt,
        "lazy_images": lazy_images,
        "lang": lang,
        "viewport": viewport,
        "charset": charset,
        "forms": visible.find_all("form"),
        "unlabeled_controls": unlabeled_controls,
        "unnamed_buttons": unnamed_buttons,
        "lists": visible.find_all(["ul", "ol"]),
        "tables": visible.find_all("table"),
        "videos": visible.find_all(["video", "iframe"]),
        "landmarks": {name: len(visible.find_all(name)) for name in ("main", "nav", "header", "footer", "article", "aside")},
        "page_type": page_type,
        "social_links": social_links,
        "fetched_at": datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
    }


def _status(condition: bool, partial: bool = False) -> str:
    return "PASS" if condition else ("PARTIAL" if partial else "FAIL")


def evaluate(c: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def add(family: str, category: str, number: int, title: str, status: str, evidence: str,
            recommendation: str, severity: str = "MEDIUM", source: str = "ai") -> None:
        prefix = re.sub(r"[^A-Z]", "", category.upper())[:5]
        source_title, source_url = SOURCES[source]
        findings.append({
            "id": f"{family}-{prefix}-{number:03d}", "family": family, "category": category,
            "title": title, "status": status, "evidence": evidence, "recommendation": recommendation,
            "severity": severity, "weight": SEVERITY_WEIGHTS[severity],
            "source_title": source_title, "source_url": source_url,
        })

    text = c["text_lower"]
    types = c["schema_types"]
    page_type = c["page_type"]
    has_noindex = "noindex" in c["robots_meta"]
    has_nosnippet = "nosnippet" in c["robots_meta"]
    image_count = len(c["images"])
    meaningful_images = max(0, image_count - len(c["empty_alt"]))
    alt_coverage = (image_count - len(c["missing_alt"])) / image_count if image_count else 1.0
    avg_sentence = sum(len(re.findall(r"\b[\w’'-]+\b", s)) for s in c["sentences"]) / max(1, len(c["sentences"]))
    avg_paragraph = sum(c["paragraph_words"]) / max(1, len(c["paragraph_words"]))
    numeric_claims = re.findall(r"\b\d+(?:[.,]\d+)?(?:%|\+|\s+(?:years?|customers?|clients?|studies|locations?))?\b", c["visible_text"], re.I)
    has_attribution = bool(re.search(r"\b(according to|source:|study|research|report|survey|data from|cited by)\b", text))
    has_dates = bool(re.search(r"\b(?:19|20)\d{2}\b", c["visible_text"]))
    has_author = bool(re.search(r"\b(by|written by|author)\s+[A-Z][\w'-]+", c["visible_text"])) or bool(types & {"Person", "ProfilePage"})
    has_about = any(re.search(r"\b(about|company|who we are|our story)\b", item["text"], re.I) for item in c["internal_links"])
    has_contact = any(re.search(r"\b(contact|support|get in touch)\b", item["text"], re.I) for item in c["internal_links"])
    has_privacy = any(re.search(r"\b(privacy|terms|legal)\b", item["text"], re.I) for item in c["internal_links"])
    has_editorial = any(re.search(r"\b(editorial|corrections?|fact.check)\b", item["text"], re.I) for item in c["internal_links"])
    has_faq_text = bool(re.search(r"\b(frequently asked questions|faq)\b", text)) or len(c["question_headings"]) >= 2
    has_examples = bool(re.search(r"\b(for example|case study|example:|such as|we tested|we found)\b", text))
    has_comparison = bool(c["tables"]) or bool(re.search(r"\b(compare|comparison|versus|\bvs\.?\b|pros and cons|alternatives?)\b", text))
    has_procedure = bool(c["lists"]) and bool(re.search(r"\b(how to|steps?|first|next|then|finally)\b", text))
    has_questions = bool(c["question_headings"])
    has_definition = bool(re.search(r"\b[A-Z][\w -]{2,40}\s+(?:is|means|refers to)\s+", c["visible_text"]))
    has_reviews = bool(types & {"Review", "AggregateRating"}) or bool(re.search(r"\b(testimonials?|customer reviews?|rated \d|stars?)\b", text))
    has_certs = bool(re.search(r"\b(certified|certification|accredited|accreditation|licensed|award(?:ed|s)?)\b", text))
    has_org = bool(types & {"Organization", "Corporation", "LocalBusiness"})
    has_same_as = any(isinstance(item.get("sameAs"), (str, list)) for item in c["schema_objects"])
    has_main_entity = any("mainEntity" in item or "about" in item for item in c["schema_objects"])
    schema_text_matches = any(name.lower() in text for name in c["schema_names"] if len(name) > 2)
    meta_date = any(_meta(BeautifulSoup(c["html"], "html.parser"), key) for key in ("date", "article:published_time", "article:modified_time"))
    visible_date = has_dates or meta_date
    expected_schema = {
        "homepage": {"WebSite", "Organization"}, "article": {"Article", "BlogPosting", "NewsArticle"},
        "product": {"Product"}, "local": {"LocalBusiness"}, "about": {"AboutPage", "Organization", "ProfilePage"},
        "service": {"Service", "Organization"}, "webpage": {"WebPage"},
    }[page_type]
    page_schema_ok = bool(types & expected_schema)
    root_path = urlparse(c["final_url"]).path.rstrip("") in {"", "/"}
    non_home = page_type != "homepage" and not root_path

    # GEO: 7 categories x 8 rules = 56.
    cat = "Crawlability & Indexability"
    add("GEO", cat, 1, "Successful HTML response", _status(200 <= c["status_code"] < 300), f"HTTP {c['status_code']}; {c['content_type'] or 'content type missing'}.", "Return a stable 2xx HTML response for the canonical page.", "CRITICAL", "search")
    add("GEO", cat, 2, "HTTPS delivery", _status(urlparse(c["final_url"]).scheme == "https"), c["final_url"], "Serve and canonicalize the page over HTTPS.", "HIGH", "search")
    add("GEO", cat, 3, "Indexing permitted", _status(not has_noindex), c["robots_meta"] or "No noindex directive observed.", "Remove noindex only if this page is intended to appear in search.", "CRITICAL", "search")
    add("GEO", cat, 4, "robots.txt permits the page", "UNKNOWN" if c["robots_status"] is None else _status(not c["robots_blocks"]), f"robots.txt status: {c['robots_status'] or 'unreachable'}; blocked: {c['robots_blocks']}.", "Verify the intended crawler rules in robots.txt.", "HIGH", "search")
    add("GEO", cat, 5, "Canonical URL declared", "FAIL" if not c["canonical"] else _status(c["canonical"].rstrip("/") == c["final_url"].rstrip("/"), partial=True), c["canonical"] or "No canonical link observed.", "Declare the preferred canonical URL and ensure it matches the intended indexable page.", "HIGH", "seo")
    add("GEO", cat, 6, "XML sitemap discoverable", "UNKNOWN" if c["sitemap_status"] is None else _status(c["sitemap_status"] == 200 and c["sitemap_looks_valid"], partial=c["sitemap_status"] == 200), f"{c['sitemap_url']} returned {c['sitemap_status'] or 'no response'}.", "Publish a valid XML sitemap and declare it in robots.txt.", "MEDIUM", "search")
    add("GEO", cat, 7, "Substantive server-rendered text", _status(c["word_count"] >= 200, partial=c["word_count"] >= 80), f"{c['word_count']} visible words were present in the fetched HTML.", "Ensure the primary information is available in crawlable HTML; write for people rather than a fixed word count.", "HIGH", "ai")
    add("GEO", cat, 8, "Snippet generation permitted", _status(not has_nosnippet), c["robots_meta"] or "No nosnippet directive observed.", "Remove nosnippet only when search and AI-result excerpts are desired.", "HIGH", "ai")

    cat = "Entity Clarity & Identity"
    add("GEO", cat, 1, "Descriptive page title", _status(bool(c["title"]), partial=False), c["title"] or "No title observed.", "Write a unique title that identifies the page and its subject.", "HIGH", "seo")
    add("GEO", cat, 2, "Single primary heading", _status(len(c["h1s"]) == 1, partial=len(c["h1s"]) > 1), f"Found {len(c['h1s'])} H1 heading(s): {', '.join(c['h1s'][:3]) or 'none'}.", "Use one clear H1 for the page's primary subject.", "HIGH", "seo")
    add("GEO", cat, 3, "Organization or person identified", _status(has_org or "Person" in types, partial=bool(c["h1s"] or c["title"])), f"Entity schema types: {', '.join(sorted(types & {'Organization','Corporation','LocalBusiness','Person'})) or 'none'}.", "Identify the responsible organization or person visibly and, where appropriate, in structured data.", "HIGH", "organization")
    add("GEO", cat, 4, "About information reachable", _status(has_about, partial="about" in text), "An About/Company link was observed." if has_about else "No clear About/Company link was observed on this page.", "Link to a substantive About page that establishes identity and purpose.", "MEDIUM", "helpful")
    add("GEO", cat, 5, "Contact path reachable", _status(has_contact, partial=bool(re.search(r"\b(email|phone|call us)\b", text))), "A contact/support path was observed." if has_contact else "No clear contact or support path was observed.", "Provide an easy-to-find contact or support route.", "MEDIUM", "helpful")
    add("GEO", cat, 6, "Official profiles connected", _status(has_same_as or bool(c["social_links"]), partial=False), f"Observed {len(c['social_links'])} official-profile link(s); schema sameAs: {has_same_as}.", "Connect authoritative profiles with visible links and Organization/Person sameAs where appropriate.", "MEDIUM", "organization")
    add("GEO", cat, 7, "Page purpose summarized", _status(bool(c["description"]), partial=bool(c["first_substantive"])), c["description"] or c["first_substantive"][:220] or "No summary observed.", "Provide a concise, accurate summary of the page's purpose.", "MEDIUM", "seo")
    add("GEO", cat, 8, "Entity consistency across channels", "MANUAL_REVIEW", "A single-page crawl cannot verify cross-channel naming and factual consistency.", "Compare official website, business profiles, social profiles, directories, and product feeds.", "HIGH", "ai")

    cat = "Structured Data"
    add("GEO", cat, 1, "Machine-readable structured data present", _status(bool(c["schema_objects"])), f"Found {len(c['schema_objects'])} JSON-LD object(s).", "Add relevant structured data when it accurately represents visible page content; it is not a special AI requirement.", "MEDIUM", "schema")
    add("GEO", cat, 2, "JSON-LD parses successfully", "NOT_APPLICABLE" if not c["schema_objects"] and not c["schema_errors"] else _status(not c["schema_errors"]), "; ".join(c["schema_errors"]) or "All observed JSON-LD blocks parsed.", "Correct JSON syntax and validate the page's markup.", "HIGH", "schemaorg")
    add("GEO", cat, 3, "Web page or site type declared", _status(bool(types & {"WebPage", "WebSite", "AboutPage", "ProfilePage", "CollectionPage"}), partial=bool(types)), f"Observed types: {', '.join(sorted(types)) or 'none'}.", "Use a specific WebPage/WebSite subtype when it truthfully describes this page.", "MEDIUM", "schema")
    add("GEO", cat, 4, "Responsible entity structured", _status(has_org or "Person" in types, partial=bool(types)), f"Observed types: {', '.join(sorted(types & {'Organization','Corporation','LocalBusiness','Person'})) or 'none'}.", "Add appropriate Organization or Person data on the page where that entity is established.", "MEDIUM", "organization")
    add("GEO", cat, 5, "Page-type schema is appropriate", _status(page_schema_ok, partial=bool(types)), f"Detected page type: {page_type}; expected one of {', '.join(sorted(expected_schema))}; observed {', '.join(sorted(types)) or 'none'}.", "Use structured data that matches the visible page type and content.", "HIGH", "schema")
    add("GEO", cat, 6, "Breadcrumb markup on interior page", "NOT_APPLICABLE" if not non_home else _status("BreadcrumbList" in types, partial=bool(re.search(r"\bhome\s*[>/]", text))), "Homepage: breadcrumb not required." if not non_home else f"BreadcrumbList observed: {'BreadcrumbList' in types}.", "For an interior page, add visible breadcrumbs and matching BreadcrumbList markup when useful.", "LOW", "breadcrumb")
    add("GEO", cat, 7, "Primary subject relationship declared", _status(has_main_entity, partial=bool(types)), f"mainEntity/about relationship observed: {has_main_entity}.", "Connect the page to its primary subject with accurate mainEntity/about relationships when appropriate.", "MEDIUM", "schema")
    add("GEO", cat, 8, "Structured data matches visible content", "NOT_APPLICABLE" if not c["schema_objects"] else _status(schema_text_matches, partial=bool(c["schema_names"] or c["schema_descriptions"])), f"Schema names found in visible text: {schema_text_matches}; named schema values: {len(c['schema_names'])}.", "Ensure structured claims, names, descriptions, prices, and dates are also visible and consistent on the page.", "HIGH", "schema")

    cat = "Evidence & Citations"
    add("GEO", cat, 1, "External supporting sources", _status(len(c["external_links"]) >= 2, partial=len(c["external_links"]) == 1), f"Found {len(c['external_links'])} external HTTP link(s).", "Link important factual claims to authoritative first-party or primary sources where appropriate.", "HIGH", "helpful")
    add("GEO", cat, 2, "Quantified claims supported", "NOT_APPLICABLE" if not numeric_claims else _status(has_attribution and bool(c["external_links"]), partial=has_attribution or bool(c["external_links"])), f"Observed {len(numeric_claims)} numeric expression(s); attribution language: {has_attribution}; external links: {len(c['external_links'])}.", "Place a named, linked source beside material quantitative claims.", "HIGH", "helpful")
    add("GEO", cat, 3, "Source attribution language", _status(has_attribution, partial=bool(c["external_links"])), "Attribution language was observed." if has_attribution else "No explicit source-attribution language was observed.", "Name the source, study, dataset, or authority supporting important claims.", "MEDIUM", "helpful")
    add("GEO", cat, 4, "Dates support time-sensitive claims", "NOT_APPLICABLE" if page_type not in {"article", "product", "local"} else _status(visible_date), f"Visible or metadata date observed: {visible_date}.", "Show publication, update, price-validity, or availability dates when recency affects the answer.", "MEDIUM", "helpful")
    add("GEO", cat, 5, "Named author on editorial content", "NOT_APPLICABLE" if page_type != "article" else _status(has_author), f"Named author signal observed: {has_author}.", "Add a visible byline and connect it to an author profile for editorial content.", "HIGH", "article")
    add("GEO", cat, 6, "Author expertise is substantiated", "NOT_APPLICABLE" if page_type != "article" else "MANUAL_REVIEW", "Credentials require human verification against the author's real expertise.", "Verify that the byline, bio, experience, and subject matter genuinely align.", "HIGH", "helpful")
    add("GEO", cat, 7, "References or methodology section", _status(bool(re.search(r"\b(references|sources|methodology|how we tested|data sources)\b", text)), partial=has_attribution), "A references/methodology signal was observed." if re.search(r"\b(references|sources|methodology|how we tested|data sources)\b", text) else "No explicit references or methodology section was observed.", "For evidence-heavy pages, explain methods and collect sources in a clear section.", "MEDIUM", "helpful")
    add("GEO", cat, 8, "Claims are factually verified", "MANUAL_REVIEW", "HTML can expose claims and citations but cannot establish that every claim is true.", "Verify material claims against the cited primary evidence and record the review.", "CRITICAL", "helpful")

    cat = "Authority & Trust"
    add("GEO", cat, 1, "Business identity and history", _status(has_about, partial=bool(re.search(r"\b(founded|since \d{4}|years? of experience|our story)\b", text))), "About/history signal observed." if has_about else "No clearly linked business-history page was observed.", "Explain who is responsible for the site, its purpose, and relevant history.", "MEDIUM", "helpful")
    add("GEO", cat, 2, "Contact and accountability information", _status(has_contact), "Contact/support route observed." if has_contact else "No contact/support route observed.", "Publish a reliable way for users to contact the responsible organization.", "HIGH", "helpful")
    add("GEO", cat, 3, "Privacy and legal information", _status(has_privacy, partial=False), "Privacy/terms/legal link observed." if has_privacy else "No privacy, terms, or legal link observed on this page.", "Link appropriate privacy, terms, and legal policies from the site-wide navigation or footer.", "MEDIUM", "helpful")
    add("GEO", cat, 4, "Named experts or leadership", _status(has_author or bool(types & {"Person", "ProfilePage"}), partial=bool(re.search(r"\b(team|leadership|experts?|founder)\b", text))), f"Author/person signal: {has_author or bool(types & {'Person','ProfilePage'})}.", "Identify the qualified people responsible for important advice, claims, or company decisions.", "MEDIUM", "helpful")
    add("GEO", cat, 5, "Certifications are visible and verifiable", "NOT_APPLICABLE" if not has_certs else _status(has_certs and bool(c["external_links"]), partial=True), f"Certification/accreditation language: {has_certs}; external links: {len(c['external_links'])}.", "Link each material certification or award to its authoritative issuer or record.", "HIGH", "helpful")
    add("GEO", cat, 6, "Reviews and testimonials are attributable", "NOT_APPLICABLE" if not has_reviews else _status("Review" in types and bool(re.search(r"\b[A-Z][a-z]+\s+[A-Z]", c["visible_text"])), partial=True), f"Review/testimonial signal: {has_reviews}; Review schema: {'Review' in types}.", "Show authentic, attributable reviews and represent them accurately in eligible structured data.", "MEDIUM", "schema")
    add("GEO", cat, 7, "Editorial and correction standards", _status(has_editorial, partial=page_type != "article"), "Editorial/corrections policy link observed." if has_editorial else "No editorial or corrections policy link observed.", "For publishing sites, explain editorial standards, review, corrections, and conflicts.", "LOW", "helpful")
    add("GEO", cat, 8, "Independent reputation confirmed", "MANUAL_REVIEW", "A page cannot prove its own independent reputation.", "Review reputable independent coverage, references, business records, and expert recognition.", "HIGH", "helpful")

    cat = "Content Depth & Originality"
    expected_words = 500 if page_type == "article" else 250
    add("GEO", cat, 1, "Content depth fits the page purpose", _status(c["word_count"] >= expected_words, partial=c["word_count"] >= expected_words * 0.45), f"{c['word_count']} words; heuristic review threshold for {page_type}: {expected_words}. This is not a ranking minimum.", "Cover the user's task completely; do not pad content to meet a word count.", "HIGH", "helpful")
    add("GEO", cat, 2, "Primary topic is unambiguous", _status(bool(c["title"] and c["h1s"]), partial=bool(c["title"] or c["h1s"])), f"Title present: {bool(c['title'])}; H1 present: {bool(c['h1s'])}.", "Align the title, H1, introduction, and page purpose around one clear subject.", "HIGH", "helpful")
    add("GEO", cat, 3, "Subtopics use meaningful sections", _status(len(c["h2s"]) >= 2, partial=len(c["h2s"]) == 1), f"Found {len(c['h2s'])} H2 section heading(s).", "Use descriptive section headings where they help readers navigate substantive subtopics.", "MEDIUM", "seo")
    add("GEO", cat, 4, "Examples or first-hand evidence", _status(has_examples, partial=bool(re.search(r"\b(our|we|I)\b", c["visible_text"]))), "Examples/case-study/first-hand language was observed." if has_examples else "No clear example or case-study signal was observed.", "Add original examples, observations, tests, demonstrations, or case material when useful.", "HIGH", "helpful")
    add("GEO", cat, 5, "Comparisons are structured", _status(has_comparison and bool(c["tables"]), partial=has_comparison), f"Comparison signal: {has_comparison}; tables: {len(c['tables'])}.", "Present genuine comparisons with explicit criteria, evidence, and a scannable structure.", "MEDIUM", "helpful")
    add("GEO", cat, 6, "Common follow-up questions addressed", _status(has_faq_text, partial=has_questions), f"FAQ signal: {has_faq_text}; question headings: {len(c['question_headings'])}.", "Address real follow-up questions where they naturally belong; do not add boilerplate FAQs solely for search.", "MEDIUM", "ai")
    add("GEO", cat, 7, "Relevant media supports the text", _status(meaningful_images > 0 or bool(c["videos"]), partial=image_count > 0), f"Images: {image_count}; images with non-empty alt: {meaningful_images}; video/iframe elements: {len(c['videos'])}.", "Use original, relevant images or video where they materially help the audience.", "LOW", "ai")
    add("GEO", cat, 8, "Originality and added value", "MANUAL_REVIEW", "Originality cannot be established from one page without comparison and subject review.", "Confirm that the page adds experience, analysis, data, or utility beyond commodity summaries.", "CRITICAL", "helpful")

    cat = "Technical Delivery & Consistency"
    add("GEO", cat, 1, "Redirect chain is limited", _status(c["redirects"] <= 1, partial=c["redirects"] <= 3), f"Observed {c['redirects']} redirect(s).", "Point internal links and canonical signals directly to the final URL.", "MEDIUM", "seo")
    add("GEO", cat, 2, "Character encoding declared", _status(bool(c["charset"])), c["charset"] or "No charset declaration observed.", "Declare UTF-8 early in the document head.", "MEDIUM", "wcag")
    add("GEO", cat, 3, "Mobile viewport declared", _status(bool(c["viewport"])), c["viewport"] or "No viewport meta tag observed.", "Declare a responsive viewport and verify mobile rendering.", "HIGH", "seo")
    compression = c["headers"].get("content-encoding", "")
    add("GEO", cat, 4, "Transfer compression observed", _status(bool(compression), partial=False), compression or "No Content-Encoding header observed in this response.", "Enable Brotli or gzip for compressible resources; verify at the CDN/origin.", "LOW", "seo")
    cache = c["headers"].get("cache-control", "")
    add("GEO", cat, 5, "Caching policy declared", _status(bool(cache)), cache or "No Cache-Control header observed.", "Set an intentional caching policy appropriate to HTML and static assets.", "LOW", "seo")
    hsts = c["headers"].get("strict-transport-security", "")
    add("GEO", cat, 6, "HSTS on HTTPS", "NOT_APPLICABLE" if urlparse(c["final_url"]).scheme != "https" else _status(bool(hsts)), hsts or "No Strict-Transport-Security header observed.", "Enable HSTS after confirming the complete site works over HTTPS.", "LOW", "search")
    add("GEO", cat, 7, "Image text alternatives", _status(alt_coverage == 1.0, partial=alt_coverage >= 0.8), f"{image_count - len(c['missing_alt'])} of {image_count} image(s) include an alt attribute; empty alt may be correct for decorative images.", "Give informative images useful alt text and decorative images an empty alt attribute.", "HIGH", "wcag")
    add("GEO", cat, 8, "Internal discovery paths", _status(len(c["internal_links"]) >= 5, partial=len(c["internal_links"]) >= 1), f"Found {len(c['internal_links'])} internal link(s).", "Link important related pages with descriptive anchor text and a clear hierarchy.", "HIGH", "seo")

    # AEO: 6 categories x 10 rules = 60.
    cat = "Question & Intent Alignment"
    add("AEO", cat, 1, "Question-led headings", _status(len(c["question_headings"]) >= 2, partial=len(c["question_headings"]) == 1), f"Found {len(c['question_headings'])} question heading(s).", "Use real audience questions as headings when the section directly answers them.", "MEDIUM", "ai")
    add("AEO", cat, 2, "Answers follow question headings", "NOT_APPLICABLE" if not c["question_headings"] else "MANUAL_REVIEW", "The HTML exposes question headings; answer accuracy and adjacency need contextual review.", "Confirm that each question is followed immediately by a complete, accurate answer.", "HIGH", "helpful")
    add("AEO", cat, 3, "Opening provides a direct orientation", _status(20 <= len(c["first_substantive"].split()) <= 120, partial=bool(c["first_substantive"])), f"First substantive paragraph contains {len(c['first_substantive'].split())} words.", "Open with a direct orientation or answer before supporting detail when that serves the user.", "HIGH", "helpful")
    question_words = {word for word in ("what", "how", "why", "when", "where", "who", "which", "can", "does") if re.search(rf"\b{word}\b", " ".join(c["question_headings"]), re.I)}
    add("AEO", cat, 4, "Multiple relevant question forms", _status(len(question_words) >= 3, partial=len(question_words) >= 1), f"Question forms observed: {', '.join(sorted(question_words)) or 'none'}.", "Cover the distinct questions users actually ask; avoid synthetic question stuffing.", "LOW", "ai")
    add("AEO", cat, 5, "FAQ content is visible", _status(has_faq_text, partial=has_questions), f"FAQ section signal: {has_faq_text}.", "Add a focused FAQ only when it helps users complete the page's task.", "MEDIUM", "helpful")
    add("AEO", cat, 6, "FAQ markup matches visible FAQ", "NOT_APPLICABLE" if not has_faq_text else _status("FAQPage" in types, partial=bool(types)), f"Visible FAQ: {has_faq_text}; FAQPage type: {'FAQPage' in types}.", "If eligible and useful, ensure FAQ markup exactly represents visible questions and answers; do not expect an AI-ranking benefit.", "LOW", "schema")
    add("AEO", cat, 7, "Primary user intent is satisfied", "MANUAL_REVIEW", "Intent satisfaction requires the target query, audience, and subject review.", "Define the target task or question and verify that the page completes it without forcing another search.", "CRITICAL", "helpful")
    add("AEO", cat, 8, "Comparison intent supported", _status(has_comparison, partial=False), f"Comparison/table signal: {has_comparison}.", "Where users must choose, compare alternatives using explicit, fair criteria.", "MEDIUM", "helpful")
    add("AEO", cat, 9, "Procedural intent supported", _status(has_procedure, partial=bool(c["lists"])), f"Procedure language: {has_procedure}; lists: {len(c['lists'])}.", "For how-to intent, use complete ordered steps with prerequisites, cautions, and expected results.", "MEDIUM", "helpful")
    transactional_re = r"(?:[$€£]\s?\d|\bprice\b|\bin stock\b|\bbook now\b|\bbuy\b|\bcall\b|\bhours\b)"
    transactional_status = "NOT_APPLICABLE" if page_type not in {"product", "service", "local"} else _status(bool(re.search(transactional_re, text, re.I)), partial=has_contact)
    add("AEO", cat, 10, "Transactional facts are explicit", transactional_status, f"Page type: {page_type}; price/availability/action signal: {bool(re.search(transactional_re, text, re.I))}.", "State the decision-critical facts—such as price, availability, hours, location, or next action—clearly on relevant pages.", "HIGH", "helpful")

    cat = "Answer Extractability"
    add("AEO", cat, 1, "Paragraphs are scannable", _status(avg_paragraph <= 100, partial=avg_paragraph <= 160), f"Average paragraph length: {avg_paragraph:.1f} words across {len(c['paragraphs'])} paragraph(s).", "Break dense passages where doing so improves comprehension; keep related reasoning together.", "MEDIUM", "helpful")
    add("AEO", cat, 2, "Lists expose grouped facts or steps", _status(len(c["lists"]) >= 1), f"Found {len(c['lists'])} list(s).", "Use semantic lists for genuine sets, steps, requirements, or options.", "MEDIUM", "wcag")
    add("AEO", cat, 3, "Tables expose comparable data", _status(len(c["tables"]) >= 1, partial=has_comparison), f"Found {len(c['tables'])} table(s).", "Use an accessible table when readers need exact row-and-column comparison.", "LOW", "wcag")
    add("AEO", cat, 4, "Definitions are explicit", _status(has_definition), "Definition pattern observed." if has_definition else "No explicit ‘X is/means/refers to’ definition pattern was observed.", "Define unfamiliar central terms directly when the audience needs the definition.", "MEDIUM", "helpful")
    add("AEO", cat, 5, "Key facts are concrete", _status(len(numeric_claims) >= 2, partial=len(numeric_claims) == 1), f"Observed {len(numeric_claims)} numeric expression(s).", "Use specific facts, quantities, dates, limits, and conditions when they improve accuracy.", "MEDIUM", "helpful")
    add("AEO", cat, 6, "Claims retain attribution context", _status(has_attribution and bool(c["external_links"]), partial=has_attribution or bool(c["external_links"])), f"Attribution language: {has_attribution}; external links: {len(c['external_links'])}.", "Keep source names and links close to the claims they support.", "HIGH", "helpful")
    answer_blocks = sum(1 for level, heading in c["headings"] if level <= 3 and heading and any(len(p.split()) >= 10 for p in c["paragraphs"]))
    add("AEO", cat, 7, "Headed answer blocks", _status(len(c["h2s"]) >= 2 and bool(c["paragraphs"]), partial=bool(c["headings"] and c["paragraphs"])), f"Headings: {len(c['headings'])}; paragraphs: {len(c['paragraphs'])}; potential blocks: {answer_blocks}.", "Pair descriptive headings with self-contained explanatory text.", "HIGH", "ai")
    id_headings = sum(1 for tag in BeautifulSoup(c["html"], "html.parser").find_all(re.compile(r"^h[1-6]$")) if tag.get("id"))
    add("AEO", cat, 8, "Sections support fragment linking", _status(id_headings >= max(1, len(c["headings"]) // 2), partial=id_headings > 0), f"{id_headings} of {len(c['headings'])} heading(s) have IDs.", "Add stable IDs to useful sections so users and systems can link directly to them.", "LOW", "seo")
    add("AEO", cat, 9, "Primary information is visible text", _status(c["word_count"] >= 100, partial=c["word_count"] >= 40), f"Extracted {c['word_count']} visible words from HTML.", "Keep essential answers in selectable, crawlable text rather than only in images, video, or widgets.", "HIGH", "ai")
    add("AEO", cat, 10, "Answer completeness and accuracy", "MANUAL_REVIEW", "Completeness and correctness cannot be established by structural extraction alone.", "Review each central answer for correctness, necessary qualifications, and missing decision-critical facts.", "CRITICAL", "helpful")

    cat = "Semantic Structure"
    add("AEO", cat, 1, "One clear H1", _status(len(c["h1s"]) == 1, partial=len(c["h1s"]) > 1), f"Found {len(c['h1s'])} H1 heading(s).", "Use one clear page-level heading.", "HIGH", "wcag")
    add("AEO", cat, 2, "Heading hierarchy is sequential", _status(c["heading_jumps"] == 0), f"Observed {c['heading_jumps']} skipped heading-level transition(s).", "Use heading levels to represent hierarchy, not visual size.", "MEDIUM", "wcag")
    add("AEO", cat, 3, "Sections use H2 headings", _status(len(c["h2s"]) >= 2, partial=len(c["h2s"]) == 1), f"Found {len(c['h2s'])} H2 heading(s).", "Give major sections concise, descriptive H2 headings.", "MEDIUM", "wcag")
    add("AEO", cat, 4, "Semantic landmarks are present", _status(c["landmarks"]["main"] >= 1 and c["landmarks"]["nav"] >= 1, partial=c["landmarks"]["main"] >= 1), f"Landmarks: {c['landmarks']}.", "Use main, nav, header, footer, article, and aside according to their semantic roles.", "MEDIUM", "wcag")
    add("AEO", cat, 5, "Link text is descriptive", _status(not c["generic_links"], partial=len(c["generic_links"]) <= 2), f"Found {len(c['generic_links'])} generic link label(s): {', '.join(c['generic_links'][:5]) or 'none'}.", "Replace ambiguous labels such as ‘click here’ with link text that names the destination or action.", "MEDIUM", "wcag")
    add("AEO", cat, 6, "Canonical relationship is explicit", _status(bool(c["canonical"]), partial=False), c["canonical"] or "No canonical link observed.", "Declare the page's preferred canonical URL.", "MEDIUM", "seo")
    add("AEO", cat, 7, "Document language is declared", _status(bool(c["lang"])), c["lang"] or "No html lang attribute observed.", "Declare the primary document language on the html element.", "HIGH", "wcag")
    add("AEO", cat, 8, "Interior hierarchy is visible", "NOT_APPLICABLE" if not non_home else _status("BreadcrumbList" in types, partial=bool(re.search(r"\bhome\s*[>/]", text))), "Homepage: interior hierarchy not applicable." if not non_home else f"BreadcrumbList: {'BreadcrumbList' in types}.", "Use useful visible breadcrumbs on deeper pages and match them with structured data.", "LOW", "breadcrumb")
    add("AEO", cat, 9, "Related information is linked", _status(len(c["internal_links"]) >= 5, partial=len(c["internal_links"]) >= 1), f"Found {len(c['internal_links'])} internal link(s).", "Link relevant supporting, parent, and next-step pages in context.", "MEDIUM", "seo")
    add("AEO", cat, 10, "Informative images have text alternatives", _status(alt_coverage == 1.0, partial=alt_coverage >= 0.8), f"Alt-attribute coverage: {alt_coverage:.0%} across {image_count} image(s).", "Describe informative images; use empty alt for decorative images.", "HIGH", "wcag")

    cat = "Clarity & Readability"
    add("AEO", cat, 1, "Title is concise and descriptive", _status(15 <= len(c["title"]) <= 65, partial=bool(c["title"])), f"Title length: {len(c['title'])} characters; title: {c['title'] or 'missing'}.", "Write a concise unique title; treat length as a review heuristic, not a ranking rule.", "MEDIUM", "seo")
    add("AEO", cat, 2, "Meta description is useful", _status(70 <= len(c["description"]) <= 170, partial=bool(c["description"])), f"Description length: {len(c['description'])} characters.", "Write an accurate summary that helps a searcher understand why the page is useful.", "MEDIUM", "seo")
    add("AEO", cat, 3, "Sentence length supports comprehension", _status(avg_sentence <= 25, partial=avg_sentence <= 35), f"Average sentence length: {avg_sentence:.1f} words.", "Revise unnecessarily long sentences while preserving necessary nuance.", "MEDIUM", "helpful")
    add("AEO", cat, 4, "Paragraph length supports scanning", _status(avg_paragraph <= 100, partial=avg_paragraph <= 160), f"Average paragraph length: {avg_paragraph:.1f} words.", "Break up dense paragraphs where the ideas remain coherent.", "LOW", "helpful")
    acronyms = re.findall(r"\b[A-Z]{3,}\b", c["visible_text"])
    add("AEO", cat, 5, "Acronym density is controlled", _status(len(acronyms) <= max(3, c["word_count"] // 150), partial=len(acronyms) <= max(6, c["word_count"] // 80)), f"Observed {len(acronyms)} all-cap acronym token(s).", "Define unfamiliar acronyms on first use and remove unnecessary jargon.", "LOW", "helpful")
    add("AEO", cat, 6, "Headings are concise", _status(all(len(text.split()) <= 12 for _, text in c["headings"]), partial=all(len(text.split()) <= 18 for _, text in c["headings"])), f"Longest heading: {max((len(text.split()) for _, text in c['headings']), default=0)} words.", "Use headings that state the section subject without becoming paragraphs.", "LOW", "helpful")
    heading_texts = [heading.strip().lower() for _, heading in c["headings"] if heading.strip()]
    duplicates = len(heading_texts) - len(set(heading_texts))
    add("AEO", cat, 7, "Section headings are distinct", _status(duplicates == 0), f"Found {duplicates} duplicate heading occurrence(s).", "Give each section a distinct label that communicates its specific purpose.", "LOW", "wcag")
    add("AEO", cat, 8, "Important qualifications are visible", "MANUAL_REVIEW", "Automated text extraction cannot determine whether required caveats are complete or prominent.", "Make material conditions, limits, risks, and exceptions visible near the relevant claim.", "HIGH", "helpful")
    add("AEO", cat, 9, "Grammar and terminology are accurate", "MANUAL_REVIEW", "Reliable grammar and domain-terminology review requires language and subject context.", "Review for grammar, consistent terminology, ambiguity, and domain-specific errors.", "MEDIUM", "helpful")
    add("AEO", cat, 10, "Freshness is signaled where relevant", "NOT_APPLICABLE" if page_type not in {"article", "product", "local"} else _status(visible_date), f"Page type: {page_type}; date signal: {visible_date}.", "Show meaningful publish/update/effective dates when currentness changes the answer.", "MEDIUM", "helpful")

    cat = "Page Experience & Accessibility"
    add("AEO", cat, 1, "All images declare alt behavior", _status(alt_coverage == 1.0, partial=alt_coverage >= 0.8), f"{len(c['missing_alt'])} of {image_count} image(s) omit the alt attribute.", "Give every image either informative alt text or an intentional empty alt attribute.", "HIGH", "wcag")
    add("AEO", cat, 2, "Form controls have accessible names", _status(not c["unlabeled_controls"]), f"Found {len(c['unlabeled_controls'])} unlabeled form control(s).", "Associate every control with a visible label or an appropriate accessible name.", "HIGH", "wcag")
    add("AEO", cat, 3, "Buttons have accessible names", _status(not c["unnamed_buttons"]), f"Found {len(c['unnamed_buttons'])} unnamed button(s).", "Give every button a visible or accessible name describing its action.", "HIGH", "wcag")
    add("AEO", cat, 4, "Responsive viewport is declared", _status(bool(c["viewport"])), c["viewport"] or "No viewport meta tag observed.", "Declare and test a responsive viewport.", "HIGH", "wcag")
    add("AEO", cat, 5, "Page language is available to assistive technology", _status(bool(c["lang"])), c["lang"] or "No document language observed.", "Set a valid lang attribute on the html element.", "HIGH", "wcag")
    has_skip = any(re.search(r"\b(skip|main content)\b", item["text"], re.I) and urlparse(item["url"]).fragment for item in c["links"])
    add("AEO", cat, 6, "Skip navigation is offered", _status(has_skip, partial=c["landmarks"]["main"] >= 1), f"Skip link observed: {has_skip}; main landmark count: {c['landmarks']['main']}.", "Provide a keyboard-accessible skip link when repeated navigation precedes main content.", "MEDIUM", "wcag")
    add("AEO", cat, 7, "Heading order is programmatic", _status(c["heading_jumps"] == 0 and bool(c["headings"]), partial=bool(c["headings"])), f"Headings: {len(c['headings'])}; level jumps: {c['heading_jumps']}.", "Represent content hierarchy with semantic headings in a logical order.", "MEDIUM", "wcag")
    add("AEO", cat, 8, "Link purpose is understandable", _status(not c["generic_links"], partial=len(c["generic_links"]) <= 2), f"Generic link labels: {len(c['generic_links'])}.", "Use link labels that remain meaningful in context and when listed independently.", "MEDIUM", "wcag")
    add("AEO", cat, 9, "Color contrast is sufficient", "MANUAL_REVIEW", "Static HTML extraction does not calculate computed colors across states and backgrounds.", "Run an automated accessibility scan and manually review text, controls, focus, hover, and image text contrast.", "HIGH", "wcag")
    add("AEO", cat, 10, "Mobile interaction works", "MANUAL_REVIEW", "A source-only audit does not exercise responsive layout, touch targets, dialogs, or keyboard flow.", "Test the rendered page on mobile sizes and with keyboard and assistive technology.", "HIGH", "wcag")

    cat = "Search & Answer Eligibility"
    add("AEO", cat, 1, "Page is technically indexable", _status(200 <= c["status_code"] < 300 and not has_noindex and not c["robots_blocks"]), f"HTTP {c['status_code']}; noindex: {has_noindex}; robots block: {c['robots_blocks']}.", "Resolve status, robots, and noindex conflicts for pages intended to appear in search.", "CRITICAL", "search")
    add("AEO", cat, 2, "Search snippets are permitted", _status(not has_nosnippet), c["robots_meta"] or "No restrictive snippet directive observed.", "Allow snippets when you want the page to support search and AI-generated result excerpts.", "HIGH", "ai")
    add("AEO", cat, 3, "Relevant structured data is available", _status(bool(c["schema_objects"]), partial=False), f"JSON-LD objects: {len(c['schema_objects'])}.", "Add only accurate, relevant structured data; it is useful for supported search features but not required for generative AI search.", "MEDIUM", "ai")
    add("AEO", cat, 4, "Structured data is syntactically valid", "NOT_APPLICABLE" if not c["schema_objects"] and not c["schema_errors"] else _status(not c["schema_errors"]), "; ".join(c["schema_errors"]) or "Observed JSON-LD parsed.", "Fix JSON-LD syntax and validate with Schema.org and applicable rich-result tools.", "HIGH", "schemaorg")
    add("AEO", cat, 5, "Markup matches the page type", _status(page_schema_ok, partial=bool(types)), f"Page type: {page_type}; expected: {', '.join(sorted(expected_schema))}; observed: {', '.join(sorted(types)) or 'none'}.", "Use the most specific applicable type and follow the feature's current requirements.", "HIGH", "schema")
    required_schema_ok = any(item.get("@type") and (item.get("name") or item.get("headline")) for item in c["schema_objects"])
    add("AEO", cat, 6, "Core structured properties are populated", "NOT_APPLICABLE" if not c["schema_objects"] else _status(required_schema_ok, partial=bool(c["schema_objects"])), f"A typed object with name/headline was observed: {required_schema_ok}.", "Populate required and useful recommended properties with values visible on the page.", "HIGH", "schema")
    og_ok = bool(_property(BeautifulSoup(c["html"], "html.parser"), "og:title") and _property(BeautifulSoup(c["html"], "html.parser"), "og:description"))
    add("AEO", cat, 7, "Open Graph summary is complete", _status(og_ok, partial=bool(_property(BeautifulSoup(c["html"], "html.parser"), "og:title"))), f"Open Graph title and description both present: {og_ok}.", "Provide accurate Open Graph title, description, URL, and image for shared previews.", "LOW", "seo")
    twitter = _meta(BeautifulSoup(c["html"], "html.parser"), "twitter:card")
    add("AEO", cat, 8, "Social card metadata is declared", _status(bool(twitter), partial=og_ok), twitter or "No twitter:card value observed; Open Graph may still support previews.", "Declare appropriate social-card metadata and test representative previews.", "LOW", "seo")
    icon = BeautifulSoup(c["html"], "html.parser").find("link", rel=lambda value: value and any(part in str(value).lower() for part in ("icon", "shortcut")))
    add("AEO", cat, 9, "Site identity icon is declared", _status(bool(icon)), str(icon.get("href", "")) if isinstance(icon, Tag) else "No icon link observed.", "Declare a crawlable favicon and use consistent site identity assets.", "LOW", "seo")
    add("AEO", cat, 10, "Observed AI visibility is measured separately", "MANUAL_REVIEW", "Page readiness does not reveal whether a brand is actually cited for a defined query set.", "Run a separate, repeatable visibility study with fixed questions, systems, dates, locations, answers, and captured citations.", "CRITICAL", "ai")

    assert len([f for f in findings if f["family"] == "GEO"]) == 56
    assert len([f for f in findings if f["family"] == "AEO"]) == 60
    return findings


def summarize(findings: list[dict[str, Any]], family: str | None = None) -> dict[str, Any]:
    selected = [item for item in findings if family is None or item["family"] == family]
    counts = Counter(item["status"] for item in selected)
    evaluated = [item for item in selected if item["status"] in STATUS_VALUES]
    possible = sum(item["weight"] for item in evaluated)
    earned = sum(item["weight"] * STATUS_VALUES[item["status"]] for item in evaluated)
    score = round(100 * earned / possible) if possible else None
    eligible = len(selected) - counts["NOT_APPLICABLE"]
    coverage = round(100 * len(evaluated) / eligible) if eligible else 0
    return {
        "total": len(selected), "evaluated": len(evaluated), "score": score, "coverage": coverage,
        "pass": counts["PASS"], "partial": counts["PARTIAL"], "fail": counts["FAIL"],
        "unknown": counts["UNKNOWN"], "manual": counts["MANUAL_REVIEW"], "not_applicable": counts["NOT_APPLICABLE"],
    }


def build_report(raw_url: str, selected_page_type: str = "auto") -> dict[str, Any]:
    capture = capture_page(raw_url, selected_page_type)
    findings = evaluate(capture)
    sections: list[dict[str, Any]] = []
    for family in ("GEO", "AEO"):
        categories: list[dict[str, Any]] = []
        names = list(dict.fromkeys(item["category"] for item in findings if item["family"] == family))
        for name in names:
            category_findings = [item for item in findings if item["family"] == family and item["category"] == name]
            categories.append({"name": name, "findings": category_findings, "summary": summarize(category_findings)})
        sections.append({"family": family, "categories": categories, "summary": summarize(findings, family)})

    combined = summarize(findings)
    score = combined["score"] or 0
    compliance = "Strong foundation" if score >= 85 else "Good foundation" if score >= 70 else "Needs improvement" if score >= 50 else "Needs significant improvement"
    status_rank = {"FAIL": 0, "PARTIAL": 1}
    priorities = sorted(
        (item for item in findings if item["status"] in status_rank and item["severity"] in {"CRITICAL", "HIGH"}),
        key=lambda item: (-item["weight"], status_rank[item["status"]], item["id"]),
    )[:12]
    strengths = sorted(
        (item for item in findings if item["status"] == "PASS"),
        key=lambda item: (-item["weight"], item["id"]),
    )[:8]
    review_items = [item for item in findings if item["status"] in {"UNKNOWN", "MANUAL_REVIEW"}]
    return {
        "capture": capture, "findings": findings, "sections": sections,
        "geo": summarize(findings, "GEO"), "aeo": summarize(findings, "AEO"), "combined": combined,
        "compliance": compliance, "priorities": priorities, "strengths": strengths,
        "review_items": review_items, "sources": [{"title": title, "url": url} for title, url in dict.fromkeys(SOURCES.values())],
    }
