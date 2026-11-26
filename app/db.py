"""Local perfume database (SQLite): storage, upserts and typo-tolerant search."""

import difflib
import json
import os
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("SNIFFY_DB", Path(__file__).parent / "data" / "perfumes.db"))
LEVELS = ("top", "heart", "base")
SOURCES = ("brand", "fragrantica", "retailer", "curated")

SCHEMA = """
CREATE TABLE IF NOT EXISTS perfumes (
    id              INTEGER PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    brand           TEXT NOT NULL,
    aliases         TEXT NOT NULL DEFAULT '[]',     -- JSON list of other names people search for
    year            TEXT NOT NULL DEFAULT '',
    perfumer        TEXT NOT NULL DEFAULT '',
    concentration   TEXT NOT NULL DEFAULT '',
    families        TEXT NOT NULL DEFAULT '[]',     -- JSON list
    description     TEXT NOT NULL DEFAULT '',
    flat_pyramid    INTEGER NOT NULL DEFAULT 0,
    brand_url       TEXT NOT NULL DEFAULT '',
    fragrantica_url TEXT NOT NULL DEFAULT '',
    source_note     TEXT NOT NULL DEFAULT '',
    origin          TEXT NOT NULL DEFAULT 'curated', -- curated | brand-import | web-lookup
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS perfume_notes (
    perfume_id INTEGER NOT NULL REFERENCES perfumes(id) ON DELETE CASCADE,
    level      TEXT NOT NULL CHECK (level IN ('top', 'heart', 'base')),
    position   INTEGER NOT NULL,
    note       TEXT NOT NULL,
    sources    TEXT NOT NULL DEFAULT '[]',          -- JSON list, values from SOURCES
    PRIMARY KEY (perfume_id, level, note)
);

CREATE TABLE IF NOT EXISTS perfume_sources (
    perfume_id INTEGER NOT NULL REFERENCES perfumes(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    url        TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (perfume_id, kind, url)
);

-- Searches that didn't find a confident match: the to-do list visitors write for us.
CREATE TABLE IF NOT EXISTS search_log (
    query_key  TEXT PRIMARY KEY,                   -- slugified query, so "Dior Sauvage" = "dior  sauvage"
    query      TEXT NOT NULL,                      -- latest spelling typed
    count      INTEGER NOT NULL DEFAULT 1,
    best_score REAL NOT NULL DEFAULT 0,            -- typo_score(): high means probably a typo of a stored one
    status     TEXT NOT NULL DEFAULT 'pending',    -- pending | added | not_found
    slug       TEXT NOT NULL DEFAULT '',           -- the perfume it turned into, once added
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS perfumes_fts USING fts5(
    name, brand, aliases, tokenize = 'unicode61 remove_diacritics 2'
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_names: dict[int, dict] | None = None  # per perfume: name strings and words, for matching searches


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Letters that don't break down into a plain letter + accent ("Cœur" -> "coeur", Vietnamese "Đào" -> "dao").
_LETTERS = str.maketrans({"œ": "oe", "Œ": "Oe", "æ": "ae", "Æ": "Ae", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ß": "ss"})


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_LETTERS)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def perfume_slug(brand: str, name: str, concentration: str = "") -> str:
    return slugify(f"{brand} {name} {concentration}")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.executescript(SCHEMA)
    return _conn


def _invalidate() -> None:
    global _names
    _names = None


# ---------- writes ----------

def upsert_perfume(p: dict, origin: str, source_urls: dict[str, str] | None = None) -> int:
    """Insert or update a perfume and replace its notes. `p` uses the API shape: top/heart/base are
    lists of {"name", "sources"} (or plain strings). Returns the perfume id."""
    brand, name = (p.get("brand") or "").strip(), (p.get("name") or "").strip()
    if not brand or not name:
        raise ValueError("A perfume needs both a brand and a name.")
    concentration = (p.get("concentration") or "").strip()
    slug = perfume_slug(brand, name, concentration)
    stamp = now()
    fields = {
        "slug": slug, "name": name, "brand": brand, "concentration": concentration,
        "aliases": json.dumps(p.get("aliases") or [], ensure_ascii=False),
        "year": str(p.get("year") or ""), "perfumer": p.get("perfumer") or "",
        "families": json.dumps(p.get("families") or [], ensure_ascii=False),
        "description": p.get("description") or "", "flat_pyramid": int(bool(p.get("flat_pyramid"))),
        "brand_url": p.get("brand_url") or "", "fragrantica_url": p.get("fragrantica_url") or "",
        "source_note": p.get("source_note") or "", "origin": origin, "updated_at": stamp,
    }
    c = conn()
    with _lock, c:
        row = c.execute("SELECT id FROM perfumes WHERE slug = ?", (slug,)).fetchone()
        if row:
            pid = row["id"]
            c.execute(f"UPDATE perfumes SET {', '.join(f'{k} = :{k}' for k in fields)} WHERE id = :id",
                      {**fields, "id": pid})
            c.execute("DELETE FROM perfume_notes WHERE perfume_id = ?", (pid,))
        else:
            fields["created_at"] = stamp
            pid = c.execute(f"INSERT INTO perfumes ({', '.join(fields)}) VALUES ({', '.join(':' + k for k in fields)})",
                            fields).lastrowid

        for level in LEVELS:
            seen = set()
            for i, note in enumerate(p.get(level) or []):
                note_name = (note.get("name") if isinstance(note, dict) else str(note)).strip()
                if not note_name or note_name.lower() in seen:
                    continue
                seen.add(note_name.lower())
                sources = note.get("sources", []) if isinstance(note, dict) else []
                sources = [s for s in sources if s in SOURCES] or ["curated"]
                c.execute("INSERT INTO perfume_notes (perfume_id, level, position, note, sources) VALUES (?, ?, ?, ?, ?)",
                          (pid, level, i, note_name, json.dumps(sources)))

        for kind, url in (source_urls or {}).items():
            c.execute("INSERT OR REPLACE INTO perfume_sources (perfume_id, kind, url, fetched_at) VALUES (?, ?, ?, ?)",
                      (pid, kind, url or "", stamp))

        c.execute("DELETE FROM perfumes_fts WHERE rowid = ?", (pid,))
        c.execute("INSERT INTO perfumes_fts (rowid, name, brand, aliases) VALUES (?, ?, ?, ?)",
                  (pid, name, brand, " ".join(json.loads(fields["aliases"]))))
    _invalidate()
    return pid


def save_web_lookup(result: dict) -> tuple[int, bool]:
    """Store a validated web-lookup result unless the perfume is already stored (our copy wins).
    Returns (perfume id, whether it was newly added). Raises ValueError for incomplete records."""
    existing = get_by_slug(perfume_slug(result["brand"], result["name"], result.get("concentration", "")))
    if existing:
        return existing["id"], False
    urls = {"brand": result.get("brand_url", ""), "fragrantica": result.get("fragrantica_url", "")}
    return upsert_perfume(result, origin="web-lookup", source_urls={k: v for k, v in urls.items() if v}), True


def delete_perfume(slug: str) -> bool:
    c = conn()
    with _lock, c:
        row = c.execute("SELECT id FROM perfumes WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM perfumes_fts WHERE rowid = ?", (row["id"],))
        c.execute("DELETE FROM perfumes WHERE id = ?", (row["id"],))
    _invalidate()
    return True


# ---------- reads ----------

def get_perfume(pid: int) -> dict | None:
    c = conn()
    r = c.execute("SELECT * FROM perfumes WHERE id = ?", (pid,)).fetchone()
    if not r:
        return None
    p = dict(r)
    p["aliases"] = json.loads(p["aliases"])
    p["families"] = json.loads(p["families"])
    p["flat_pyramid"] = bool(p["flat_pyramid"])
    p["found"] = True
    for level in LEVELS:
        p[level] = [{"name": n["note"], "sources": json.loads(n["sources"])} for n in c.execute(
            "SELECT note, sources FROM perfume_notes WHERE perfume_id = ? AND level = ? ORDER BY position",
            (pid, level))]
    p["provenance"] = [dict(s) for s in c.execute(
        "SELECT kind, url, fetched_at FROM perfume_sources WHERE perfume_id = ? ORDER BY fetched_at DESC", (pid,))]
    return p


def get_by_slug(slug: str) -> dict | None:
    r = conn().execute("SELECT id FROM perfumes WHERE slug = ?", (slug,)).fetchone()
    return get_perfume(r["id"]) if r else None


# ---------- search log (visitor-driven queue) ----------

def log_miss(query: str, best_score: float = 0.0) -> None:
    key = slugify(query)
    if len(key) < 2:
        return
    stamp = now()
    c = conn()
    with _lock, c:
        c.execute(
            """INSERT INTO search_log (query_key, query, best_score, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(query_key) DO UPDATE SET
                 count = count + 1, query = excluded.query, last_seen = excluded.last_seen,
                 best_score = excluded.best_score""",
            (key, query.strip()[:120], best_score, stamp, stamp))


def mark_search(query: str, status: str, slug: str = "") -> None:
    c = conn()
    with _lock, c:
        c.execute("UPDATE search_log SET status = ?, slug = ? WHERE query_key = ?", (status, slug, slugify(query)))


def search_queue(status: str | None = "pending", include_close: bool = False, limit: int = 50) -> list[dict]:
    """Most-requested logged searches first. Close matches (likely typos of a stored perfume) are left out
    unless asked for."""
    sql, args = "SELECT * FROM search_log WHERE 1 = 1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if not include_close:
        sql += " AND best_score < 0.6"
    sql += " ORDER BY count DESC, last_seen DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn().execute(sql, args)]


def exists(brand: str, name: str, concentration: str = "") -> bool:
    """True if the perfume is stored. With no concentration given, any concentration counts."""
    if concentration:
        return get_by_slug(perfume_slug(brand, name, concentration)) is not None
    target = slugify(f"{brand} {name}")
    return any(slugify(f"{r['brand']} {r['name']}") == target
               for r in conn().execute("SELECT brand, name FROM perfumes"))


def count() -> int:
    return conn().execute("SELECT COUNT(*) FROM perfumes").fetchone()[0]


def sample(n: int = 3) -> list[str]:
    return [f"{r['brand']} {r['name']}" for r in conn().execute(
        "SELECT brand, name FROM perfumes ORDER BY updated_at DESC LIMIT ?", (n,))]


def all_perfumes() -> list[dict]:
    return [get_perfume(r["id"]) for r in conn().execute("SELECT id FROM perfumes ORDER BY brand, name")]


def _summary(r: sqlite3.Row, score: float, all_words: bool) -> dict:
    return {"slug": r["slug"], "name": r["name"], "brand": r["brand"],
            "concentration": r["concentration"], "score": round(score, 3), "all_words": all_words}


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


# Words that don't tell perfumes apart ("Eau de Parfum", "The Tragedy of ...").
STOPWORDS = {"the", "de", "du", "des", "of", "and", "by", "la", "le", "les", "a", "an", "eau", "s"}
# Version words: if a search names none of them, prefer the version without them (Ganymede EDP over Extrait).
VERSION_WORDS = {"extrait", "intense", "elixir", "absolu", "absolue", "concentree", "parfum-extrait"}
CONCENTRATION_SHORT = {"toilette": "edt", "cologne": "edc"}


def _normalize(text: str) -> str:
    # "No 5", "No. 5", "N°5" all become "n5"
    return re.sub(r"\bn(?:o|r|º|°)?[\s.#]*(\d+)\b", r"n\1", text, flags=re.IGNORECASE)


def _words(text: str) -> list[str]:
    return [w for w in slugify(_normalize(text)).split("-") if w and w not in STOPWORDS]


def _word_matches(q: str, candidates: set[str], last: bool) -> bool:
    if q in candidates:
        return True
    if len(q) >= 3 and q.endswith("s") and q[:-1] in candidates:  # "mans" for "Man's", "roses" for "Rose"
        return True
    for c in candidates:
        if last and len(q) >= 2 and c.startswith(q):  # still typing: "sau" -> "sauvage"
            return True
        if len(q) >= 4 and len(c) >= 4 and _ratio(q, c) >= 0.8:  # typo: "ganymed", "savage"
            return True
    return False


def _index() -> dict[int, dict]:
    global _names
    if _names is None:
        _names = {}
        for r in conn().execute("SELECT id, brand, name, concentration, aliases FROM perfumes"):
            aliases = json.loads(r["aliases"])
            conc = r["concentration"] or ""
            conc_words = set(_words(conc))
            if conc_words & {"parfum"} and not conc_words & VERSION_WORDS and "toilette" not in conc_words:
                conc_words.add("edp")
            for long, short in CONCENTRATION_SHORT.items():
                if long in conc_words:
                    conc_words.add(short)
            name_words = set(_words(r["name"]))
            alias_words = {w for a in aliases for w in _words(a)}
            _names[r["id"]] = {
                "strings": [slugify(_normalize(x)) for x in
                            (f"{r['brand']} {r['name']}", r["name"], f"{r['name']} {conc}",
                             f"{r['brand']} {r['name']} {conc}", *aliases)],
                "name_words": name_words,
                # Each other name counts as a full name too ("Nhài Diễm Hương" for Jasmine Bomb).
                "alias_word_sets": [set(_words(a)) for a in aliases if _words(a)],
                "words": name_words | set(_words(r["brand"])) | conc_words | alias_words,
                "version_words": conc_words & VERSION_WORDS,
            }
    return _names


def search(query: str, limit: int = 8) -> list[dict]:
    """Rank perfumes for a free-text query. Scores of 0.85 and up are confident matches.

    A perfume can only be a confident match if every word typed matches one of its words (name, brand,
    version or alias), allowing typos ("ganymed") and a half-typed last word ("dior sau"). So "Interlude
    Woman" is never mistaken for Interlude Man; it shows up as a "did you mean" suggestion instead.
    """
    q_slug = slugify(_normalize(query.strip()))
    if len(q_slug) < 2:
        return []
    q_words = _words(query) or [q_slug]
    c = conn()
    scored: dict[int, tuple[float, bool]] = {}

    for pid, entry in _index().items():
        # Whole-string similarity: good for typos, blind to which words differ.
        s = max(_ratio(q_slug, x) for x in entry["strings"])
        if q_slug in entry["strings"]:
            s = 1.0
        elif any(x.startswith(q_slug) for x in entry["strings"]):
            s = max(s, 0.85)

        matched = [w for i, w in enumerate(q_words) if _word_matches(w, entry["words"], i == len(q_words) - 1)]
        if len(matched) == len(q_words):
            # Every typed word fits: confident, more so the more of the perfume's name was typed.
            def covered(names: set) -> float:
                named = sum(1 for w in names if _word_matches(w, set(q_words), False)
                            or any(_word_matches(q, {w}, True) for q in q_words))
                return named / len(names) if names else 1.0
            coverage = max(covered(n) for n in [entry["name_words"], *entry["alias_word_sets"]])
            s = max(s, 0.85 + 0.15 * coverage) if coverage > 0 else min(s, 0.75)
            # One word that's only part of a longer name ("water", "rose"), or too little of the name typed:
            # suggest, don't decide. An exact name or alias still counts ("santal").
            if q_slug not in entry["strings"] and ((len(q_words) == 1 and coverage < 1) or coverage < 0.5):
                s = min(s, 0.8)
            # Unasked-for version words (Extrait, Intense) lose a tie against the plain version.
            s -= 0.01 * len(entry["version_words"] - set(q_words))
        else:
            # A word that fits nothing ("woman" vs "Man", "intense") means a different perfume: suggest only.
            s = min(s, 0.75)
            if not matched:
                continue
        if s >= 0.6:
            scored[pid] = (s, len(matched) == len(q_words))

    ranked = sorted(scored.items(), key=lambda kv: -kv[1][0])[:limit]
    out = []
    for pid, (s, all_words) in ranked:
        r = c.execute("SELECT slug, name, brand, concentration FROM perfumes WHERE id = ?", (pid,)).fetchone()
        if r:
            out.append(_summary(r, min(s, 1.0), all_words))
    return out


def typo_score(hits: list[dict]) -> float:
    """How likely a missed search is just a typo or vague version of something stored: the best score among
    hits that matched every typed word. A search with a word that fits nothing ("Interlude Woman") scores 0,
    because it names a different perfume."""
    return max((h["score"] for h in hits if h["all_words"]), default=0.0)


CONFIDENT = 0.85


def best_match(query: str) -> dict | None:
    """The one perfume a search clearly means, or None. Ties between different perfumes ("man" fits Interlude
    Man, Epic Man and Reflection Man equally) are never a clear match."""
    hits = search(query, limit=2)
    if not hits or hits[0]["score"] < CONFIDENT:
        return None
    if len(hits) > 1 and hits[1]["score"] > hits[0]["score"] - 0.005:
        return None
    return hits[0]
