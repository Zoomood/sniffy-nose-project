"""Manage the perfume database from the command line.

    python -m app.manage init                      create the database and load the seed perfumes
    python -m app.manage import perfumes.csv       add/update perfumes from a spreadsheet (CSV) or JSON file
    python -m app.manage export perfumes.csv       write everything to CSV (edit it, then import it again)
    python -m app.manage import-url URL [URL ...]  read notes from brand product pages (polite, robots.txt-aware)
    python -m app.manage import-url --file urls.txt
    python -m app.manage import-list data/wishlist.csv  work through a to-do list (brand, name, concentration, brand_url)
    python -m app.manage import-list data/wishlist.csv --web   ...and look up rows without a URL on the web (paid)
    python -m app.manage import-store "Miller Harris" millerharris.com   a brand's lineup + notes from its own store
    python -m app.manage search "ganymed"          try the search the website uses
    python -m app.manage list
    python -m app.manage delete SLUG
    python -m app.manage check                     check the content files in app/data for mistakes

CSV columns (one row per perfume; notes separated by ";"):
    brand, name, concentration, year, perfumer, families, top, heart, base,
    description, brand_url, fragrantica_url, aliases, sources

`sources` says where the row's notes came from (brand, fragrantica, retailer, curated; default curated).
To tag one note differently, put its sources in braces: "Suede {fragrantica}; Akigalawood {brand+fragrantica}".
"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from app import brand_import, db  # noqa: E402

SEED_FILE = Path(__file__).parent / "data" / "seed_perfumes.json"
CSV_COLUMNS = ["brand", "name", "concentration", "year", "perfumer", "families", "top", "heart", "base",
               "description", "brand_url", "fragrantica_url", "aliases", "sources", "source_note"]
TEXT_FIELDS = ("brand", "name", "concentration", "year", "perfumer", "description", "brand_url",
               "fragrantica_url", "source_note")


def _split(cell: str) -> list[str]:
    return [x.strip() for x in (cell or "").split(";") if x.strip()]


def _sources_list(cell: str) -> list[str]:
    out = [s.strip().lower() for s in re.split(r"[,+ ]+", cell or "") if s.strip()]
    bad = [s for s in out if s not in db.SOURCES]
    if bad:
        raise ValueError(f"unknown source {bad[0]!r} (use {', '.join(db.SOURCES)})")
    return out


def _parse_notes(cell: str, default_sources: list[str]) -> list[dict]:
    notes = []
    for item in _split(cell):
        m = re.match(r"^(.*?)\s*\{([^}]*)\}\s*$", item)
        if m:
            notes.append({"name": m.group(1).strip(), "sources": _sources_list(m.group(2))})
        else:
            notes.append({"name": item, "sources": list(default_sources)})
    return notes


def row_to_perfume(row: dict) -> dict:
    default_sources = _sources_list(row.get("sources", "")) or ["curated"]
    p = {k: (row.get(k) or "").strip() for k in TEXT_FIELDS}
    p["families"] = _split(row.get("families", ""))
    p["aliases"] = _split(row.get("aliases", ""))
    for level in db.LEVELS:
        p[level] = _parse_notes(row.get(level, ""), default_sources)
    p["flat_pyramid"] = bool(p["heart"]) and not p["top"] and not p["base"]
    return p


def perfume_to_row(p: dict) -> dict:
    all_sources = [tuple(n["sources"]) for lvl in db.LEVELS for n in p[lvl]]
    common = max(set(all_sources), key=all_sources.count) if all_sources else ("curated",)
    row = {k: p.get(k, "") for k in TEXT_FIELDS}
    row["families"] = "; ".join(p["families"])
    row["aliases"] = "; ".join(p["aliases"])
    row["sources"] = ",".join(common)
    for level in db.LEVELS:
        row[level] = "; ".join(n["name"] if tuple(n["sources"]) == common else f"{n['name']} {{{'+'.join(n['sources'])}}}"
                               for n in p[level])
    return row


def import_file(path: Path) -> int:
    if path.suffix.lower() == ".json":
        items = json.loads(path.read_text())
        perfumes = [items] if isinstance(items, dict) else items
        rows = None
    else:
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        perfumes = []
        for i, row in enumerate(rows, start=2):
            if not any((v or "").strip() for v in row.values()):
                continue
            try:
                perfumes.append(row_to_perfume(row))
            except ValueError as e:
                print(f"  row {i}: skipped - {e}")
    n = 0
    for p in perfumes:
        try:
            db.upsert_perfume(p, origin=p.get("origin", "curated"),
                              source_urls={"curated": str(path.name)} if rows is not None else None)
            n += 1
        except ValueError as e:
            print(f"  skipped {p.get('name', '?')}: {e}")
    return n


def export_csv(path: Path) -> int:
    perfumes = db.all_perfumes()
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for p in perfumes:
            w.writerow(perfume_to_row(p))
    return len(perfumes)


def _cost_estimate(n: int) -> str:
    from app import lookup
    model = os.environ.get("OPENROUTER_MODEL", "")
    model_part = "the model itself is free" if model.endswith(":free") else "plus the model's own token prices"
    # Search is ~$0.001-0.007 per search depending on engine, and a lookup runs a few searches.
    return (f"roughly ${n * 0.005:.2f}-${n * 0.03:.2f} for web searches ({model_part}). "
            "Free models are limited to 50 requests a day on accounts that have bought under $10 of credits")


def import_list(path: Path, web: bool = False, use_ai: bool = True, dry_run: bool = False, yes: bool = False,
                brands: list[str] | None = None) -> None:
    """Work through a wishlist CSV: skip perfumes already stored, import rows that have a brand_url from the
    brand's page, and (with web=True) look the rest up on the web."""
    from app import lookup

    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("brand") or "").strip() and (r.get("name") or "").strip()]
    if brands:
        wanted = {db.slugify(b) for b in brands}
        rows = [r for r in rows if db.slugify(r["brand"]) in wanted]
        if not rows:
            print(f"No rows for {', '.join(brands)} in {path}.")
            return

    todo_url, todo_web, stored = [], [], 0
    for r in rows:
        brand, name, conc = r["brand"].strip(), r["name"].strip(), (r.get("concentration") or "").strip()
        if db.exists(brand, name, conc):
            stored += 1
        elif (r.get("brand_url") or "").strip():
            todo_url.append((brand, name, conc, r["brand_url"].strip()))
        else:
            todo_web.append((brand, name, conc))

    print(f"{len(rows)} perfumes on the list: {stored} already stored, {len(todo_url)} with a brand URL, "
          f"{len(todo_web)} without one.")

    for brand, name, conc, url in todo_url:
        print(f"- {brand} {name} ({url})")
        try:
            p = brand_import.import_url(url, brand, name, conc, use_ai=use_ai, dry_run=dry_run)
            levels = ", ".join(f"{lvl}: {len(p[lvl])}" for lvl in db.LEVELS if p[lvl])
            print(f"  {'would save' if dry_run else 'saved'} ({levels}; read by {p['method']})")
        except brand_import.ImportError_ as e:
            print(f"  skipped: {e}")

    if not todo_web:
        return
    if not web:
        print(f"\n{len(todo_web)} perfumes still need a brand_url (or run again with --web to look them up):")
        for brand, name, conc in todo_web:
            print(f"  {brand} {name} {conc}".rstrip())
        return
    if not lookup.has_credentials():
        print("\n--web needs OPENROUTER_API_KEY and OPENROUTER_MODEL in .env.")
        return
    print(f"\nLooking up {len(todo_web)} perfumes with {lookup.describe_provider()} will cost "
          f"{_cost_estimate(len(todo_web))}.")
    if dry_run:
        return
    if not yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return
    for brand, name, conc in todo_web:
        query = f"{brand} {name} {conc}".strip()
        print(f"- {query}")
        try:
            result = lookup.research_perfume(query)
        except lookup.LookupFailed as e:
            print(f"  failed: {e}")
            if e.status in (401, 402, 429):  # bad key, no credits or rate limited: the rest would fail too
                print("Stopping here. Run the same command again later to continue where it left off.")
                return
            continue
        if not result.get("found"):
            print("  not found")
            continue
        # Store under the wishlist's spelling so the row counts as done next time.
        result.update(brand=brand, name=name, concentration=conc or result.get("concentration", ""))
        db.save_web_lookup(result)
        print(f"  saved ({', '.join(f'{lvl}: {len(result[lvl])}' for lvl in db.LEVELS if result[lvl])})")


def _confirm(n: int, yes: bool) -> bool:
    from app import lookup
    print(f"\nLooking up {n} perfume(s) with {lookup.describe_provider()} will cost {_cost_estimate(n)}.")
    return yes or input("Continue? [y/N] ").strip().lower() == "y"


def show_queue(include_close: bool, show_all: bool) -> None:
    rows = db.search_queue(status=None if show_all else "pending", include_close=include_close, limit=100)
    if not rows:
        print("Nothing waiting. Searches that miss the database show up here.")
        return
    print(f"{'asked':>5}  {'status':<10} {'close':>5}  query")
    for r in rows:
        extra = f"  -> {r['slug']}" if r["slug"] else ""
        print(f"{r['count']:>5}  {r['status']:<10} {r['best_score']:>5.2f}  {r['query']}{extra}")
    if not include_close:
        print("\n(Searches that closely match a stored perfume, likely typos, are hidden. Add --include-close.)")


def process_queue(limit: int, include_close: bool, yes: bool, dry_run: bool) -> None:
    """Look up the most-requested missing perfumes, most popular first."""
    from app import lookup

    # Searches that now match something stored (added since, by any route) are done: no need to pay for them.
    for r in db.search_queue(include_close=include_close, limit=500):
        best = db.best_match(r["query"])
        if best:
            db.mark_search(r["query"], "added", best["slug"])
    rows = db.search_queue(include_close=include_close, limit=limit)
    if not rows:
        print("Queue is empty.")
        return
    print(f"{len(rows)} most-requested missing perfume(s):")
    for r in rows:
        print(f"  {r['count']}x  {r['query']}")
    if dry_run:
        return
    if not lookup.has_credentials():
        print("\nNeeds OPENROUTER_API_KEY and OPENROUTER_MODEL in .env.")
        return
    if not _confirm(len(rows), yes):
        print("Cancelled.")
        return
    added = 0
    for r in rows:
        print(f"- {r['query']}")
        try:
            result = lookup.research_perfume(r["query"])
        except lookup.LookupFailed as e:
            print(f"  failed: {e}")
            if e.status in (401, 402, 429):
                print("Stopping here. The rest stay in the queue for next time.")
                break
            continue
        if not result.get("found"):
            db.mark_search(r["query"], "not_found")
            print("  not found (won't be retried)")
            continue
        try:
            pid, new = db.save_web_lookup(result)
        except ValueError as e:
            print(f"  skipped: {e}")
            continue
        slug = db.get_perfume(pid)["slug"]
        db.mark_search(r["query"], "added", slug)
        added += new
        print(f"  {'added' if new else 'already stored'}: {slug}")
    print(f"Done: {added} new perfume(s).")


def discover_brand(brand: str, limit: int, wishlist: Path, dry_run: bool) -> None:
    """Ask the model for a brand's lineup and append the perfumes we don't have yet to the wishlist."""
    from app import lookup

    if not lookup.has_credentials():
        print("Needs OPENROUTER_API_KEY and OPENROUTER_MODEL in .env.")
        return
    print(f"Asking {lookup.describe_provider()} for {brand}'s lineup (one request)...")
    try:
        lineup = lookup.discover_lineup(brand, limit)
    except lookup.LookupFailed as e:
        print(f"Failed: {e}")
        return
    if not lineup["perfumes"]:
        print("No perfumes found for that brand. Check the spelling.")
        return

    existing_rows = []
    if wishlist.exists():
        with wishlist.open(newline="", encoding="utf-8-sig") as f:
            existing_rows = list(csv.DictReader(f))
    listed = {db.perfume_slug(r["brand"], r["name"], r.get("concentration") or "") for r in existing_rows}

    new_rows = []
    for p in lineup["perfumes"]:
        slug = db.perfume_slug(lineup["brand"], p["name"], p["concentration"])
        if slug in listed:
            state = "already on wishlist"
        elif db.exists(lineup["brand"], p["name"], p["concentration"]):
            state = "already in database"
        else:
            state = "new"
            new_rows.append({"brand": lineup["brand"], "name": p["name"],
                             "concentration": p["concentration"], "brand_url": ""})
            listed.add(slug)
        print(f"  {p['name']} {p['concentration']}".rstrip() + f"  [{state}]")
    print(f"Source: {lineup['source_url'] or 'not given'}")

    if dry_run or not new_rows:
        print(f"{len(new_rows)} new perfume(s){' would be' if dry_run else ''} added to {wishlist}.")
        return
    write_header = not wishlist.exists()
    with wishlist.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "name", "concentration", "brand_url"])
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(f"Added {len(new_rows)} perfume(s) to {wishlist}. Look them up with:\n"
          f'  python -m app.manage import-list {wishlist} --web --brands "{lineup["brand"]}"')


def seed_if_empty() -> int:
    if db.count() == 0 and SEED_FILE.exists():
        return import_file(SEED_FILE)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.manage", description="Manage the Sniffy Nose perfume database.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the database and load the seed perfumes")
    p_imp = sub.add_parser("import", help="import a CSV or JSON file")
    p_imp.add_argument("file", type=Path)
    p_exp = sub.add_parser("export", help="export everything to CSV")
    p_exp.add_argument("file", type=Path)
    p_url = sub.add_parser("import-url", help="import notes from brand product pages")
    p_url.add_argument("urls", nargs="*")
    p_url.add_argument("--file", type=Path, help="text file with one URL per line")
    p_url.add_argument("--brand", default="")
    p_url.add_argument("--name", default="")
    p_url.add_argument("--concentration", default="")
    p_url.add_argument("--no-ai", action="store_true", help="never use the AI model to read the page")
    p_url.add_argument("--dry-run", action="store_true", help="show what would be saved without saving")
    p_list = sub.add_parser("import-list", help="work through a wishlist CSV")
    p_list.add_argument("file", type=Path)
    p_list.add_argument("--web", action="store_true", help="look up rows without a brand_url on the web (paid)")
    p_list.add_argument("--yes", action="store_true", help="don't ask before paid web lookups")
    p_list.add_argument("--no-ai", action="store_true", help="never use the AI model to read brand pages")
    p_list.add_argument("--dry-run", action="store_true", help="show what would happen without saving")
    p_list.add_argument("--brands", default="", help='only these brands, comma-separated: "Amouage,Penhaligon\'s"')
    p_q = sub.add_parser("queue", help="show searches visitors made that weren't in the database")
    p_q.add_argument("--include-close", action="store_true", help="also show likely typos of stored perfumes")
    p_q.add_argument("--all", action="store_true", help="include ones already added or not found")
    p_pq = sub.add_parser("process-queue", help="look up the most-requested missing perfumes (paid)")
    p_pq.add_argument("--limit", type=int, default=20, help="how many to look up this run (default 20)")
    p_pq.add_argument("--include-close", action="store_true", help="also look up likely typos")
    p_pq.add_argument("--yes", action="store_true", help="don't ask before paid lookups")
    p_pq.add_argument("--dry-run", action="store_true", help="only show what would be looked up")
    p_db = sub.add_parser("discover-brand", help="add a brand's lineup to the wishlist (one paid request)")
    p_db.add_argument("brand")
    p_db.add_argument("--limit", type=int, default=30, help="max perfumes to list (default 30)")
    p_db.add_argument("--wishlist", type=Path, default=Path("data/wishlist.csv"))
    p_db.add_argument("--dry-run", action="store_true", help="show the lineup without changing the wishlist")
    p_st = sub.add_parser("import-store", help="import a brand's lineup and notes from its own (Shopify) store")
    p_st.add_argument("brand")
    p_st.add_argument("site", help="the store's address, e.g. millerharris.com")
    p_st.add_argument("--limit", type=int, default=0, help="only handle this many new perfumes")
    p_st.add_argument("--wishlist", type=Path, default=Path("data/wishlist.csv"))
    p_st.add_argument("--no-ai", action="store_true", help="never use the AI model to read descriptions")
    p_st.add_argument("--dry-run", action="store_true", help="show what would happen without saving")
    p_search = sub.add_parser("search", help="search the database like the website does")
    p_search.add_argument("query")
    sub.add_parser("list", help="list all perfumes")
    p_del = sub.add_parser("delete", help="delete a perfume by slug (see `list`)")
    p_del.add_argument("slug")
    sub.add_parser("check", help="check the site's content files (wording, notes, glossary, history) for mistakes")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        from app import content_check
        return content_check.main()

    if args.cmd == "init":
        n = seed_if_empty()
        print(f"Database ready at {db.DB_PATH} ({db.count()} perfumes, {n} added from the seed file).")
    elif args.cmd == "import":
        n = import_file(args.file)
        print(f"Imported {n} perfumes. The database now has {db.count()}.")
    elif args.cmd == "export":
        n = export_csv(args.file)
        print(f"Wrote {n} perfumes to {args.file}.")
    elif args.cmd == "import-url":
        urls = list(args.urls)
        if args.file:
            urls += [u.strip() for u in args.file.read_text().splitlines() if u.strip() and not u.startswith("#")]
        if not urls:
            ap.error("give at least one URL or --file")
        if len(urls) > 1 and (args.name or args.brand):
            print("note: --brand/--name apply to every URL")
        ok = 0
        for url in urls:
            print(f"- {url}")
            try:
                p = brand_import.import_url(url, args.brand, args.name, args.concentration,
                                            use_ai=not args.no_ai, dry_run=args.dry_run)
                levels = ", ".join(f"{lvl}: {len(p[lvl])}" for lvl in db.LEVELS if p[lvl])
                print(f"  {'would save' if args.dry_run else 'saved'} {p['brand']} {p['name']} ({levels}; read by {p['method']})")
                ok += 1
            except brand_import.ImportError_ as e:
                print(f"  skipped: {e}")
        print(f"Done: {ok}/{len(urls)} pages imported.")
    elif args.cmd == "import-list":
        import_list(args.file, web=args.web, use_ai=not args.no_ai, dry_run=args.dry_run, yes=args.yes,
                    brands=[b.strip() for b in args.brands.split(",") if b.strip()])
    elif args.cmd == "queue":
        show_queue(args.include_close, args.all)
    elif args.cmd == "process-queue":
        process_queue(args.limit, args.include_close, args.yes, args.dry_run)
    elif args.cmd == "discover-brand":
        discover_brand(args.brand, args.limit, args.wishlist, args.dry_run)
    elif args.cmd == "import-store":
        from app import shop_import
        try:
            shop_import.import_catalog(args.brand, args.site, args.wishlist, use_ai=not args.no_ai,
                                       dry_run=args.dry_run, limit=args.limit)
        except brand_import.ImportError_ as e:
            print(f"Stopped: {e}")
    elif args.cmd == "search":
        for r in db.search(args.query):
            print(f"{r['score']:.2f}  {r['brand']} {r['name']} {r['concentration']}  [{r['slug']}]")
    elif args.cmd == "list":
        for p in db.all_perfumes():
            print(f"{p['slug']:<50} {p['origin']:<12} updated {p['updated_at'][:10]}")
    elif args.cmd == "delete":
        print("Deleted." if db.delete_perfume(args.slug) else "No perfume with that slug.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
