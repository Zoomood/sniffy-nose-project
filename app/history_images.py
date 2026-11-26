"""Fetch one picture per milestone of the perfume history timeline into static/history/.

Each milestone in app/data/history.json has an "image" list of candidates, tried in order:
  "commons:<search>"  - the first freely licensed picture (public domain, CC0, CC BY, CC BY-SA) on Wikimedia Commons
  "file:<File name>"  - one exact Wikimedia Commons file
  "pubchem:<name>"    - the molecule's structure drawing from PubChem (US government, free to use)
Credits go to app/data/history_images.json, which the timeline shows under each picture.

    .venv/bin/python -m app.history_images            # fetch what's missing
    .venv/bin/python -m app.history_images --force    # fetch everything again
"""

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from app.note_images import get, plain, pubchem_png, slugify

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"
EVENTS = DATA / "history.json"
OUT = ROOT / "static" / "history"
MANIFEST = DATA / "history_images.json"

# Licences that allow reuse with a credit (non-commercial and no-derivatives ones are skipped).
def open_licence(lic: str) -> str | None:
    low = lic.lower()
    if "nc" in low.replace("-", " ").split() or "-nc" in low or "-nd" in low or " nd" in low:
        return None
    if "public domain" in low or low.startswith("pd"):
        return "Public domain"
    if "cc0" in low:
        return "CC0"
    if low.startswith("cc by-sa") or low.startswith("cc-by-sa"):
        return lic.upper().replace("CC-BY-SA", "CC BY-SA")
    if low.startswith("cc by") or low.startswith("cc-by"):
        return lic.upper().replace("CC-BY", "CC BY")
    return None


def artist_name(raw: str) -> str:
    """Commons credits are HTML: keep the words apart, and drop a trailing "Unknown author" after a real name."""
    name = re.sub(r"\s+", " ", plain(re.sub(r"<[^>]+>", " ", raw or ""))).strip()
    name = re.sub(r"\s*\bUnknown author\b\s*$", "", name, flags=re.I).strip() or "Unknown author"
    return name[:120]


def commons(search: str, exact: bool = False) -> dict | None:
    params = {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": 500}  # a standard thumbnail size, which Wikimedia serves from cache
    if exact:
        params["titles"] = search if search.startswith("File:") else f"File:{search}"
    else:
        params.update(generator="search", gsrnamespace=6, gsrlimit=10, gsrsearch=f"{search} filetype:bitmap")
    data = json.loads(get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)))
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    for p in pages:
        info = (p.get("imageinfo") or [None])[0]
        if not info or info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        meta = info.get("extmetadata", {})
        lic = open_licence(plain(meta.get("LicenseShortName", {}).get("value", "")))
        if not lic:
            continue
        return {"url": (info.get("thumburl") or info["url"]).split("?")[0], "page": info["descriptionurl"],
                "title": p["title"].removeprefix("File:"),
                "artist": artist_name(meta.get("Artist", {}).get("value", "")),
                "license": lic}
    return None


def main(force: bool = False) -> None:
    events = json.loads(EVENTS.read_text())
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)
    for e in events:
        key = slugify(e["title"])
        have = manifest.get(key)
        if have and not force and (OUT / have["file"]).exists():
            continue
        entry = None
        for cand in e.get("image", []):
            kind, _, what = cand.partition(":")
            try:
                if kind == "pubchem":
                    png = pubchem_png(what)
                    if png:
                        file = f"{key}.png"
                        (OUT / file).write_bytes(png)
                        entry = {"file": file, "kind": "molecule", "caption": what, "artist": "PubChem",
                                 "license": "Public domain (US government)",
                                 "page": f"https://pubchem.ncbi.nlm.nih.gov/#query={urllib.parse.quote(what)}"}
                else:
                    hit = commons(what, exact=kind == "file")
                    if hit:
                        ext = ".png" if hit["url"].lower().endswith(".png") else ".jpg"
                        file = f"{key}{ext}"
                        (OUT / file).write_bytes(get(hit["url"]))
                        entry = {"file": file, "kind": "picture", "caption": hit["title"],
                                 **{k: hit[k] for k in ("artist", "license", "page")}}
            except Exception as err:  # try the next candidate
                print(f"  ! {e['title']} [{cand}]: {err}", file=sys.stderr)
            time.sleep(8 if entry and entry["kind"] == "picture" else 1.5)  # Wikimedia throttles image downloads hard
            if entry:
                break
        if entry:
            manifest[key] = entry
            print(f"{entry['kind']:8} {e['title']}  <- {entry['caption']}")
        else:
            print(f"--       {e['title']}  (nothing found)")
        MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(force="--force" in sys.argv)
