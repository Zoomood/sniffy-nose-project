"""Build the website as plain files in _site/, ready for GitHub Pages (or any static host).

Everything the site shows is written out as JSON next to the page: the perfumes from the database, the notes
dictionary, glossary, history and all wording. The search runs in the visitor's browser, so no server is needed.

    .venv/bin/python -m app.build_site                 # build into _site/
    python3 -m http.server 8000 -d _site               # preview it at http://localhost:8000

GitHub builds and publishes the site the same way on every push (see .github/workflows/pages.yml).
"""

import hashlib
import html
import json
import re
import shutil
from pathlib import Path

from app import db

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"
STATIC = ROOT / "static"
OUT = ROOT / "_site"


def _load(name: str, default=None):
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default


def _public(d: dict) -> dict:
    """Drop the "_help" notes meant for whoever edits the file."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _write(name: str, data) -> None:
    (OUT / "data" / name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def perfumes() -> list[dict]:
    """Every stored perfume with its notes, plus the words the browser's search matches against."""
    index = db._index()
    out = []
    for p in db.all_perfumes():
        entry = index[p["id"]]
        rec = {k: p[k] for k in ("slug", "name", "brand", "concentration", "aliases", "year", "perfumer", "families",
                                 "description", "flat_pyramid", "brand_url", "fragrantica_url", *db.LEVELS)}
        rec["provenance"] = [{"kind": s["kind"], "url": s["url"]} for s in p["provenance"]]
        rec["updated_at"] = p["updated_at"]
        rec["match"] = {"strings": entry["strings"], "name_words": sorted(entry["name_words"]),
                        "alias_word_sets": [sorted(s) for s in entry["alias_word_sets"]],
                        "words": sorted(entry["words"]), "version_words": sorted(entry["version_words"])}
        out.append(rec)
    return out


def notes() -> tuple[list[dict], dict]:
    """The dictionary list (with picture and type) and, per note, its details and picture credit."""
    images, details = _load("note_images.json", {}), _load("note_details.json", {})
    listing, full = [], {}
    for n in _load("notes.json", []):
        item = {**n}
        if n["name"] in images:
            item.update(image=images[n["name"]]["file"], image_kind=images[n["name"]]["kind"])
        if details.get(n["name"], {}).get("type"):
            item["type"] = details[n["name"]]["type"]
        listing.append(item)
        full[n["name"]] = {"details": details.get(n["name"], {}), "image": images.get(n["name"])}
    return listing, full


def history() -> list[dict]:
    pics = _load("history_images.json", {})
    out = []
    for e in _load("history.json", []):
        e = {k: v for k, v in e.items() if k != "image"}
        if db.slugify(e["title"]) in pics:
            e["picture"] = pics[db.slugify(e["title"])]
        out.append(e)
    return out


def build() -> Path:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data").mkdir(parents=True)

    perfume_list = perfumes()
    latest = sorted(perfume_list, key=lambda p: p["updated_at"], reverse=True)[:3]
    site = _load("site.json", {})
    _write("site.json", {"text": _public(site), "families": _public(_load("families.json", {})),
                         "types": _public(_load("note_types.json", {})),
                         "status": {"count": len(perfume_list), "sample": [f"{p['brand']} {p['name']}" for p in latest]}})
    listing, full = notes()
    _write("dictionary.json", {"notes": listing, "glossary": _load("glossary.json", [])})
    _write("notes.json", full)
    _write("perfumes.json", perfume_list)
    _write("history.json", history())

    for folder in ("notes", "history"):
        shutil.copytree(STATIC / folder, OUT / folder)
    for name in ("app.js", "styles.css"):
        shutil.copy2(STATIC / name, OUT / name)
    (OUT / ".nojekyll").write_text("")  # serve files as they are, without GitHub's Jekyll step

    # The page shell: fill {{meta.title}}-style placeholders and stamp a version so browsers fetch fresh files.
    version = hashlib.sha1(b"".join((OUT / n).read_bytes() for n in ("app.js", "styles.css", "data/site.json",
                                                                      "data/perfumes.json"))).hexdigest()[:10]

    def text(key: str) -> str:
        node = site
        for part in key.split("."):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return node if isinstance(node, str) else key

    page = (STATIC / "index.html").read_text().replace("{{version}}", version)
    page = re.sub(r"\{\{([\w.]+)\}\}", lambda m: html.escape(text(m.group(1))), page)
    (OUT / "index.html").write_text(page)
    return OUT


if __name__ == "__main__":
    out = build()
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"Built {out.relative_to(ROOT)}/ ({size:.1f} MB). Preview: python3 -m http.server 8000 -d _site")
