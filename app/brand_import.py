"""Polite importer for perfume product pages on brand websites.

- Identifies itself with a clear User-Agent and honours robots.txt.
- Waits between requests to the same site.
- Refuses fragrance-database sites whose terms or bot rules don't allow copying (Fragrantica, Parfumo).
- Reads note lists with simple pattern matching; if that fails and an OpenRouter key is set, asks the AI
  model to read the page text instead.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urlparse

from app import db

USER_AGENT = f"SniffyNoseBot/0.1 (+{os.environ.get('SNIFFY_CONTACT', 'contact: see site')})"
BLOCKED_DOMAINS = ("fragrantica.", "parfumo.")
DELAY_SECONDS = 10
MAX_PAGE_CHARS = 60_000

_last_hit: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


class ImportError_(Exception):
    pass


# ---------- fetching ----------

def _check_allowed(url: str) -> None:
    host = urlparse(url).netloc.lower()
    if any(b in host for b in BLOCKED_DOMAINS):
        raise ImportError_(f"{host} doesn't allow copying its database. Link to it instead of importing it.")
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
        try:
            req = urllib.request.Request(rp.url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as r:
                rp.parse(r.read().decode("utf-8", "replace").splitlines())
        except Exception:
            rp.parse([])  # no robots.txt -> everything allowed
        _robots[host] = rp
    if not _robots[host].can_fetch(USER_AGENT, url):
        raise ImportError_(f"{host}'s robots.txt doesn't allow bots on this page, so it was skipped.")


def fetch(url: str) -> str:
    _check_allowed(url)
    host = urlparse(url).netloc.lower()
    wait = DELAY_SECONDS - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise ImportError_(f"The site answered {e.code} for {url}. If it blocks bots, add this perfume by hand.")
    finally:
        _last_hit[host] = time.time()
    return html


# ---------- parsing ----------

class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "dt", "dd"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
        self.jsonld: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_jsonld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("type") == "application/ld+json":
            self._in_jsonld = True
        elif tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        elif tag == "meta" and a.get("property", a.get("name")) in ("og:title", "og:site_name", "og:description"):
            self.meta[a.get("property", a.get("name"))] = a.get("content", "")
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
        elif tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_jsonld:
            self.jsonld.append(data)
        elif not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        return re.sub(r"\n\s*\n+", "\n", t).strip()


LEVEL_LABELS = {
    "top": r"top|head|opening|tête",
    "heart": r"heart|middle|coeur|cœur",
    "base": r"base|bottom|dry[- ]?down|fond",
}
_LABEL = r"(?P<label>{alts})\s*(?:notes?|accords?)?\s*[:\-–—]\s*"


def _split_notes(chunk: str) -> list[str]:
    chunk = re.split(r"[.\n]", chunk)[0]
    parts = re.split(r",|;|•|·|\band\b|\bet\b|&|/", chunk)
    notes = []
    for p in parts:
        p = p.strip(" -–—*\t").strip()
        if 1 < len(p) <= 40 and not re.search(r"\d{3,}|http", p):
            notes.append(p[:1].upper() + p[1:])
    return notes


def parse_pyramid(text: str) -> dict[str, list[str]]:
    """Find 'Top notes: a, b, c' style lists in page text."""
    found: dict[str, list[str]] = {}
    for level, alts in LEVEL_LABELS.items():
        m = re.search(_LABEL.format(alts=alts) + r"(?P<rest>[^\n]{2,300})", text, re.IGNORECASE)
        if m:
            rest = re.split(r"(?:" + "|".join(LEVEL_LABELS.values()) + r")\s*(?:notes?|accords?)\s*[:\-–—]",
                            m.group("rest"), flags=re.IGNORECASE)[0]
            notes = _split_notes(rest)
            if notes:
                found[level] = notes
    return found


def _jsonld_product(blobs: list[str]) -> dict:
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and "Product" in str(item.get("@type", "")):
                brand = item.get("brand")
                brand = brand.get("name", "") if isinstance(brand, dict) else (brand or "")
                return {"name": item.get("name", ""), "brand": brand, "description": item.get("description", "")}
    return {}


# ---------- import ----------

def import_url(url: str, brand: str = "", name: str = "", concentration: str = "",
               use_ai: bool = True, dry_run: bool = False) -> dict:
    html = fetch(url)
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.text()
    product = _jsonld_product(parser.jsonld)

    pyramid = parse_pyramid(text)
    method = "pattern"
    extracted: dict = {}
    if len(pyramid) < 2 and use_ai:
        from app import lookup
        if lookup.has_credentials():
            page = text[:MAX_PAGE_CHARS]
            if len(text) > MAX_PAGE_CHARS:
                print(f"  note: page text is long; only the first {MAX_PAGE_CHARS:,} characters were read")
            try:
                extracted = lookup.extract_from_page(page, url, f"{brand} {name}".strip())
            except lookup.LookupFailed as e:
                raise ImportError_(f"The AI couldn't read {url}: {e}")
            if extracted.get("found"):
                pyramid = {lvl: [n["name"] for n in extracted.get(lvl, [])] for lvl in db.LEVELS}
                method = "ai"
    if not any(pyramid.values()):
        raise ImportError_(f"Couldn't find a note list on {url}. Add this perfume by hand (CSV import).")

    perfume = {
        "brand": brand or extracted.get("brand") or product.get("brand") or parser.meta.get("og:site_name", ""),
        "name": name or extracted.get("name") or product.get("name") or parser.meta.get("og:title", ""),
        "concentration": concentration or extracted.get("concentration", ""),
        "description": extracted.get("description") or "",
        "families": extracted.get("families") or [],
        "year": extracted.get("year") or "",
        "perfumer": extracted.get("perfumer") or "",
        "flat_pyramid": bool(pyramid.get("heart")) and not pyramid.get("top") and not pyramid.get("base"),
        "brand_url": url,
    }
    if not perfume["brand"] or not perfume["name"]:
        raise ImportError_("Couldn't tell the brand or perfume name from the page. Pass --brand and --name.")

    perfume = merge_into_existing(perfume, pyramid, "brand")
    perfume["method"] = method
    if not dry_run:
        db.upsert_perfume(perfume, origin="brand-import", source_urls={"brand": url})
    return perfume


def merge_into_existing(perfume: dict, pyramid: dict[str, list[str]], source: str) -> dict:
    """Combine new notes with what's already stored, so each source's notes are kept and tagged."""
    existing = db.get_by_slug(db.perfume_slug(perfume["brand"], perfume["name"], perfume.get("concentration", "")))
    if existing:
        for key, value in perfume.items():
            if not value and existing.get(key):
                perfume[key] = existing[key]
        perfume["aliases"] = existing.get("aliases", [])
        perfume["fragrantica_url"] = existing.get("fragrantica_url", "")
        perfume["source_note"] = existing.get("source_note", "")
    for level in db.LEVELS:
        notes = [dict(n) for n in (existing or {}).get(level, [])]
        # A re-import replaces this source's old claims instead of piling up stale ones.
        for n in notes:
            n["sources"] = [s for s in n["sources"] if s != source]
        by_name = {n["name"].lower(): n for n in notes}
        for name in pyramid.get(level, []):
            if name.lower() in by_name:
                by_name[name.lower()]["sources"].append(source)
            else:
                n = {"name": name, "sources": [source]}
                notes.append(n)
                by_name[name.lower()] = n
        perfume[level] = [n for n in notes if n["sources"]]
    return perfume
