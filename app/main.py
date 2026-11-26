"""Sniffy Nose - FastAPI server. Database first: searches are answered from the local SQLite database,
and only a miss (with an API key set) triggers a web lookup, whose result is saved for next time."""

import html
import json
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent.parent / ".env")

from app import db, lookup, manage  # noqa: E402  (needs env loaded first)

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"

manage.seed_if_empty()

app = FastAPI(title="Sniffy Nose")


def _api_error(e: lookup.LookupFailed) -> HTTPException:
    return HTTPException(e.status, str(e))


def _not_found(message: str, suggestions: list[dict], can_lookup: bool = False) -> HTTPException:
    return HTTPException(404, {"message": message, "suggestions": suggestions, "can_lookup": can_lookup})


@app.get("/api/status")
def status():
    return {"live": lookup.has_credentials(), "count": db.count(), "sample": db.sample(3)}


_cache: dict[str, tuple[float, object]] = {}


def _json(name: str, default):
    """Read a data file, re-reading it only when it changes on disk (the note images fill in over time)."""
    path = DATA / name
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return default
    if name not in _cache or _cache[name][0] != mtime:
        _cache[name] = (mtime, json.loads(path.read_text()))
    return _cache[name][1]


def text(key: str, **values) -> str:
    """A piece of site wording from site.json, e.g. text("errors.note_missing")."""
    node = _json("site.json", {})
    for part in key.split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    s = node if isinstance(node, str) else key
    return s.format(**values) if values else s


@app.get("/api/site")
def site():
    """All the site's wording, family colours and note-type intros, which the page draws from."""
    drop = lambda d: {k: v for k, v in d.items() if not k.startswith("_")}
    return {"text": drop(_json("site.json", {})), "families": drop(_json("families.json", {})),
            "types": drop(_json("note_types.json", {}))}


@app.get("/api/history")
def history():
    """The perfume timeline, each milestone with its picture and credit when it has one."""
    pics = _json("history_images.json", {})
    out = []
    for e in _json("history.json", []):
        e = {k: v for k, v in e.items() if k != "image"}
        if lookup.slugify(e["title"]) in pics:
            e["picture"] = pics[lookup.slugify(e["title"])]
        out.append(e)
    return out


def _notes_with_images() -> list[dict]:
    """Dictionary notes with their picture and type (Natural, Synthetic, Accord...), where we have them."""
    images = _json("note_images.json", {})
    details = _json("note_details.json", {})
    out = []
    for n in _json("notes.json", []):
        n = {**n}
        if n["name"] in images:
            n.update(image=images[n["name"]]["file"], image_kind=images[n["name"]]["kind"])
        if details.get(n["name"], {}).get("type"):
            n["type"] = details[n["name"]]["type"]
        out.append(n)
    return out


@app.get("/api/dictionary")
def dictionary():
    return {"notes": _notes_with_images(), "glossary": _json("glossary.json", [])}


@app.get("/api/note/{slug}")
def note(slug: str):
    """One dictionary note with its history, chemistry, picture and the stored perfumes that use it."""
    for n in _json("notes.json", []):
        names = [n["name"], *n.get("aliases", [])]
        if slug in (lookup.slugify(x) for x in names):
            details = _json("note_details.json", {}).get(n["name"], {})
            image = _json("note_images.json", {}).get(n["name"])
            lowered = [x.lower() for x in names]
            rows = db.conn().execute(
                f"""SELECT DISTINCT p.slug, p.brand, p.name, n.level FROM perfume_notes n
                    JOIN perfumes p ON p.id = n.perfume_id
                    WHERE lower(n.note) IN ({",".join("?" * len(lowered))})
                    ORDER BY p.brand, p.name""", lowered).fetchall()
            return {**n, "details": details, "image": image, "perfumes": [dict(r) for r in rows]}
    raise HTTPException(404, text("errors.note_missing"))


@app.get("/api/suggest")
def suggest(q: str = Query(..., min_length=1, max_length=120)):
    return db.search(q, limit=6)


@app.get("/api/perfume/{slug}")
def perfume(slug: str):
    p = db.get_by_slug(slug)
    if not p:
        raise HTTPException(404, text("errors.perfume_missing"))
    return p


@app.get("/api/search")
def search(q: str = Query(..., min_length=2, max_length=120), web: bool = False):
    """`web=true` skips the "did you mean" step and looks the perfume up on the web."""
    matches = db.search(q)
    best = db.best_match(q)
    if best and not web:
        return db.get_by_slug(best["slug"])

    # Everything past this point is a miss: remember it so popular requests get added later.
    db.log_miss(q, db.typo_score(matches))

    if not lookup.has_credentials():
        raise _not_found(text("errors.not_in_database", q=q), matches)

    # Close-but-unsure matches are often typos of a stored perfume: ask before paying for a web lookup.
    if matches and not web:
        raise _not_found(text("errors.no_exact_match", q=q), matches, can_lookup=True)

    try:
        result = lookup.research_perfume(q)
    except lookup.LookupFailed as e:
        raise _api_error(e)  # stays "pending" in the log, so the nightly queue retries it
    if not result.get("found"):
        db.mark_search(q, "not_found")
        raise _not_found(text("errors.not_found_web", q=q), matches)

    try:
        pid, _ = db.save_web_lookup(result)
    except ValueError as e:
        raise HTTPException(502, text("errors.lookup_incomplete", error=e))
    perfume = db.get_perfume(pid)
    db.mark_search(q, "added", perfume["slug"])
    return perfume


@app.get("/api/explain")
def explain(term: str = Query(..., min_length=1, max_length=80)):
    key = lookup.slugify(term)
    for entry in _json("notes.json", []) + _json("glossary.json", []):
        names = [entry["name"]] + entry.get("aliases", [])
        if key in (lookup.slugify(n) for n in names):
            return {"term": entry["name"], "family": entry.get("family", "Term"),
                    "description": entry["description"], "source": "dictionary"}
    if not lookup.has_credentials():
        return {"term": term, "family": "", "description": text("errors.no_entry"),
                "source": "none"}
    try:
        return {**lookup.explain_term(term), "source": "ai"}
    except lookup.LookupFailed as e:
        raise _api_error(e)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    """The page shell, with its {{meta.title}}-style placeholders filled from site.json."""
    page = (ROOT / "static" / "index.html").read_text()
    return HTMLResponse(re.sub(r"\{\{([\w.]+)\}\}", lambda m: html.escape(text(m.group(1))), page))
