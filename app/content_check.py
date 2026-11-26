"""Check the site's content files in app/data for mistakes before they show up on the website.

    .venv/bin/python -m app.manage check

Problems (the site will show something wrong) make the command fail; warnings are worth a look.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _load(name: str, problems: list[str]):
    try:
        return json.loads((DATA / name).read_text())
    except FileNotFoundError:
        problems.append(f"{name}: file is missing")
    except json.JSONDecodeError as e:
        problems.append(f"{name}: not valid JSON ({e})")
    return None


def _text_at(site: dict, key: str):
    node = site
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def check() -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []
    site = _load("site.json", problems) or {}
    families = _load("families.json", problems) or {}
    types = _load("note_types.json", problems) or {}
    notes = _load("notes.json", problems) or []
    details = _load("note_details.json", problems) or {}
    glossary = _load("glossary.json", problems) or []
    history = _load("history.json", problems) or []
    pics = _load("history_images.json", problems) or {}

    # Every piece of wording the page asks for exists in site.json.
    js = (ROOT / "static" / "app.js").read_text()
    used = set(re.findall(r"""\b(?:t|tx|rich|textAt)\(\s*["`]([\w.]+)["`]""", js))
    used |= set(re.findall(r"\{\{([\w.]+)\}\}", (ROOT / "static" / "index.html").read_text()))
    used.discard("version")  # filled in by the build, not a piece of wording
    used |= {f"perfume.levels.{k}" for k in ("top", "heart", "base")} | {f"shelf.levels.{k}" for k in ("top", "heart", "base")}
    used |= {f"theme.to_{m}" for m in ("dark", "light")} | {f"theme.to_{m}_label" for m in ("dark", "light")}
    used |= {f"crumbs.{tab}" for tab in ("home", "dictionary", "glossary", "history")}
    for key in sorted(used):
        val = _text_at(site, key)
        if val is None:
            problems.append(f"site.json: missing wording for '{key}'")
        elif isinstance(val, dict) and {"one", "other"} <= val.keys():
            continue
        elif isinstance(val, str) and not val.strip():
            warnings.append(f"site.json: '{key}' is empty")
    for key in ("home.steps", "autolink_skip"):
        if not isinstance(_text_at(site, key), list):
            problems.append(f"site.json: '{key}' should be a list")

    # Notes: no duplicates, and each family and type has its colour and intro.
    fam_keys = {k.lower() for k in families if not k.startswith("_")}
    if "other" not in fam_keys:
        problems.append("families.json: needs an 'Other' entry (used for unknown families)")
    for fam, v in families.items():
        if not fam.startswith("_") and not (isinstance(v, dict) and v.get("chip") and v.get("liquid")):
            problems.append(f"families.json: '{fam}' needs both a 'chip' and a 'liquid' colour")
    seen: dict[str, str] = {}
    note_names = set()
    for n in notes:
        if not n.get("name") or not n.get("description"):
            problems.append(f"notes.json: an entry is missing its name or description ({n.get('name', '?')})")
            continue
        note_names.add(n["name"])
        if (n.get("family") or "").lower() not in fam_keys:
            warnings.append(f"notes.json: '{n['name']}' has family '{n.get('family')}', which has no colour in families.json")
        for name in [n["name"], *n.get("aliases", [])]:
            k = slugify(name)
            if k in seen and seen[k] != n["name"]:
                problems.append(f"notes.json: '{name}' is used by both '{seen[k]}' and '{n['name']}'")
            seen[k] = n["name"]
    for name, d in details.items():
        if name not in note_names:
            problems.append(f"note_details.json: '{name}' isn't a note in notes.json")
        if d.get("type") and d["type"] not in types:
            warnings.append(f"note_details.json: type '{d['type']}' (on {name}) has no intro in note_types.json")
    missing_type = sorted(n for n in note_names if not details.get(n, {}).get("type"))
    if missing_type:
        warnings.append(f"note_details.json: {len(missing_type)} notes have no type: {', '.join(missing_type)}")

    # Glossary: complete and without duplicates.
    gseen: dict[str, str] = {}
    for g in glossary:
        if not g.get("name") or not g.get("description"):
            problems.append(f"glossary.json: an entry is missing its name or description ({g.get('name', '?')})")
            continue
        for name in [g["name"], *g.get("aliases", [])]:
            k = slugify(name)
            if k in gseen and gseen[k] != g["name"]:
                problems.append(f"glossary.json: '{name}' is used by both '{gseen[k]}' and '{g['name']}'")
            gseen[k] = g["name"]

    # History: every milestone complete, with a picture search and a fetched picture.
    titles = set()
    for i, e in enumerate(history, 1):
        label = e.get("title") or f"milestone {i}"
        for field in ("era", "year", "title", "text"):
            if not e.get(field):
                problems.append(f"history.json: '{label}' is missing '{field}'")
        if e.get("title") in titles:
            problems.append(f"history.json: two milestones are called '{e['title']}'")
        titles.add(e.get("title"))
        key = slugify(e.get("title", ""))
        if not e.get("image"):
            warnings.append(f"history.json: '{label}' has no 'image' searches")
        elif key not in pics:
            warnings.append(f"history.json: '{label}' has no picture yet - run: python -m app.history_images")
        elif not (ROOT / "static" / "history" / pics[key]["file"]).exists():
            problems.append(f"history_images.json: picture file for '{label}' is missing")
    return problems, warnings


def main() -> int:
    problems, warnings = check()
    for p in problems:
        print(f"PROBLEM  {p}")
    for w in warnings:
        print(f"warning  {w}")
    print(f"\n{len(problems)} problems, {len(warnings)} warnings.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
