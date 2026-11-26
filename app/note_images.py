"""Fetch one picture per note into static/notes/, and record where each came from.

Natural notes get a freely licensed photo from iNaturalist (CC0 / CC BY / CC BY-SA), searched by the plant's
Latin name; with --prints, a public-domain botanical print from Wikimedia Commons is tried when iNaturalist has none.
synthetic notes and accords get the molecule's structure drawing from PubChem (US government, free to use).
Credits go to app/data/note_images.json, which the note pages show under each picture.

    .venv/bin/python -m app.note_images           # fetch what's missing
    .venv/bin/python -m app.note_images --force   # fetch everything again
"""

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"
OUT = ROOT / "static" / "notes"
MANIFEST = DATA / "note_images.json"
UA = {"User-Agent": "SniffyNose/1.0 (perfume notes dictionary; botanical illustrations)"}

PRINT_WORDS = ("köhler", "koehler", "curtis", "blanco", "illustration", "plate", "drawing", "botanical", "flora",
               "tafel", "lithograph", "engraving", "bauer", "jacquin", "redouté", "redoute", "thomé", "thome",
               "sowerby", "medizinal", "plantae", "herbarium")
QUERIES = ('"{}" Köhler', '"{}" botanical illustration', '"{}" plate')
PAUSE = 1.5  # seconds between notes; iNaturalist asks for about one request a second


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def get(url: str) -> bytes:
    """GET with polite back-off: Wikimedia answers 429 when we go too fast."""
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 10 * (attempt + 1))
    raise RuntimeError("unreachable")


def plain(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def commons_print(latin: str) -> dict | None:
    """Best public-domain illustration of a plant on Wikimedia Commons, or None."""
    seen = set()
    for q in QUERIES:
        params = {
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 15,
            "gsrsearch": q.format(latin), "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": 640,
        }
        data = json.loads(get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)))
        pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
        best = None
        for p in pages:
            title = p["title"]
            if title in seen or not p.get("imageinfo"):
                continue
            seen.add(title)
            info = p["imageinfo"][0]
            meta = info.get("extmetadata", {})
            lic = plain(meta.get("LicenseShortName", {}).get("value", "")).lower()
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if not ("public domain" in lic or lic.startswith("pd") or "cc0" in lic):
                continue
            genus = latin.split()[0].lower()
            low = title.lower()
            if genus not in low and genus not in plain(meta.get("ImageDescription", {}).get("value", "")).lower():
                continue
            score = sum(w in low for w in PRINT_WORDS)
            if score and (best is None or score > best[0]):
                best = (score, {
                    "url": info["thumburl"], "page": info["descriptionurl"], "title": title.removeprefix("File:"),
                    "artist": plain(meta.get("Artist", {}).get("value", ""))[:120] or "Unknown artist",
                    "license": "Public domain",
                })
        if best:
            return best[1]
        time.sleep(3)  # Wikimedia's limit for unauthenticated bots is strict
    return None


# Licences that allow reuse with a credit (non-commercial ones are skipped so the site stays free to grow).
OPEN_LICENCES = {"cc0": "CC0", "cc-by": "CC BY", "cc-by-sa": "CC BY-SA"}


def inat_photo(latin: str) -> dict | None:
    """A freely licensed photo of the plant from iNaturalist (one request, two if the main photo isn't open)."""
    q = urllib.parse.urlencode({"q": latin, "per_page": 5})
    results = json.loads(get(f"https://api.inaturalist.org/v1/taxa?{q}")).get("results", [])
    genus = latin.split()[0].lower()
    taxon = next((t for t in results if t.get("name", "").lower() == latin.lower()), None) \
        or next((t for t in results if t.get("name", "").lower().startswith(genus)), None)
    if not taxon:
        return None
    photos = [taxon.get("default_photo") or {}]
    if photos[0].get("license_code") not in OPEN_LICENCES:
        time.sleep(1)
        full = json.loads(get(f"https://api.inaturalist.org/v1/taxa/{taxon['id']}")).get("results", [{}])[0]
        photos = [tp["photo"] for tp in full.get("taxon_photos", [])]
    if not any(ph.get("license_code") in OPEN_LICENCES for ph in photos):
        # Featured photos are all non-commercial: fall back to the best-voted open-licence observation photos.
        time.sleep(1)
        q = urllib.parse.urlencode({"taxon_id": taxon["id"], "photo_license": ",".join(OPEN_LICENCES),
                                    "quality_grade": "research", "order_by": "votes", "per_page": 5})
        obs = json.loads(get(f"https://api.inaturalist.org/v1/observations?{q}")).get("results", [])
        photos = [{**p, "medium_url": (p.get("url") or "").replace("/square.", "/medium.")}
                  for o in obs for p in o.get("photos", [])]
    for ph in photos:
        if ph.get("license_code") in OPEN_LICENCES and ph.get("medium_url"):
            return {"url": ph["medium_url"].replace("/medium.", "/large."), "title": taxon["name"],
                    "artist": ph.get("attribution_name") or ph.get("attribution", "")[:120],
                    "license": OPEN_LICENCES[ph["license_code"]],
                    "page": f"https://www.inaturalist.org/photos/{ph['id']}"}
    return None


def pubchem_png(name: str) -> bytes | None:
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
           f"{urllib.parse.quote(name)}/PNG?image_size=500x500")
    try:
        return get(url)
    except Exception:
        return None


def main(force: bool = False) -> None:
    details = json.loads((DATA / "note_details.json").read_text())
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    for name, d in details.items():
        have = manifest.get(name)
        # Skip notes that already have a picture, unless a plant note only got a molecule drawing.
        if have and not force and (OUT / have["file"]).exists() and not (d.get("plant") and have["kind"] == "molecule"):
            continue
        entry = None
        try:
            if d.get("plant"):
                hit = inat_photo(d["plant"])
                if hit:
                    file = f"{slugify(name)}.jpg"
                    (OUT / file).write_bytes(get(hit["url"]))
                    entry = {"file": file, "kind": "photo", "caption": d["plant"], **{k: hit[k] for k in ("title", "artist", "license", "page")}}
            if entry is None and d.get("plant") and "--prints" in sys.argv:
                hit = commons_print(d["plant"])
                if hit:
                    ext = ".png" if hit["url"].lower().endswith(".png") else ".jpg"
                    file = f"{slugify(name)}{ext}"
                    (OUT / file).write_bytes(get(hit["url"]))
                    entry = {"file": file, "kind": "print", "caption": d["plant"], **{k: hit[k] for k in ("title", "artist", "license", "page")}}
            if entry is None and have and (OUT / have["file"]).exists():
                entry = have  # keep the molecule drawing we already have
            if entry is None:
                mol = d.get("mol") or (d.get("molecules") or [[None]])[0][0]
                png = pubchem_png(mol) if mol else None
                if png:
                    file = f"{slugify(name)}.png"
                    (OUT / file).write_bytes(png)
                    entry = {"file": file, "kind": "molecule", "caption": mol, "artist": "PubChem",
                             "license": "Public domain (US government)",
                             "page": f"https://pubchem.ncbi.nlm.nih.gov/#query={urllib.parse.quote(mol)}"}
        except Exception as e:  # keep going; a missing picture just shows the placeholder
            print(f"  ! {name}: {e}", file=sys.stderr)
        if entry:
            manifest[name] = entry
            done += 1
            print(f"{entry['kind']:8} {name}  <- {entry.get('title') or entry['caption']}")
        else:
            print(f"none     {name}")
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        time.sleep(PAUSE)
    print(f"\n{done} pictures fetched, {len(manifest)} of {len(details)} notes have one.")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
