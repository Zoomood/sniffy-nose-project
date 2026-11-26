# 👃 Sniffy Nose

Search a perfume's name to see its notes laid out level by level:
**Level 1 · Opening (top)**, **Level 2 · Heart** and **Level 3 · Base (dry-down)**. Each note shows where it
came from. You can hover or tap any note, or any underlined perfume word ("sillage", "nose", "EDP"…), to read
a short explanation without leaving the page.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.manage init      # creates app/data/perfumes.db with the seed perfumes
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000. No API key is needed.

## How search works (database first)

1. Every search is answered from the local SQLite database (`app/data/perfumes.db`). Matching tolerates
   typos and partial names, so "ganymed", "savage" and "dior sau" all work. This step is free and instant.
2. If nothing matches and a lookup provider is set up in `.env` (see below), an AI model looks the perfume up
   on the web and the result is **saved to the database**, so each perfume is paid for once. If the search
   is close to something already stored ("dior sav"), the visitor sees "did you mean" first and has to
   click to search the web.
3. Without a key, a miss shows "did you mean" suggestions instead.

Each note is tagged with its source: **B** brand website, **F** Fragrantica, **R** a retailer listing,
**C** added by hand. Each perfume also records where its data came from and when.

## Web lookup with OpenRouter (optional)

Searches for perfumes that aren't in the database can be looked up on the web by an AI model through
[OpenRouter](https://openrouter.ai). Put two settings in a `.env` file in the project folder (copy
`.env.example`):

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=vendor/model-name:free
```

Then restart the server. Roughly, a new perfume costs $0.005–0.03 in web-search fees with a free model, plus
the model's token price if you pick a paid one. Notes:

- **Pick a model** at https://openrouter.ai/models and copy its ID into `OPENROUTER_MODEL`. Free models end in
  `:free`. Choose one that supports tool use, because the lookup needs the web search tool.
- **Search isn't free, even with a free model.** OpenRouter charges a small fee per search, so the account
  needs some credits.
- **Free models have a daily limit:** 50 requests a day until the account has bought $10 of credits, then
  1,000 a day. If the limit is hit, `import-list --web` stops and you can run it again later. It picks up
  where it left off.
- **Smaller free models make more mistakes.** Every answer is checked before it's saved: it needs a name, a
  brand and at least one note; link-like or overlong "notes" are dropped; and unknown source tags become
  **R**. Still, skim what a new model adds (`export` to CSV) before trusting it.
- The model is asked to reply in JSON. If it answers in plain text instead, one cheap follow-up request
  (without web search) converts it. If that fails too, nothing is saved.

## Adding and updating perfumes

```bash
.venv/bin/python -m app.manage import my_perfumes.csv     # add/update from a spreadsheet (CSV) or JSON file
.venv/bin/python -m app.manage export all_perfumes.csv    # export everything, edit it, import it again
.venv/bin/python -m app.manage import-url https://brand.com/some-perfume   # read notes from a brand's page
.venv/bin/python -m app.manage import-url --file brand_urls.txt           # many pages, one per line
.venv/bin/python -m app.manage search "ganymed"           # test the website's search
.venv/bin/python -m app.manage list
.venv/bin/python -m app.manage delete SLUG
```

**Spreadsheets:** start from [data/perfume_template.csv](data/perfume_template.csv). It has one row per
perfume, and notes are separated by `;`. The `sources` column says where the row's notes came from
(`brand`, `fragrantica`, `retailer` or `curated`), and the optional `source_note` column
explains where sources agree or disagree. To tag one note differently, put braces after it:
`Suede {fragrantica}` or `Akigalawood {brand+fragrantica}`. Use `+`, not a comma, inside the braces. Importing a perfume that already exists (same brand, name and concentration)
updates it.

**Brand pages:** `import-url` identifies itself as `SniffyNoseBot`, follows each site's robots.txt and waits
10 seconds between pages on the same site. It reads "Top notes: …" style lists itself. If a page is laid out
differently and an OpenRouter key is set, the AI model reads the page instead. New notes are
merged with the ones already stored and tagged **B**. It refuses Fragrantica and Parfumo pages, because
those sites don't allow their databases to be copied. Link to them instead. Set `SNIFFY_CONTACT` in `.env`
to an email or URL so site owners can reach you.

## Filling the database

[data/wishlist.csv](data/wishlist.csv) is a to-do list of 42 popular perfumes (brand, name, concentration,
brand_url). Work through it with:

```bash
.venv/bin/python -m app.manage import-list data/wishlist.csv        # shows what's stored and what's left
.venv/bin/python -m app.manage import-list data/wishlist.csv --web  # looks up the rest on the web (paid, asks first)
```

Rows with a `brand_url` are read from the brand's page. Rows without one are listed as still to do, or with
`--web` they're looked up on the web, after the tool shows the estimated cost and you confirm.

**What to expect from brand sites:** in a test on 4 real brand pages (Nov 2026), none could be imported
automatically. Two sites turned the bot away on the first request (error 429), one site's robots.txt forbids
bots, and one describes its notes in sentences rather than as a list. The importer respects those blocks and
doesn't try to get around them. Expect most entries to be added by hand from a spreadsheet: see
[data/curated_batch_01.csv](data/curated_batch_01.csv) for 4 perfumes added this way from brands' own pages.
When a brand doesn't split its notes into top, heart and base, enter them all in the `heart` column only. The
site then shows them as a single "All notes" list instead of guessing levels.

## Growing the database without picking perfumes

**1. Let visitors decide.** Every website search that doesn't find a confident match is logged, with a
count of how often it was asked for. Searches that closely match a stored perfume (likely typos) are kept
apart. To see and work through the log:

```bash
.venv/bin/python -m app.manage queue                       # what visitors asked for that we don't have
.venv/bin/python -m app.manage process-queue --limit 20    # look up the 20 most-requested (asks first)
```

`process-queue` skips anything that has been added since by another route. Perfumes the web can't find are
marked `not_found` and never retried. If it hits the free model's daily limit, it stops, and the rest wait
for the next run.

To run it automatically every night at 3am on this Mac, run `crontab -e` and add this line:

```
0 3 * * * cd /Users/tuedungtruong/Documents/PythonProjects/sniffy-nose-project && .venv/bin/python -m app.manage process-queue --limit 20 --yes >> data/queue.log 2>&1
```

**2. Name a brand, get its lineup.** One request asks the model for a brand's current perfumes (from the
brand's own site where possible). Ones you don't have yet are added to the wishlist, and then you look
them up as usual:

If the brand sells online through Shopify (many niche houses do), its own store is the better source:
its product list is public, so the lineup is free and exact, and notes come from the brand's own text.

```bash
.venv/bin/python -m app.manage import-store "Miller Harris" millerharris.com --dry-run   # preview
.venv/bin/python -m app.manage import-store "Miller Harris" millerharris.com             # import
```

It skips candles, sets, travel sizes and other non-perfume products, merges sizes into one perfume, reads
"Top notes: ..." lists for free, and asks the AI (no web search) to read notes written as prose. Perfumes
whose text has no notes go onto the wishlist for `import-list --web`. Sites that block bots (robots.txt or
HTTP 429) are skipped; use `discover-brand` + `import-list --web` for those.

```bash
.venv/bin/python -m app.manage discover-brand "Creed" --dry-run   # preview, wishlist unchanged
.venv/bin/python -m app.manage discover-brand "Creed"             # add the new ones to data/wishlist.csv
.venv/bin/python -m app.manage import-list data/wishlist.csv --web --brands "Creed"
```

The model can list discontinued or misspelled names. Any it can't find during the lookup are skipped, and
those cost only a search fee.

## Project layout

| Piece | Where |
|---|---|
| Database: schema, saving, typo-tolerant search | `app/db.py` |
| Command-line tool for imports and exports | `app/manage.py` |
| Brand-page importer | `app/brand_import.py` |
| Web lookup for missing perfumes and note explanations (OpenRouter), answer checks | `app/lookup.py` |
| Notes dictionary (~90 notes) and glossary (~40 terms) | `app/data/notes.json`, `app/data/glossary.json` |
| All website wording (menus, headings, buttons, messages) | `app/data/site.json` |
| Note details, note-type intros, family colours | `app/data/note_details.json`, `app/data/note_types.json`, `app/data/families.json` |
| History timeline (with each milestone's picture search) and picture credits | `app/data/history.json`, `app/data/history_images.json` |
| Seed perfumes loaded by `init` | `app/data/seed_perfumes.json` |
| Website | `static/` |

## Editing the content

All content lives in `app/data/` and changes show on the site without a restart (refresh the page).
In `site.json`, `{name}` is filled in by the site, `[[word]]` becomes a hoverable glossary word, and counts
use `{"one": ..., "other": ...}`. After an edit, run the check:

```bash
.venv/bin/python -m app.manage check
```

To add a history milestone, add it to `history.json` with an `image` list of picture searches, then run
`.venv/bin/python -m app.history_images` to fetch its picture.

## Notes

- Ganymede and Sauvage were first compiled from retailer listings. Ganymede has since been checked against the
  brand's page; Sauvage hasn't.
- Fragrantica blocks automated visitors, so web lookups usually fall back to retailer pages (tagged **R**).
- Every web lookup is billed to the OpenRouter key in `.env`. Before you put the site online, set a credit
  limit on the key at openrouter.ai and add rate limiting.
- To trade cost for accuracy, change `OPENROUTER_MODEL` to a stronger (paid) model.
