"""AI-powered perfume lookup through OpenRouter: web research and note explanations.

Uses the model named in OPENROUTER_MODEL (free ones work) with OpenRouter's web search/fetch tools.

Every answer is checked by `validate_perfume` before anything is saved, so a model that returns
half-formed data produces an error instead of a bad database row.
"""

import json
import os
import re
import threading
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SOURCES = {"brand", "fragrantica", "retailer"}
MAX_NOTE_LENGTH = 60

_cache_lock = threading.Lock()


class LookupFailed(Exception):
    """A lookup that couldn't produce usable data. `status` mirrors an HTTP status for the API layer."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ---------- settings ----------

def _setting(name: str) -> str:
    # Tolerate stray spaces or quotes pasted into .env.
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()


def has_credentials() -> bool:
    return bool(_setting("OPENROUTER_API_KEY") and _setting("OPENROUTER_MODEL"))


def describe_provider() -> str:
    return f"OpenRouter ({_setting('OPENROUTER_MODEL') or 'no model set'})"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------- tiny JSON file cache ----------

def _cache_path(kind: str) -> Path:
    return CACHE_DIR / f"{kind}.json"


def cache_get(kind: str, key: str):
    path = _cache_path(kind)
    if not path.exists():
        return None
    with _cache_lock:
        return json.loads(path.read_text()).get(key)


def cache_put(kind: str, key: str, value) -> None:
    path = _cache_path(kind)
    with _cache_lock:
        data = json.loads(path.read_text()) if path.exists() else {}
        data[key] = value
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---------- schemas ----------

NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {"type": "string", "enum": ["brand", "fragrantica", "retailer"]},
        },
    },
    "required": ["name", "sources"],
    "additionalProperties": False,
}

PERFUME_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "name": {"type": "string"},
        "brand": {"type": "string"},
        "year": {"type": "string"},
        "perfumer": {"type": "string"},
        "concentration": {"type": "string"},
        "families": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "top": {"type": "array", "items": NOTE_SCHEMA},
        "heart": {"type": "array", "items": NOTE_SCHEMA},
        "base": {"type": "array", "items": NOTE_SCHEMA},
        "flat_pyramid": {"type": "boolean"},
        "brand_url": {"type": "string"},
        "fragrantica_url": {"type": "string"},
        "source_note": {"type": "string"},
    },
    "required": [
        "found", "name", "brand", "year", "perfumer", "concentration", "families",
        "description", "top", "heart", "base", "flat_pyramid", "brand_url",
        "fragrantica_url", "source_note",
    ],
    "additionalProperties": False,
}

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "term": {"type": "string"},
        "family": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["term", "family", "description"],
    "additionalProperties": False,
}

RESEARCH_PROMPT = """You are a fragrance researcher. Find the note pyramid for this perfume: "{query}".

Use web search and web fetch to check two sources:
1. The brand's official website (the product page for this exact perfume).
2. Fragrantica (fragrantica.com) - its page for this exact perfume.
Retailer pages are a fallback only if one of those two can't be reached.

Rules:
- Match the exact perfume and concentration the user asked for (an EDP and its Extrait are different pyramids). If the query is ambiguous, pick the original/most popular version and say so in source_note.
- top = opening notes, heart = middle notes, base = dry-down notes.
- For each note, list which sources include it: "brand", "fragrantica", or "retailer" (any other shop or site
  you had to fall back to). Only tag a source whose page you actually read. Use title case note names ("Mandarin Orange"), merge obvious duplicates.
- If a perfume has no pyramid (only a flat list), put all notes in heart and set flat_pyramid true.
- description: 2-3 friendly sentences on what it smells like overall, in plain language.
- families: 1-4 scent families (e.g. "Woody", "Citrus", "Amber", "Floral", "Leather").
- source_note: one short sentence on where the sources agree or disagree.
- Use empty strings for unknown fields. If you can't find the perfume at all, set found false.
- Never invent notes that no source lists."""


# ---------- validation ----------

def _text(value, limit: int = 2000) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def _url(value) -> str:
    v = _text(value, 500)
    return v if re.match(r"^https?://", v) else ""


def _note_key(name: str) -> str:
    """Same key for spellings of one note: accents, case and an "accord"/"absolute"/"oil" suffix don't matter."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"\b(accord|absolute|oil|notes?)\b", "", plain)
    return re.sub(r"[^a-z0-9]+", "", plain)


PLACEHOLDER = re.compile(r"^(unknown|n/?a|none|not (given|stated|listed|specified)|-|\?)$", re.I)
_SMALL_WORDS = {"de", "du", "des", "of", "the", "and", "la", "le", "les", "d", "l", "et", "in", "with"}


def _whole_sentences(text: str, limit: int) -> str:
    """Shorten to at most `limit` characters without stopping mid-sentence."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 0 else ""  # no sentence fits: better empty than broken


def tidy_note(name: str) -> str:
    """Consistent capitalisation: "fresh rose petals" -> "Fresh Rose Petals", "rose de mai" -> "Rose de Mai"."""
    words = re.split(r"(\s+|-)", name.strip())
    out = []
    for i, w in enumerate(words):
        if w.lower() in _SMALL_WORDS and i:
            out.append(w.lower())
        elif w[:1].islower():
            out.append(w[:1].upper() + w[1:])
        else:
            out.append(w)
    return "".join(out)


def validate_perfume(data) -> dict:
    """Normalise a model's perfume answer and reject anything unusable. Returns {"found": False} when the
    model says (or effectively says) it found nothing."""
    if not isinstance(data, dict):
        raise LookupFailed("The model's answer wasn't a perfume record.")
    if not data.get("found"):
        return {"found": False}

    out = {k: _text(data.get(k), 200) for k in ("name", "brand", "year", "perfumer", "concentration")}
    out = {k: "" if PLACEHOLDER.match(v) else v for k, v in out.items()}  # "Unknown" means empty
    if not out["name"] or not out["brand"]:
        raise LookupFailed("The model's answer was missing the perfume's name or brand.")
    out["description"] = _whole_sentences(_text(data.get("description"), 5000), 800)
    out["source_note"] = _text(data.get("source_note"), 500)
    out["brand_url"] = _url(data.get("brand_url"))
    out["fragrantica_url"] = _url(data.get("fragrantica_url"))
    families = data.get("families") if isinstance(data.get("families"), list) else []
    out["families"] = [f for f in (_text(x, 30) for x in families) if f][:5]

    total = 0
    for level in ("top", "heart", "base"):
        items = data.get(level) if isinstance(data.get(level), list) else []
        notes, seen = [], {}
        for item in items:
            name = tidy_note(_text(item.get("name") if isinstance(item, dict) else item, 200))
            if not name or len(name) > MAX_NOTE_LENGTH or "http" in name.lower():
                continue
            sources = item.get("sources", []) if isinstance(item, dict) else []
            sources = [s for s in sources if s in ALLOWED_SOURCES] if isinstance(sources, list) else []
            # A note with no stated source still came from some website, never from our own curation.
            sources = sorted(set(sources)) or ["retailer"]  # the model sometimes repeats a source
            key = _note_key(name)
            if key in seen:  # "Mate" after "Maté", "Paprika Accord" after "Paprika": merge the sources
                seen[key]["sources"] = sorted(set(seen[key]["sources"]) | set(sources))
                continue
            seen[key] = {"name": name, "sources": sources}
            notes.append(seen[key])
        out[level] = notes
        total += len(notes)
    if total == 0:
        return {"found": False}
    out["flat_pyramid"] = bool(data.get("flat_pyramid")) or (bool(out["heart"]) and not out["top"] and not out["base"])
    out["found"] = True
    return out


def _extract_json(text: str):
    """Pull the first JSON object out of a model reply (bare, or inside a ```json block)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    if not candidate:
        raise ValueError("no JSON object in reply")
    return json.loads(candidate)


# ---------- OpenRouter ----------

def _openrouter_call(messages: list[dict], web: bool) -> str:
    body = {"model": _setting("OPENROUTER_MODEL"), "messages": messages}
    if web:
        search = {"type": "openrouter:web_search", "parameters": {"max_results": 5}}
        if _setting("OPENROUTER_SEARCH_ENGINE"):
            search["parameters"]["engine"] = _setting("OPENROUTER_SEARCH_ENGINE")
        body["tools"] = [
            search,
            # The "openrouter" fetch engine is free; cap page size so long pages don't eat tokens.
            {"type": "openrouter:web_fetch", "parameters": {"engine": "openrouter", "max_content_tokens": 20000}},
        ]
        body["max_tool_calls"] = 10
    req = urllib.request.Request(
        os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_setting('OPENROUTER_API_KEY')}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001 - error bodies aren't always JSON
            pass
        if e.code == 401:
            raise LookupFailed("OpenRouter rejected the API key. Check OPENROUTER_API_KEY in .env.", 401)
        if e.code == 402:
            raise LookupFailed("The OpenRouter account is out of credits.", 402)
        if e.code == 429:
            raise LookupFailed("OpenRouter's rate limit was reached (free models allow a limited number of "
                               "requests per day). Try again later.", 429)
        raise LookupFailed(f"OpenRouter error {e.code}: {detail or e.reason}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise LookupFailed(f"Couldn't reach OpenRouter ({e}).", 503)

    if data.get("error"):
        raise LookupFailed(f"OpenRouter error: {data['error'].get('message', data['error'])}")
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if isinstance(content, list):  # some providers return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content


def _openrouter_json(prompt: str, schema: dict, web: bool) -> dict:
    instructions = (
        prompt
        + "\n\nWhen you're done, reply with only one JSON object (no other text) that matches this JSON schema:\n"
        + json.dumps(schema)
    )
    reply = _openrouter_call([{"role": "user", "content": instructions}], web)
    try:
        return _extract_json(reply)
    except ValueError:
        pass
    if not reply.strip():
        raise LookupFailed("The model returned an empty answer.")
    # One cheap repair pass without web tools: reformat what the model already found.
    repaired = _openrouter_call([{
        "role": "user",
        "content": "Convert the text below into one JSON object matching this schema. Use only information in "
                   "the text; don't add anything. Reply with the JSON object only.\n\nSchema:\n"
                   + json.dumps(schema) + "\n\nText:\n" + reply,
    }], web=False)
    try:
        return _extract_json(repaired)
    except ValueError:
        raise LookupFailed("The model didn't return usable data. Try again, or pick a different OPENROUTER_MODEL.")


# ---------- shared entry points ----------

def _ask_json(prompt: str, schema: dict, web: bool = False) -> dict:
    return _openrouter_json(prompt, schema, web)


def research_perfume(query: str) -> dict:
    """Look a perfume up on the web and return a validated record. The caller stores it in the database."""
    raw = _ask_json(RESEARCH_PROMPT.format(query=query), PERFUME_SCHEMA, web=True)
    return validate_perfume(raw)


def extract_from_page(page_text: str, url: str, hint: str = "") -> dict:
    """Read a perfume pyramid out of one brand product page's text (no web access)."""
    raw = _ask_json(
        f"This is the text of a perfume product page from the brand's own website ({url}). "
        + (f'It should be about "{hint}". ' if hint else "")
        + "Extract the perfume and its notes into the schema. Tag every note's source as \"brand\". "
        "Only use notes the page actually lists; if it lists no notes, set found false. "
        "description: 2-3 short, complete sentences in your own words on what it smells like overall; "
        "never copy the page's story text. "
        "Put the URL in brand_url and leave fragrantica_url empty.\n\n<page>\n" + page_text + "\n</page>",
        PERFUME_SCHEMA,
    )
    return validate_perfume(raw)


def explain_term(term: str) -> dict:
    key = slugify(term)
    cached = cache_get("terms", key)
    if cached:
        return cached
    raw = _ask_json(
        f'Explain "{term}" as it is used in perfumery, for a curious beginner. '
        "description: 2-3 short sentences on what it smells like or means, and where it comes from "
        "(natural or synthetic). family: one scent family (Citrus, Floral, Woody, Amber, Fresh, Green, "
        "Fruity, Gourmand, Spicy, Musky, Leather, Aquatic, Earthy, Aromatic) or 'Term' if it's not a note.",
        EXPLAIN_SCHEMA,
    )
    if not isinstance(raw, dict) or not _text(raw.get("description")):
        raise LookupFailed("The model didn't return an explanation.")
    result = {"term": _text(raw.get("term"), 80) or term, "family": _text(raw.get("family"), 30),
              "description": _text(raw.get("description"), 600)}
    cache_put("terms", key, result)
    return result


# ---------- brand lineup discovery ----------

LINEUP_SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {"type": "string"},
        "perfumes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "concentration": {"type": "string"}},
                "required": ["name", "concentration"],
                "additionalProperties": False,
            },
        },
        "source_url": {"type": "string"},
    },
    "required": ["brand", "perfumes", "source_url"],
    "additionalProperties": False,
}

LINEUP_PROMPT = """List the perfumes currently sold by the fragrance house "{brand}".

Use web search and web fetch. Prefer the brand's own website (its fragrance or "all perfumes" pages);
use retailers only if the brand site can't be reached.

Rules:
- Only wearable perfumes: no candles, home scents, body care, hair mists, gift sets, samples or discovery sets.
- One entry per distinct perfume and concentration ("Eau de Parfum", "Eau de Toilette", "Extrait de Parfum",
  "Parfum", "Cologne"). Don't list bottle sizes separately. Use an empty string if the concentration isn't shown.
- Use the brand's own spelling of each name, without the brand name in front.
- List at most {limit} perfumes, best-known first.
- Never invent names. If you can't find the brand, return an empty list.
- brand: the brand's official name. source_url: the main page you used."""


def discover_lineup(brand: str, limit: int = 30) -> dict:
    """Ask the model for a brand's current perfume lineup. Returns {"brand", "perfumes": [{name, concentration}],
    "source_url"}, cleaned and capped at `limit`."""
    raw = _ask_json(LINEUP_PROMPT.format(brand=brand, limit=limit), LINEUP_SCHEMA, web=True)
    if not isinstance(raw, dict):
        raise LookupFailed("The model's answer wasn't a perfume list.")
    out_brand = _text(raw.get("brand"), 80) or brand
    items = raw.get("perfumes") if isinstance(raw.get("perfumes"), list) else []
    perfumes, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 200)
        conc = _text(item.get("concentration"), 40)
        if not name or len(name) > 80 or "http" in name.lower():
            continue
        # Drop a repeated brand prefix ("Amouage Interlude Man" -> "Interlude Man").
        if name.lower().startswith(out_brand.lower() + " "):
            name = name[len(out_brand) + 1:].strip()
        key = (_note_key(name), _note_key(conc))
        if key in seen:
            continue
        seen.add(key)
        perfumes.append({"name": name, "concentration": conc})
        if len(perfumes) >= limit:
            break
    return {"brand": out_brand, "perfumes": perfumes, "source_url": _url(raw.get("source_url"))}
