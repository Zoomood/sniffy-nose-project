"""Import a brand's lineup from its own Shopify store.

Shopify stores publish their catalogue at /products.json. This reads it politely (robots.txt, a pause between
requests, through brand_import.fetch), keeps only the perfumes, and merges sizes into one perfume each.
For every perfume not stored yet:
  1. look for a "Top notes: ..." style list in the product description (free);
  2. otherwise, if the description talks about notes, ask the AI to read them out of it (one request);
  3. if the description has no notes at all, add the perfume to the wishlist without a brand_url, so
     `import-list --web` looks it up on the web later.
"""

import csv
import html
import json
import re
from pathlib import Path
from urllib.parse import quote, urlparse

from app import brand_import, db

# Words that mark a product as something other than a bottle of perfume (titles and product types,
# English and French since some stores are French).
NOT_PERFUME = re.compile(
    r"\b(candles?|bougies?|workshops?|discovery|d[ée]couverte|coffret|gift ?cards?|cartes? cadeaux?|gift ?sets?|"
    r"diffus(er|eur)s?|reed|room ?spray|home ?fragrance|parfum d'ambiance|ambiance|hand ?(wash|cream|gel)|"
    r"d[ée]sinfectant|body ?(wash|lotion|oil|cream|mist)|shower|bath|soap|savon|lotion|gel douche|"
    r"samples?|[ée]chantillons?|refills?|recharges?|travel|voyage|miniatures?|bundle|duo|trio|wrap|pouch|"
    r"case|etui|étui|accessor(y|ies)|tote|bag|sets?|layering|oils?|huiles?|crayons?|pencils?|c[ée]ramique|"
    r"carnet|vaporisateur)\b", re.I)
# Concentration words removed from the end of a product title to get the perfume's name.
NAME_SUFFIX = re.compile(r"\s*[-–,]?\s*(extrait de parfum|extrait|eau de parfum|eau de toilette|eau de cologne|"
                         r"\bedp|\bedt|\bedc)\s*$", re.I)
SIZE = re.compile(r"\b\d+(\.\d+)?\s?ml\b", re.I)
CONCENTRATIONS = [
    (r"extrait( de parfum)?", "Extrait de Parfum"),
    (r"eau de parfum|\bedp\b", "Eau de Parfum"),
    (r"eau de toilette|\bedt\b", "Eau de Toilette"),
    (r"eau de cologne|\bedc\b|\bcologne\b", "Eau de Cologne"),
    (r"\bparfum\b", "Parfum"),
]
# The description is worth one AI request if it talks about notes or is long enough to describe the scent
# in story form ("the lotus rested in the embrace of white oak, hay absolute and musk").
MENTIONS_NOTES = re.compile(
    r"\bnotes?\b|\baccords?\b|opens with|heart of|base of|dries down|t[êe]te|c(oe|œ)ur|fond|"
    r"hương đầu|hương giữa|hương cuối", re.I)


def _text(body_html: str) -> str:
    t = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", body_html or "", flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def fetch_catalog(site: str) -> list[dict]:
    """Every product in the store, page by page."""
    base = site.rstrip("/")
    if not urlparse(base).scheme:
        base = "https://" + base
    products, page = [], 1
    while True:
        raw = brand_import.fetch(f"{base}/products.json?limit=250&page={page}")
        try:
            batch = json.loads(raw).get("products", [])
        except ValueError:
            raise brand_import.ImportError_(f"{base} doesn't publish a Shopify product list.")
        products += batch
        if len(batch) < 250:
            return products
        page += 1


_SMALL = {"de", "du", "des", "of", "the", "and", "la", "le", "les", "et", "in", "a"}


def _title(name: str) -> str:
    out = []
    for i, w in enumerate(name.lower().split()):
        m = re.match(r"^([dl])['’](.*)$", w)
        if m:  # "d'or" -> "d'Or" (first word: "L'Ombre")
            w = (m.group(1).upper() if i == 0 else m.group(1)) + "'" + m.group(2)[:1].upper() + m.group(2)[1:]
        elif i == 0 or w not in _SMALL:
            w = w[:1].upper() + w[1:]
        out.append(w)
    return " ".join(out)


def _concentration(*texts: str) -> str:
    for t in texts:
        for pattern, label in CONCENTRATIONS:
            if re.search(pattern, t or "", re.I):
                return label
    return ""


def perfumes_in(products: list[dict], site: str) -> list[dict]:
    """Keep only perfumes, one per name + concentration (sizes and duplicate listings merged)."""
    base = site.rstrip("/") if urlparse(site).scheme else "https://" + site.rstrip("/")
    found: dict[str, dict] = {}
    for p in products:
        title, ptype = p.get("title", ""), p.get("product_type") or ""
        variants = " ".join(v.get("title", "") for v in p.get("variants", []))
        if NOT_PERFUME.search(title) or NOT_PERFUME.search(ptype):
            continue
        is_bottle = SIZE.search(variants) or SIZE.search(title) or re.search(r"perfume|fragrance|parfum", ptype, re.I)
        if not is_bottle:
            continue
        # "English name | Vietnamese name" -> name + alias
        parts = [s.strip() for s in re.split(r"\s*\|\s*", title.replace("\xa0", " ")) if s.strip()]
        conc = _concentration(*parts[1:], ptype, parts[0])
        name = SIZE.sub("", NAME_SUFFIX.sub("", parts[0])).strip(" -–,")
        # Other parts are names in another language ("Jasmine Bomb | Nhài Diễm Hương"), unless they only
        # say the concentration ("PERFECT OUD | Eau de parfum").
        aliases = [a for a in parts[1:] if not _concentration(a)]
        if name.isupper():  # "POUDRE D'OR" -> "Poudre d'Or", "MEMORY OF THE SUN" -> "Memory of the Sun"
            name = _title(name)
        body = _text(p.get("body_html", ""))
        conc = conc or _concentration(body[:400])
        key = db.perfume_slug("", name, conc)
        if key in found:  # same perfume listed twice: keep the longer description
            if len(body) > len(found[key]["body"]):
                found[key]["body"] = body
            continue
        found[key] = {"name": name, "aliases": aliases, "concentration": conc, "body": body,
                      "url": f"{base}/products/{quote(p['handle'])}"}
    return list(found.values())


def _from_extracted(brand: str, item: dict, extracted: dict) -> dict:
    pyramid = {lvl: [n["name"] for n in extracted.get(lvl, [])] for lvl in db.LEVELS}
    return {
        "brand": brand, "name": item["name"], "aliases": item["aliases"],
        "concentration": item["concentration"] or extracted.get("concentration", ""),
        "description": extracted.get("description") or "",
        "families": extracted.get("families") or [],
        "year": extracted.get("year") or "", "perfumer": extracted.get("perfumer") or "",
        "flat_pyramid": bool(pyramid["heart"]) and not pyramid["top"] and not pyramid["base"],
        "brand_url": item["url"], **{lvl: [{"name": n, "sources": ["brand"]} for n in pyramid[lvl]]
                                     for lvl in db.LEVELS},
    }


def import_catalog(brand: str, site: str, wishlist: Path, use_ai: bool = True, dry_run: bool = False,
                   limit: int = 0) -> None:
    from app import lookup

    print(f"Reading {brand}'s product list from {site} ...")
    items = perfumes_in(fetch_catalog(site), site)
    print(f"{len(items)} perfumes in the store (candles, sets, sizes and other products left out).")

    stored = [i for i in items if db.exists(brand, i["name"], i["concentration"])]
    todo = [i for i in items if i not in stored]
    if limit:
        todo = todo[:limit]
    print(f"{len(stored)} already stored, {len(todo)} to add.")
    ai_ok = use_ai and lookup.has_credentials()

    saved, need_web = [], []
    for item in todo:
        label = f"{item['name']} {item['concentration']}".rstrip()
        pyramid = brand_import.parse_pyramid(item["body"])
        if len(pyramid) >= 2:
            perfume = _from_extracted(brand, item, {lvl: [{"name": n} for n in pyramid.get(lvl, [])]
                                                    for lvl in db.LEVELS})
            how = "pattern"
        elif ai_ok and (MENTIONS_NOTES.search(item["body"]) or len(item["body"]) >= 300):
            if dry_run:
                print(f"- {label}: would ask the AI to read the description")
                continue
            try:
                extracted = lookup.extract_from_page(item["body"][:brand_import.MAX_PAGE_CHARS], item["url"],
                                                     f"{brand} {item['name']}")
            except lookup.LookupFailed as e:
                print(f"- {label}: failed ({e})")
                if e.status in (401, 402, 429):
                    print("Stopping here. Run the same command again later to continue where it left off.")
                    break
                continue
            if not extracted.get("found"):
                print(f"- {label}: no notes in the description -> wishlist for a web lookup")
                need_web.append(item)
                continue
            perfume, how = _from_extracted(brand, item, extracted), "ai"
        else:
            print(f"- {label}: no notes in the description -> wishlist for a web lookup")
            need_web.append(item)
            continue
        levels = ", ".join(f"{lvl}: {len(perfume[lvl])}" for lvl in db.LEVELS if perfume[lvl])
        if not dry_run:
            db.upsert_perfume(perfume, origin="brand-import", source_urls={"brand": item["url"]})
        saved.append(item)
        print(f"- {label}: {'would save' if dry_run else 'saved'} ({levels}; read by {how})")

    if need_web and not dry_run:
        _add_to_wishlist(wishlist, brand, need_web)
        print(f"\n{len(need_web)} perfume(s) added to {wishlist} for a web lookup:\n"
              f'  python -m app.manage import-list {wishlist} --web --brands "{brand}"')
    print(f"\nDone: {len(saved)} {'would be ' if dry_run else ''}saved from the brand's own descriptions.")


def _add_to_wishlist(wishlist: Path, brand: str, items: list[dict]) -> None:
    rows = []
    if wishlist.exists():
        with wishlist.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    listed = {db.perfume_slug(r["brand"], r["name"], r.get("concentration") or "") for r in rows}
    new = [{"brand": brand, "name": i["name"], "concentration": i["concentration"], "brand_url": ""}
           for i in items if db.perfume_slug(brand, i["name"], i["concentration"]) not in listed]
    if not new:
        return
    write_header = not wishlist.exists()
    with wishlist.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "name", "concentration", "brand_url"])
        if write_header:
            w.writeheader()
        w.writerows(new)
