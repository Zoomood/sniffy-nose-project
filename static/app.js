"use strict";

const app = document.getElementById("app");
const tooltip = document.getElementById("tooltip");

const state = { notes: [], glossary: [], status: { live: false, count: 0, sample: [] }, results: {}, explained: {} };

// All wording, family colours and note-type intros come from the server (app/data/site.json,
// families.json, note_types.json), so content can change without touching this file.
const site = { text: {}, families: new Map(), types: new Map() };

function textAt(key) {
  return key.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), site.text);
}
// t("note.shelf_count", { n: 3, name: "Rose" }): fills {placeholders}; counts pick "one" or "other".
function t(key, vals = {}) {
  let s = textAt(key);
  if (s && typeof s === "object" && "other" in s) s = vals.n === 1 ? s.one : s.other;
  if (typeof s !== "string") return key;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? String(vals[k]) : m));
}
const tx = (key, vals) => esc(t(key, vals)); // escaped, ready for HTML
// Text with [[word]] marks turned into hoverable dictionary/glossary words.
const richText = (str) => esc(str).replace(/\[\[(.+?)\]\]/g, (m, w) =>
  `<button type="button" class="term" data-term="${esc(linkTargets.get(w.toLowerCase()) || w)}">${w}</button>`);
const rich = (key, vals) => richText(t(key, vals));

const family = (name) => site.families.get((name || "").toLowerCase()) || site.families.get("other") || {};
const famColor = (name) => family(name).chip || "var(--f-other)";

const LEVEL_KEYS = ["top", "heart", "base"];
const levels = () => LEVEL_KEYS.map((key, i) => ({ key, n: i + 1, ...(textAt(`perfume.levels.${key}`) || {}) }));

// ---------- le flacon: the living bottle ----------

// Clear, saturated liquid colours (the chips keep their pastel family colours).
const liquidColor = (name) => family(name).liquid || "var(--f-other)";
const defaultLayers = () => [
  { key: "base", color: liquidColor("amber") }, { key: "heart", color: liquidColor("floral") }, { key: "top", color: liquidColor("citrus") },
];
const LAYER_WEIGHT = { base: 40, heart: 33, top: 27 };

function dominantFamily(notes) {
  const counts = {};
  for (const n of notes) {
    const fam = findEntry(n.name)?.family;
    if (fam) counts[fam] = (counts[fam] || 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] || "other";
}

let flaconSeq = 0;

// layers: bottom to top, [{ key, color }]. atomizer: swap the stopper for a bulb spray.
function flaconSvg(layers, { atomizer = false, cls = "", label = t("perfume.bottle_label") } = {}) {
  const id = `fl${++flaconSeq}`;
  const cavTop = 134, cavBottom = 254, cavH = cavBottom - cavTop;
  const sum = layers.reduce((s, l) => s + (LAYER_WEIGHT[l.key] || 100), 0) || 1;
  let y = cavBottom;
  const liquid = layers.map((l, i) => {
    const h = (cavH * (LAYER_WEIGHT[l.key] || 100)) / sum;
    y -= h;
    return `<rect class="fl-liquid" data-layer="${esc(l.key)}" x="42" y="${y.toFixed(1)}" width="116" height="${(h + 0.6).toFixed(1)}"
      fill="${esc(l.color)}" style="animation-delay:${(i * 0.45).toFixed(2)}s"/>`;
  }).join("");
  const pourDone = (layers.length * 0.45 + 0.5).toFixed(2);

  const stopper = `
    <polygon class="fl-glass" points="66,92 134,92 148,60 134,24 66,24 52,60"/>
    <polyline class="fl-facet" points="52,60 100,50 148,60"/>
    <line class="fl-facet" x1="100" y1="24" x2="100" y2="92"/>
    <line class="fl-facet" x1="66" y1="24" x2="80" y2="92" style="opacity:.45"/>
    <line class="fl-facet" x1="134" y1="24" x2="120" y2="92" style="opacity:.45"/>
    <path class="fl-shine" d="M62 58 L72 30 L76 30 L67 58 Z"/>`;
  const spray = `
    <rect x="84" y="70" width="32" height="24" rx="3" fill="url(#${id}-gold)"/>
    <rect x="70" y="77" width="15" height="7" rx="1.5" fill="url(#${id}-gold)"/>
    <g class="fl-mist" fill="var(--gold)"><circle cx="66" cy="80" r="3"/><circle cx="64" cy="76" r="2.4"/><circle cx="64" cy="84" r="2.2"/><circle cx="60" cy="80" r="1.8"/></g>
    <path d="M116 80 Q150 78 158 50" fill="none" stroke="var(--gold)" stroke-width="2.5"/>
    <g class="fl-bulb"><ellipse cx="166" cy="34" rx="16" ry="20" fill="#f59ab2"/>
      <path d="M158 24 C155 32 156 42 161 48" fill="none" stroke="#fff" stroke-width="2" opacity=".6"/></g>
    <line x1="166" y1="54" x2="172" y2="82" stroke="var(--gold)" stroke-width="1.6"/>
    <path d="M166 82 L178 82 L182 106 L162 106 Z" fill="url(#${id}-gold)"/>`;

  return `
  <svg class="flacon ${cls}" viewBox="${atomizer ? "20 10 180 290" : "20 14 160 286"}" role="img" aria-label="${esc(label)}">
    <defs>
      <linearGradient id="${id}-gold" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#f6e3a1"/><stop offset=".5" stop-color="#d4af55"/><stop offset="1" stop-color="#9c7a2e"/>
      </linearGradient>
      <linearGradient id="${id}-light" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#fff" stop-opacity=".5"/><stop offset=".2" stop-color="#fff" stop-opacity=".08"/>
        <stop offset=".7" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".18"/>
      </linearGradient>
      <linearGradient id="${id}-depth" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#fff" stop-opacity=".12"/><stop offset="1" stop-color="#000" stop-opacity=".16"/>
      </linearGradient>
      <linearGradient id="${id}-sweep" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>
      </linearGradient>
      <clipPath id="${id}-body"><polygon points="42,110 158,110 170,122 170,268 158,280 42,280 30,268 30,122"/></clipPath>
      <filter id="${id}-blur" x="-20%" y="-200%" width="140%" height="500%"><feGaussianBlur stdDeviation="4"/></filter>
    </defs>
    <ellipse cx="100" cy="288" rx="74" ry="7" fill="#1f2747" opacity=".16" filter="url(#${id}-blur)"/>
    <polygon class="fl-glass" points="42,110 158,110 170,122 170,268 158,280 42,280 30,268 30,122"/>
    ${liquid}
    ${layers.length ? `<line class="fl-meniscus" x1="42" y1="${cavTop}" x2="158" y2="${cavTop}" style="animation-delay:${pourDone}s"/>` : ""}
    <rect x="42" y="122" width="116" height="132" fill="url(#${id}-depth)" pointer-events="none"/>
    <rect x="30" y="110" width="140" height="170" fill="url(#${id}-light)" clip-path="url(#${id}-body)" pointer-events="none"/>
    <rect class="fl-base" x="42" y="254" width="116" height="18" pointer-events="none"/>
    <g clip-path="url(#${id}-body)" pointer-events="none">
      <rect class="fl-sweep" x="0" y="100" width="34" height="190" fill="url(#${id}-sweep)" transform="skewX(-14)"/>
    </g>
    <rect class="fl-shine" x="38" y="116" width="7" height="150" rx="3.5" pointer-events="none"/>
    <rect class="fl-shine" x="161" y="120" width="2.5" height="140" style="opacity:.45" pointer-events="none"/>
    <rect x="${atomizer ? 88 : 80}" y="92" width="${atomizer ? 24 : 40}" height="20" rx="3" fill="url(#${id}-gold)"/>
    <rect x="${atomizer ? 88 : 80}" y="98" width="${atomizer ? 24 : 40}" height="1.5" fill="#fff" opacity=".5"/>
    ${atomizer ? spray : stopper}
  </svg>`;
}



const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

// ---------- lookup helpers ----------

let entryIndex = new Map();
let noteKeysByLength = [];
let linkRegex = null;
let linkTargets = new Map();

function buildIndexes() {
  entryIndex = new Map();
  for (const e of state.notes) for (const n of [e.name, ...(e.aliases || [])]) entryIndex.set(slug(n), { ...e, kind: "note" });
  for (const e of state.glossary) for (const n of [e.name, ...(e.aliases || [])]) {
    if (!entryIndex.has(slug(n))) entryIndex.set(slug(n), { ...e, family: "Term", kind: "term" });
  }
  noteKeysByLength = [...entryIndex.keys()].filter((k) => entryIndex.get(k).kind === "note").sort((a, b) => b.length - a.length);

  linkTargets = new Map();
  const noAutolink = new Set(textAt("autolink_skip") || []); // words too generic to underline every time
  const words = [];
  for (const e of [...state.glossary, ...state.notes]) {
    for (const n of [e.name, ...(e.aliases || [])]) {
      const k = slug(n);
      if (noAutolink.has(k) || linkTargets.has(n.toLowerCase())) continue;
      linkTargets.set(n.toLowerCase(), e.name);
      words.push(n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    }
  }
  words.sort((a, b) => b.length - a.length);
  linkRegex = new RegExp(`\\b(${words.join("|")})\\b`, "gi");
}

function findEntry(name) {
  const k = slug(name);
  if (entryIndex.has(k)) return entryIndex.get(k);
  // "Calabrian Bergamot" -> Bergamot, "Madagascar Vanilla" -> Vanilla
  const hit = noteKeysByLength.find((key) => k.endsWith(`-${key}`) || k.startsWith(`${key}-`));
  return hit ? entryIndex.get(hit) : null;
}

// Wrap glossary/dictionary words in plain text with hoverable buttons (first occurrence each).
function linkify(text, { exclude = "" } = {}) {
  const escaped = esc(text);
  if (!linkRegex) return escaped;
  const seen = new Set([exclude.toLowerCase()]);
  return escaped.replace(linkRegex, (m) => {
    const target = linkTargets.get(m.toLowerCase());
    if (!target || seen.has(target.toLowerCase())) return m;
    seen.add(target.toLowerCase());
    return `<button type="button" class="term" data-term="${esc(target)}">${m}</button>`;
  });
}

async function explain(term) {
  const local = findEntry(term);
  if (local) return local;
  if (state.explained[term]) return state.explained[term];
  const res = await fetch(`/api/explain?term=${encodeURIComponent(term)}`);
  const data = res.ok ? await res.json() : { term, description: t("tooltip.failed") };
  const entry = { name: data.term || term, family: data.family, description: data.description, emoji: "", kind: "ai" };
  state.explained[term] = entry;
  return entry;
}

async function api(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const err = new Error((typeof d === "string" ? d : d?.message) || t("common.request_failed", { status: res.status }));
    err.status = res.status;
    err.suggestions = d?.suggestions || [];
    err.canLookup = !!d?.can_lookup;
    throw err;
  }
  return data;
}

// ---------- tooltip ----------

let tipTarget = null;

async function showTip(el) {
  tipTarget = el;
  const term = el.dataset.term;
  tooltip.innerHTML = `<div class="tt-head"><b>${esc(term)}</b></div><p>${tx("tooltip.loading")}</p>`;
  tooltip.hidden = false;
  placeTip(el);
  const entry = await explain(term);
  if (tipTarget !== el) return;
  const famLabel = entry.family && entry.family !== "Term" ? entry.family : entry.kind === "term" ? t("tooltip.term") : "";
  tooltip.innerHTML = `
    <div class="tt-head">
      ${entry.image ? `<img class="tt-img ${esc(entry.image_kind || "")}" src="/static/notes/${esc(entry.image)}" alt="">` : ""}
      <b>${esc(entry.name === term || !el.classList.contains("chip") ? entry.name : term)}</b>
      ${famLabel ? `<span class="tt-fam" style="--fam:${famColor(entry.family)}">${esc(famLabel)}</span>` : ""}
    </div>
    ${el.classList.contains("chip") && slug(entry.name) !== slug(term) ? `<p><em>${tx("tooltip.kind_of", { name: entry.name })}</em></p>` : ""}
    <p>${esc(entry.description)}</p>
    ${entry.kind === "ai" ? `<div class="tt-src">${tx("tooltip.ai")}</div>` : ""}
    ${entry.kind === "note" && el.classList.contains("chip") ? `<div class="tt-src">${tx("tooltip.open_note")}</div>` : ""}`;
  placeTip(el);
}

function placeTip(el) {
  const r = el.getBoundingClientRect();
  const t = tooltip.getBoundingClientRect();
  let left = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 8);
  let top = r.top - t.height - 10;
  if (top < 8) top = r.bottom + 10;
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hideTip() {
  tipTarget = null;
  tooltip.hidden = true;
}

let lastPointer = "mouse";
document.addEventListener("pointerdown", (e) => { lastPointer = e.pointerType; });
document.addEventListener("pointerover", (e) => {
  if (e.pointerType !== "mouse") return;
  const el = e.target.closest("[data-term]");
  if (el && el !== tipTarget) showTip(el);
});
document.addEventListener("pointerout", (e) => {
  if (e.pointerType !== "mouse") return;
  const el = e.target.closest("[data-term]");
  if (el && !el.contains(e.relatedTarget)) hideTip();
});
document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-term]"); if (el) showTip(el); });
document.addEventListener("focusout", (e) => { if (e.target.closest("[data-term]")) hideTip(); });
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-term]");
  if (el && lastPointer !== "mouse") { tipTarget === el && !tooltip.hidden ? hideTip() : showTip(el); return; }
  if (el?.classList.contains("chip")) {
    const entry = findEntry(el.dataset.term);
    if (entry?.kind === "note") { location.hash = `#/note/${slug(entry.name)}`; return; }
  }
  if (!el) hideTip();
});
window.addEventListener("scroll", () => tipTarget && placeTip(tipTarget), { passive: true });

// ---------- views ----------

// Highlights the tab you're in (a perfume opened from a note's shelf stays under Notes dictionary).
function setNav() {
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav.tab));
}

function renderHome() {
  setNav();
  const { sample } = state.status;
  app.innerHTML = `
    <section class="hero">
      <div class="hero-text">
        <p class="eyebrow">${tx("home.eyebrow")}</p>
        <h1>${tx("home.title_line1")}<br><em>${tx("home.title_line2")}</em></h1>
        <p class="lede">${rich("home.lede")}</p>
        <form class="finder" id="finder" autocomplete="off">
          <div class="search-wrap">
            <input id="q" type="search" placeholder="${tx("home.placeholder")}" aria-label="${tx("home.input_label")}"
              required minlength="2" role="combobox" aria-expanded="false" aria-controls="suggestions" aria-autocomplete="list">
            <ul id="suggestions" class="suggestions" role="listbox" hidden></ul>
          </div>
          <button class="btn" type="submit">${tx("home.search")}</button>
        </form>
        ${sample.length ? `<div class="suggest"><span>${tx("home.try")}</span>${sample.map((d) => `<button type="button" data-q="${esc(d)}">${esc(d)}</button>`).join("")}</div>` : ""}
      </div>
      ${flaconSvg(defaultLayers(), { cls: "hero-flacon", label: t("home.bottle_label") })}
    </section>
    <section class="how">
      ${(textAt("home.steps") || []).map((s) => `<div><span>${esc(s.n)}</span><b>${esc(s.title)}</b><p>${richText(s.text)}</p></div>`).join("")}
    </section>`;
  const input = document.getElementById("q");
  const list = document.getElementById("suggestions");
  let items = [];
  let active = -1;
  let timer = null;
  let seq = 0;

  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const draw = () => {
    list.innerHTML = items.map((s, i) => `
      <li role="option" id="sugg-${i}" aria-selected="${i === active}" data-slug="${esc(s.slug)}">
        <b>${esc(s.name)}</b> <span>${esc(s.brand)}${s.concentration ? ` · ${esc(s.concentration)}` : ""}</span>
      </li>`).join("");
    list.hidden = !items.length;
    input.setAttribute("aria-expanded", String(!!items.length));
    input.setAttribute("aria-activedescendant", active >= 0 ? `sugg-${active}` : "");
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { items = []; return close(); }
    timer = setTimeout(async () => {
      const mine = ++seq;
      try {
        const res = await api(`/api/suggest?q=${encodeURIComponent(q)}`);
        if (mine !== seq) return; // a newer keystroke already answered
        items = res;
        active = -1;
        draw();
      } catch { /* suggestions are optional */ }
    }, 150);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { active = (active + 1) % items.length; draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = (active - 1 + items.length) % items.length; draw(); e.preventDefault(); }
    else if (e.key === "Escape") close();
  });
  list.addEventListener("pointerdown", (e) => {
    const li = e.target.closest("[data-slug]");
    if (li) { e.preventDefault(); location.hash = `#/perfume/${li.dataset.slug}`; }
  });
  input.addEventListener("blur", () => setTimeout(close, 100));

  document.getElementById("finder").addEventListener("submit", (e) => {
    e.preventDefault();
    if (active >= 0 && items[active]) { location.hash = `#/perfume/${items[active].slug}`; return; }
    const q = input.value.trim();
    if (q) location.hash = `#/p/${encodeURIComponent(q)}`;
  });
  app.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/p/${encodeURIComponent(b.dataset.q)}`; }));
}

function renderLoading(messages) {
  app.innerHTML = `
    <div class="loading">
      ${flaconSvg(defaultLayers(), { atomizer: true, cls: "loading-flacon", label: t("search.bottle_label") })}
      <p id="loading-msg" aria-live="polite">${esc(messages[0])}</p>
    </div>`;
  let i = 0;
  const timer = setInterval(() => {
    const el = document.getElementById("loading-msg");
    if (!el) return clearInterval(timer);
    i = (i + 1) % messages.length;
    el.textContent = messages[i];
  }, 2600);
  return () => clearInterval(timer);
}

function renderError(message) {
  app.innerHTML = `
    <div class="error-card">
      <h2>${tx("common.error_title")}</h2>
      <p>${esc(message)}</p>
      <a class="btn" href="#/">${tx("common.try_again")}</a>
    </div>`;
}

const srcLabel = (s) => textAt(`perfume.sources.${s}`) || s;

// Compare decoded, so a link with "%27" and one with "'" (Poudre d'Or) both count as the same search.
function stillSearching(query) {
  try { return decodeURIComponent(location.hash) === `#/p/${query}`; } catch { return false; }
}

async function renderSearch(query, web = false) {
  setNav();
  const stop = renderLoading(state.status.live ? textAt("search.messages") || [t("common.checking")] : [t("common.checking")]);
  let perfume;
  try {
    perfume = await api(`/api/search?q=${encodeURIComponent(query)}${web ? "&web=true" : ""}`);
  } catch (err) {
    stop();
    if (!stillSearching(query)) return;
    return err.status === 404 ? renderNotFound(query, err) : renderError(err.message);
  }
  stop();
  if (!stillSearching(query)) return; // user navigated away
  state.results[perfume.slug] = perfume;
  replaceHash(`#/perfume/${perfume.slug}`);
  renderPerfumeView(perfume);
}

async function renderPerfumeSlug(slugId) {
  setNav();
  let perfume = state.results[slugId];
  if (!perfume) {
    const stop = renderLoading([t("common.checking")]);
    try {
      perfume = await api(`/api/perfume/${encodeURIComponent(slugId)}`);
      state.results[slugId] = perfume;
    } catch (err) {
      stop();
      return renderError(err.message);
    }
    stop();
  }
  renderPerfumeView(perfume);
}

function renderNotFound(query, err) {
  const sugg = err.suggestions || [];
  app.innerHTML = `
    <div class="error-card">
      <h2>${tx("search.not_found_title")}</h2>
      <p>${esc(err.message)}</p>
      ${sugg.length ? `<p>${tx("search.did_you_mean")}</p><div class="did-you-mean">${sugg.map((s) =>
        `<a class="btn ghost small" href="#/perfume/${esc(s.slug)}">${esc(s.brand)} ${esc(s.name)}${s.concentration ? ` <small>${esc(s.concentration)}</small>` : ""}</a>`).join("")}</div>` : ""}
      <div class="did-you-mean">
        ${err.canLookup ? `<button type="button" class="btn" id="web-lookup">${tx("search.search_web")}</button>` : ""}
        <a class="btn ${err.canLookup ? "ghost" : ""}" href="#/">${tx("search.search_again")}</a>
      </div>
    </div>`;
  document.getElementById("web-lookup")?.addEventListener("click", () => renderSearch(query, true));
}

function renderPerfumeView(perfume) {
  const allNotes = LEVEL_KEYS.flatMap((k) => perfume[k] || []);
  const tiers = perfume.flat_pyramid ? [{ key: "heart", n: "", ...(textAt("perfume.flat") || {}) }] : levels();
  const meta = [
    perfume.concentration && linkify(perfume.concentration),
    perfume.year && esc(perfume.year),
    perfume.perfumer && `<button type="button" class="term" data-term="${tx("perfume.nose")}">${tx("perfume.nose")}</button> ${esc(perfume.perfumer)}`,
  ].filter(Boolean);
  const fragUrl = perfume.fragrantica_url || `https://www.fragrantica.com/search/?query=${encodeURIComponent(`${perfume.brand} ${perfume.name}`)}`;
  const delays = { base: 0, heart: 0.35, top: 0.7 };

  app.innerHTML = `
    <div class="page-back">${backLinks()}</div>
    <article class="bottle" aria-label="${esc(perfume.name)}">
      <div class="bottle-stopper" aria-hidden="true"></div>
      <div class="bottle-collar" aria-hidden="true"></div>
      <div class="bottle-glass">
        <div class="glass-shape" aria-hidden="true"></div>
        <div class="bottle-inner">
          <header class="bottle-label">
            <div class="label-brand">${esc(perfume.brand)}</div>
            <h1>${esc(perfume.name)}</h1>
            ${meta.length ? `<div class="label-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>` : ""}
            ${perfume.families?.length ? `<div class="label-fams">${perfume.families.map((f) => esc(f)).join(" · ")}</div>` : ""}
          </header>
          ${tiers.map((l) => layerHtml(l, perfume[l.key] || [], delays[l.key] ?? 0)).join("")}
        </div>
      </div>
      <div class="bottle-shadow" aria-hidden="true"></div>
    </article>

    <section class="after">
      ${perfume.description ? `<p class="description">${linkify(perfume.description)}</p>` : ""}
      <div class="links">
        ${perfume.brand_url ? `<a class="btn" href="${esc(perfume.brand_url)}" target="_blank" rel="noopener">${tx("perfume.brand_site")}</a>` : ""}
        <a class="btn ghost" href="${esc(fragUrl)}" target="_blank" rel="noopener">${tx("perfume.fragrantica")}</a>
      </div>
      <div class="section-title"><h2>${tx("perfume.profile_title")}</h2><small>${tx("perfume.profile_sub")}</small></div>
      ${profileHtml(allNotes)}
      ${provenanceHtml(perfume)}
    </section>`;
}

// One liquid layer of the bottle: its notes float inside it.
function layerHtml(level, notes, delay) {
  const fam = dominantFamily(notes);
  return `
    <section class="layer" data-layer="${esc(level.key)}" style="--liq:${liquidColor(notes.length ? fam : "other")};animation-delay:${delay}s">
      <div class="layer-head">
        ${level.n ? `<span class="layer-n">0${level.n}</span>` : ""}
        <h2><button type="button" class="term" data-term="${esc(level.term)}">${esc(level.title)}</button></h2>
        <span class="layer-when">${esc(level.when)}</span>
      </div>
      <div class="notes">
        ${notes.length ? notes.map(chipHtml).join("") : `<span class="empty-tier">${tx("perfume.empty_level")}</span>`}
      </div>
    </section>`;
}

function chipHtml(note) {
  const entry = findEntry(note.name);
  return `<button type="button" class="chip" data-term="${esc(note.name)}" style="--fam:${famColor(entry?.family)}">${esc(note.name)}</button>`;
}

function profileHtml(notes) {
  const counts = {};
  const seen = new Set();
  for (const n of notes) {
    if (seen.has(slug(n.name))) continue;
    seen.add(slug(n.name));
    const fam = findEntry(n.name)?.family || t("common.other_family");
    counts[fam] = (counts[fam] || 0) + 1;
  }
  const fams = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!fams.length) return "";
  return `
    <section class="profile">
      <div class="bar">${fams.map(([f, c]) => `<span style="flex:${c};--fam:${famColor(f)}" title="${esc(f)}"></span>`).join("")}</div>
      <div class="legend">${fams.map(([f, c]) => `<span style="--fam:${famColor(f)}">${esc(f)} · ${c}</span>`).join("")}</div>
    </section>`;
}

function provenanceHtml(perfume) {
  const rows = perfume.provenance || [];
  if (!rows.length) return "";
  return `
    <details class="provenance">
      <summary>${tx("perfume.sources_title")}</summary>
      <ul>${rows.map((r) => {
        const what = srcLabel(r.kind);
        const link = /^https?:\/\//.test(r.url) ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(what)}</a>` : esc(what);
        return `<li>${link}</li>`;
      }).join("")}</ul>
    </details>`;
}

function renderDictionary(focus) {
  setNav();
  const families = [...new Set(state.notes.map((n) => n.family))].sort();
  const saved = savedView();
  let active = saved?.fam || null;
  app.innerHTML = `
    ${backLinks()}
    <div class="page-head">
      <h1>${tx("dictionary.title")}</h1>
      <p>${rich("dictionary.intro")}</p>
    </div>
    <div class="filters">
      <input id="dq" type="search" placeholder="${tx("dictionary.placeholder")}" aria-label="${tx("dictionary.input_label")}">
    </div>
    <div class="filters" id="fams">
      <button type="button" class="fam-btn all ${active ? "" : "on"}" data-fam="">${tx("common.all")}</button>
      ${families.map((f) => `<button type="button" class="fam-btn ${f === active ? "on" : ""}" data-fam="${esc(f)}" style="--fam:${famColor(f)}">${esc(f)}</button>`).join("")}
    </div>
    <div class="grid" id="grid"></div>`;

  const grid = document.getElementById("grid");
  const input = document.getElementById("dq");
  if (saved?.q) input.value = saved.q;
  const draw = () => {
    saveView({ q: input.value, fam: active });
    const q = input.value.trim().toLowerCase();
    const list = state.notes.filter((n) =>
      (!active || n.family === active) &&
      (!q || [n.name, ...(n.aliases || []), n.family, n.description].join(" ").toLowerCase().includes(q)));
    grid.innerHTML = list.map(noteCardHtml).join("") || `<p class="count">${tx("dictionary.none")}</p>`;
  };
  openCardsOnClick(grid);
  input.addEventListener("input", draw);
  document.getElementById("fams").addEventListener("click", (e) => {
    const b = e.target.closest("[data-fam]");
    if (!b) return;
    if (!b.dataset.fam) input.value = ""; // "All" shows every note, so clear the search too
    active = !b.dataset.fam || active === b.dataset.fam ? null : b.dataset.fam;
    document.querySelectorAll(".fam-btn").forEach((x) => x.classList.toggle("on", x.dataset.fam === (active || "")));
    draw();
  });
  draw();
  if (focus) {
    const card = document.getElementById(`note-${focus}`);
    if (card) { card.scrollIntoView({ behavior: "smooth", block: "center" }); card.classList.add("flash"); }
  }
}

function noteCardHtml(n) {
  return `
      <div class="card note-card" id="note-${slug(n.name)}" data-href="#/note/${slug(n.name)}" style="--fam:${famColor(n.family)}">
        <div class="card-img ${esc(n.image_kind || "")}">${n.image ? `<img src="/static/notes/${esc(n.image)}" alt="" loading="lazy">` : `<span>${esc(n.name.slice(0, 1))}</span>`}</div>
        <div class="head"><div><h3><a href="#/note/${slug(n.name)}">${esc(n.name)}</a></h3><small>${esc(n.family)}${n.aliases?.length ? ` · ${tx("dictionary.aka", { names: n.aliases.slice(0, 3).join(", ") })}` : ""}</small></div></div>
        <p>${linkify(n.description, { exclude: n.name })}</p>
        <span class="more">${tx("dictionary.more")}</span>
      </div>`;
}

// A click anywhere on a note card opens it (except on its hoverable words and links).
function openCardsOnClick(grid) {
  grid.addEventListener("click", (e) => {
    if (e.target.closest(".term, a")) return;
    const card = e.target.closest("[data-href]");
    if (card) location.hash = card.dataset.href;
  });
}

// ---------- notes by type: Natural, Synthetic, Accord... ----------

function renderType(typeSlug) {
  setNav();
  const types = [...new Set(state.notes.map((n) => n.type).filter(Boolean))].sort();
  const type = types.find((t) => slug(t) === typeSlug);
  if (!type) return renderError(t("types.unknown"));
  const notes = state.notes.filter((n) => n.type === type).sort((a, b) => a.name.localeCompare(b.name));
  const saved = savedView();
  app.innerHTML = `
    ${backLinks()}
    <div class="page-head">
      <h1>${tx("types.title", { type })}</h1>
      <p>${site.types.get(type) ? `${richText(site.types.get(type))} ` : ""}${tx("types.count", { n: notes.length })}</p>
    </div>
    <div class="filters">
      ${types.map((t) => `<a class="fam-btn all ${t === type ? "on" : ""}" href="#/type/${slug(t)}"${t === type ? ' aria-current="page"' : ""}>${esc(t)}</a>`).join("")}
    </div>
    <div class="filters"><input id="tq" type="search" placeholder="${tx("types.placeholder", { type: type.toLowerCase() })}" aria-label="${tx("types.input_label")}"></div>
    <div class="grid" id="grid"></div>`;
  const grid = document.getElementById("grid");
  const input = document.getElementById("tq");
  if (saved?.q) input.value = saved.q;
  const draw = () => {
    saveView({ q: input.value });
    const q = input.value.trim().toLowerCase();
    const list = notes.filter((n) => !q || [n.name, ...(n.aliases || []), n.family, n.description].join(" ").toLowerCase().includes(q));
    grid.innerHTML = list.map(noteCardHtml).join("") || `<p class="count">${tx("common.no_match")}</p>`;
  };
  openCardsOnClick(grid);
  input.addEventListener("input", draw);
  draw();
}

function renderGlossary() {
  setNav();
  const terms = [...state.glossary].sort((a, b) => a.name.localeCompare(b.name));
  app.innerHTML = `
    ${backLinks()}
    <div class="page-head">
      <h1>${tx("glossary.title")}</h1>
      <p>${rich("glossary.intro")}</p>
    </div>
    <div class="filters"><input id="gq" type="search" placeholder="${tx("glossary.placeholder")}" aria-label="${tx("glossary.input_label")}"></div>
    <div class="glossary" id="gl"></div>`;
  const input = document.getElementById("gq");
  const saved = savedView();
  if (saved?.q) input.value = saved.q;
  const draw = () => {
    saveView({ q: input.value });
    const q = input.value.trim().toLowerCase();
    document.getElementById("gl").innerHTML = terms
      .filter((t) => !q || [t.name, ...(t.aliases || []), t.description].join(" ").toLowerCase().includes(q))
      .map((t) => `
        <div class="card">
          <h3>${esc(t.name)}</h3>
          ${t.aliases?.length ? `<small>${tx("glossary.also", { names: t.aliases.join(", ") })}</small>` : ""}
          <p>${linkify(t.description, { exclude: t.name })}</p>
        </div>`).join("");
  };
  input.addEventListener("input", draw);
  draw();
}

// ---------- note detail ----------

const noteSection = (title, body) => `<section class="note-section"><h2>${title}</h2><div>${body}</div></section>`;

async function renderNote(slugId) {
  setNav();
  app.innerHTML = `<div class="loading"><p>${tx("note.loading")}</p></div>`;
  let n;
  try {
    n = await api(`/api/note/${encodeURIComponent(slugId)}`);
  } catch (err) {
    return renderError(err.message);
  }
  if (location.hash !== `#/note/${slugId}`) return;
  const d = n.details || {};
  const img = n.image;
  const alt = img ? t(img.kind === "molecule" ? "note.alt_molecule" : "note.alt_plant", { name: img.caption }) : "";
  app.innerHTML = `
    <article class="note-page" style="--fam:${famColor(n.family)}">
      ${backLinks()}
      <div class="note-hero">
        <figure class="plate ${img ? img.kind : "empty"}">
          ${img ? `<img src="/static/notes/${esc(img.file)}" alt="${esc(alt)}">` : `<div class="plate-empty">${esc(n.name)}</div>`}
          ${img ? `<figcaption><i>${esc(img.caption)}</i><span>${img.kind === "molecule" ? tx("note.credit_molecule") : img.kind === "photo" ? tx("note.credit_photo", { artist: img.artist.replace(/^\(c\)\s*/i, "© ").replace(/, some rights reserved.*$/i, "") }) : esc(img.artist)} · <a href="${esc(img.page)}" target="_blank" rel="noopener">${esc(img.license)}</a></span></figcaption>` : ""}
        </figure>
        <div class="note-intro">
          <div class="note-fam">${esc(n.family)}</div>
          <h1>${esc(n.name)}</h1>
          ${d.type ? `<a class="note-type" href="#/type/${slug(d.type)}" title="${tx("types.see_all", { type: d.type.toLowerCase() })}">${esc(d.type)}</a>` : ""}
          <p class="lede">${linkify(n.description, { exclude: n.name })}</p>
          ${n.aliases?.length ? `<p class="aka">${tx("note.also_called", { names: n.aliases.join(", ") })}</p>` : ""}
        </div>
      </div>
      ${d.source ? noteSection(tx("note.source"), `<p>${linkify(d.source, { exclude: n.name })}</p>`) : ""}
      ${d.history ? noteSection(tx("note.history"), `<p>${linkify(d.history, { exclude: n.name })}</p>`) : ""}
      ${d.molecules?.length ? noteSection(tx("note.chemistry"), `
        <p class="hint">${tx("note.chemistry_hint")}</p>
        <ul class="molecules">${d.molecules.map(([m, smell]) => `<li><b>${esc(m)}</b><span>${esc(smell)}</span></li>`).join("")}</ul>`) : ""}
      ${n.perfumes?.length ? noteSection(`<a class="shelf-link" href="#/note/${esc(slugId)}/shelf">${tx("note.shelf_link")}</a>`, `
        <p>${tx("note.shelf_count", { n: n.perfumes.length, name: n.name })}</p>
        <a class="btn ghost small shelf-btn" href="#/note/${esc(slugId)}/shelf">${tx("note.shelf_button")}</a>`) : ""}
    </article>`;
}

// ---------- perfumes on our shelf that use a note ----------

async function renderNoteShelf(slugId) {
  setNav();
  app.innerHTML = `<div class="loading"><p>${tx("common.checking")}</p></div>`;
  let n;
  try {
    n = await api(`/api/note/${encodeURIComponent(slugId)}`);
  } catch (err) {
    return renderError(err.message);
  }
  if (location.hash !== `#/note/${slugId}/shelf`) return;
  const perfumes = [...(n.perfumes || [])].sort((a, b) => `${a.brand} ${a.name}`.localeCompare(`${b.brand} ${b.name}`));
  const lvls = LEVEL_KEYS.filter((l) => perfumes.some((p) => p.level === l));
  const saved = savedView();
  let active = lvls.includes(saved?.lv) ? saved.lv : "";
  app.innerHTML = `
    <article class="note-page" style="--fam:${famColor(n.family)}">
      ${backLinks()}
      <div class="page-head">
        <h1>${tx("shelf.title", { name: n.name })}</h1>
        <p>${tx("shelf.count", { n: perfumes.length, name: n.name })}</p>
      </div>
      <div class="filters"><input id="sq" type="search" placeholder="${tx("shelf.placeholder")}" aria-label="${tx("shelf.input_label")}"></div>
      ${lvls.length > 1 ? `<div class="filters" id="lv">
        <button type="button" class="fam-btn all ${active ? "" : "on"}" data-lv="">${tx("common.all")}</button>
        ${lvls.map((l) => `<button type="button" class="fam-btn ${l === active ? "on" : ""}" data-lv="${l}">${tx(`shelf.levels.${l}`)}</button>`).join("")}
      </div>` : ""}
      <ul class="shelf" id="shelf"></ul>
    </article>`;
  const input = document.getElementById("sq");
  const list = document.getElementById("shelf");
  if (saved?.q) input.value = saved.q;
  const draw = () => {
    saveView({ q: input.value, lv: active });
    const q = input.value.trim().toLowerCase();
    const rows = perfumes.filter((p) => (!active || p.level === active) && (!q || `${p.brand} ${p.name}`.toLowerCase().includes(q)));
    list.innerHTML = rows.map((p) => `
      <li><a href="#/perfume/${esc(p.slug)}"><small>${esc(p.brand)}</small>${esc(p.name)}</a><span>${tx(`shelf.levels.${p.level}`)}</span></li>`).join("")
      || `<li class="count">${tx("common.no_match")}</li>`;
  };
  input.addEventListener("input", draw);
  document.getElementById("lv")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-lv]");
    if (!b) return;
    active = b.dataset.lv;
    document.querySelectorAll("#lv .fam-btn").forEach((x) => x.classList.toggle("on", x.dataset.lv === active));
    draw();
  });
  draw();
}

// ---------- history of perfume ----------

async function renderHistory() {
  setNav();
  let events = [];
  try {
    events = await api("/api/history");
  } catch (err) {
    return renderError(err.message);
  }
  if (location.hash !== "#/history") return;
  const eras = [...new Set(events.map((e) => e.era))];
  const saved = savedView();
  app.innerHTML = `
    ${backLinks()}
    <div class="page-head">
      <h1>${tx("history.title")}</h1>
      <p>${rich("history.intro")}</p>
    </div>
    <div class="htl-bar">
      <div class="htl-eras" role="group" aria-label="${tx("history.eras_label")}">
        ${eras.map((era, i) => `<button type="button" data-era="${i}">${esc(era)}</button>`).join("")}
      </div>
      <div class="htl-arrows">
        <button type="button" data-step="-1" aria-label="${tx("history.earlier")}">←</button>
        <button type="button" data-step="1" aria-label="${tx("history.later")}">→</button>
      </div>
    </div>
    <div class="htl-scroll" tabindex="0" aria-label="${tx("history.rail_label")}">
      <div class="htl-track">
        ${eras.map((era, i) => `
          <section class="htl-era" data-era="${i}">
            <h2 class="htl-era-name">${esc(era)}</h2>
            <ol class="htl-events">
              ${events.map((e, j) => [e, j]).filter(([e]) => e.era === era).map(([e, j]) => `
                <li class="event ${e.picture ? "has-pic" : ""}" data-i="${j}" tabindex="0">
                  <div class="when">${esc(e.year)}</div>
                  <div class="tick" aria-hidden="true"></div>
                  <h3>${esc(e.title)}</h3>
                  <p>${linkify(e.text)}</p>
                </li>`).join("")}
            </ol>
          </section>`).join("")}
      </div>
    </div>
    <figure class="htl-card" id="htl-card" hidden></figure>`;

  const scroller = app.querySelector(".htl-scroll");
  const card = document.getElementById("htl-card");
  const eraBtns = [...app.querySelectorAll(".htl-eras button")];
  const sections = [...app.querySelectorAll(".htl-era")];
  const [prevBtn, nextBtn] = app.querySelectorAll(".htl-arrows button");

  // ----- the picture card -----
  let current = null;
  let hideTimer = null;
  const show = (li) => {
    clearTimeout(hideTimer);
    if (current === li && !card.hidden) return;
    app.querySelectorAll(".event.active").forEach((x) => x.classList.remove("active"));
    current = li;
    li.classList.add("active");
    const e = events[li.dataset.i];
    const pic = e.picture;
    if (!pic) { card.hidden = true; return; }
    const credit = pic.kind === "molecule"
      ? `${tx("history.credit_molecule", { name: pic.caption })} · <a href="${esc(pic.page)}" target="_blank" rel="noopener">${tx("history.pubchem")}</a>`
      : `${esc(pic.artist)} · <a href="${esc(pic.page)}" target="_blank" rel="noopener">${esc(pic.license)}</a>`;
    card.className = `htl-card ${pic.kind}`;
    card.innerHTML = `
      <div class="pic"><img src="/static/history/${esc(pic.file)}" alt="${esc(pic.kind === "molecule" ? t("history.alt_molecule", { name: pic.caption }) : e.title)}"></div>
      <figcaption><b>${esc(e.year)} · ${esc(e.title)}</b><small>${credit}</small></figcaption>`;
    card.hidden = false;
    place(li);
  };
  const place = (li) => {
    const r = li.getBoundingClientRect();
    const c = card.getBoundingClientRect();
    const left = Math.min(Math.max(8, r.left), window.innerWidth - c.width - 8);
    let top = r.top - c.height - 12; // above the milestone when there is room, else below it
    if (top < 8) top = Math.min(r.bottom + 12, window.innerHeight - c.height - 8);
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
  };
  const hide = () => {
    card.hidden = true;
    current?.classList.remove("active");
    current = null;
  };
  const hideSoon = () => { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 150); };

  scroller.addEventListener("pointerover", (ev) => {
    if (ev.pointerType !== "mouse") return;
    if (ev.target.closest(".term")) return hide(); // a word's own tooltip is showing instead
    const li = ev.target.closest(".event");
    if (li) show(li);
  });
  scroller.addEventListener("pointerout", (ev) => {
    if (ev.pointerType !== "mouse") return;
    const li = ev.target.closest(".event");
    if (li && !li.contains(ev.relatedTarget) && !card.contains(ev.relatedTarget)) hideSoon();
  });
  card.addEventListener("pointerenter", () => clearTimeout(hideTimer)); // so its credit link can be clicked
  card.addEventListener("pointerleave", (ev) => { if (!current?.contains(ev.relatedTarget)) hideSoon(); });
  scroller.addEventListener("click", (ev) => { // touch: tap a milestone to show or hide its picture
    if (ev.target.closest(".term, a")) return;
    const li = ev.target.closest(".event");
    if (li && lastPointer !== "mouse") (current === li && !card.hidden ? hide() : show(li));
  });
  scroller.addEventListener("focusin", (ev) => { const li = ev.target.closest(".event"); if (li) show(li); });
  scroller.addEventListener("focusout", (ev) => { if (!scroller.contains(ev.relatedTarget) && !card.contains(ev.relatedTarget)) hide(); });
  scroller.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") return hide();
    const li = ev.target.closest(".event");
    if (!li || !["ArrowLeft", "ArrowRight"].includes(ev.key)) return;
    const all = [...app.querySelectorAll(".event")];
    const next = all[all.indexOf(li) + (ev.key === "ArrowRight" ? 1 : -1)];
    if (next) { ev.preventDefault(); next.focus(); next.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" }); }
  });

  // ----- moving along the rail -----
  const update = () => {
    const x = scroller.scrollLeft;
    prevBtn.disabled = x <= 2;
    nextBtn.disabled = x >= scroller.scrollWidth - scroller.clientWidth - 2;
    const firstLeft = sections[0].offsetLeft;
    let on = 0;
    sections.forEach((s, i) => { if (s.offsetLeft - firstLeft <= x + 40) on = i; });
    eraBtns.forEach((b, i) => b.classList.toggle("on", i === on));
    saveView({ x });
    if (!card.hidden) hide();
  };
  scroller.addEventListener("scroll", update, { passive: true });
  window.addEventListener("scroll", () => !card.hidden && current && place(current), { passive: true });
  prevBtn.addEventListener("click", () => scroller.scrollBy({ left: -scroller.clientWidth * 0.8, behavior: "smooth" }));
  nextBtn.addEventListener("click", () => scroller.scrollBy({ left: scroller.clientWidth * 0.8, behavior: "smooth" }));
  eraBtns.forEach((b, i) => b.addEventListener("click", () =>
    scroller.scrollTo({ left: sections[i].offsetLeft - sections[0].offsetLeft, behavior: "smooth" })));
  // A mouse wheel scrolls the rail sideways (trackpads already can), until it reaches either end.
  scroller.addEventListener("wheel", (ev) => {
    if (Math.abs(ev.deltaX) >= Math.abs(ev.deltaY)) return;
    const max = scroller.scrollWidth - scroller.clientWidth;
    if ((ev.deltaY < 0 && scroller.scrollLeft <= 0) || (ev.deltaY > 0 && scroller.scrollLeft >= max - 1)) return;
    ev.preventDefault();
    scroller.scrollLeft += ev.deltaY;
  }, { passive: false });

  if (saved?.x) scroller.scrollLeft = saved.x;
  update();

  const io = "IntersectionObserver" in window && new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
  }, { root: scroller, rootMargin: "0px 10% 0px 0px" });
  app.querySelectorAll(".event").forEach((el) => (io ? io.observe(el) : el.classList.add("in")));
}

// ---------- router ----------

// Each tab (Find a perfume, Notes dictionary, Glossary, History) keeps its own trail of pages, so
// back links stay inside the tab: a note links back to the note you read before it and to the dictionary.
// The tab and its trail ride along in history.state, so the browser's own Back/Forward agree with them.
const TAB_ROOT = { home: "#/", dictionary: "#/dictionary", glossary: "#/glossary", history: "#/history" };
const nav = { tab: "home", stack: ["#/"], prev: null, back: false, pendingBack: false, last: null, scroll: new Map(), view: new Map() };

// The tab a hash opens when it is a tab's own page (clicking a tab starts that tab afresh).
function rootTab(hash) {
  if (hash === "#/" || hash === "") return "home";
  if (hash === "#/dictionary" || hash.startsWith("#/dictionary/")) return "dictionary";
  if (hash.startsWith("#/glossary")) return "glossary";
  if (hash.startsWith("#/history")) return "history";
  return null;
}

function pageLabel(hash) {
  const part = (h) => { try { return decodeURIComponent(h); } catch { return h; } };
  const noteName = (s) => state.notes.find((e) => slug(e.name) === s)?.name || s.replace(/-/g, " ");
  const tab = rootTab(hash);
  if (tab) return t(`crumbs.${tab}`);
  if (hash.startsWith("#/note/") && hash.endsWith("/shelf")) return t("crumbs.shelf", { name: noteName(part(hash.slice(7, -6))) });
  if (hash.startsWith("#/note/")) return noteName(part(hash.slice(7)));
  if (hash.startsWith("#/perfume/")) {
    const p = state.results[part(hash.slice(10))];
    return p ? `${p.brand} ${p.name}` : t("crumbs.perfume");
  }
  if (hash.startsWith("#/p/")) return part(hash.slice(4));
  if (hash.startsWith("#/type/")) {
    const ty = state.notes.find((n) => n.type && slug(n.type) === part(hash.slice(7)))?.type;
    return ty ? t("types.title", { type: ty }) : t("crumbs.type_fallback");
  }
  return t("crumbs.back");
}

// "← <previous page in this tab>" plus, deeper in, a link to the tab's own page.
function backLinks() {
  if (nav.stack.length < 2) return "";
  const prev = nav.stack[nav.stack.length - 2];
  const root = nav.stack[0];
  const links = [`<a class="back" href="${esc(prev)}" data-back="${esc(prev)}">← ${esc(pageLabel(prev))}</a>`];
  if (prev !== root) links.push(`<a class="back" href="${esc(root)}" data-back="${esc(root)}">${esc(pageLabel(root))}</a>`);
  return `<nav class="crumbs" aria-label="${tx("common.back_label")}">${links.join("")}</nav>`;
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-back]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  const target = a.dataset.back;
  // Step the browser back when the page behind us is the one we want, so Back/Forward stay tidy.
  if (nav.prev === target && nav.stack[nav.stack.length - 2] === target) history.back();
  else { nav.pendingBack = true; location.hash = target; }
});

// Remember a page's search box / filters, to put them back when you return to it.
const savedView = () => (nav.back ? nav.view.get(location.hash) : null);
const saveView = (v) => nav.view.set(location.hash, v);

function saveNavState(url) {
  try { history.replaceState({ navTab: nav.tab, navStack: nav.stack, navPrev: nav.prev }, "", url); } catch { /* still works, just without Back/Forward memory */ }
}

// A search result swaps its URL for the perfume's own; keep the trail in step.
function replaceHash(hash) {
  nav.stack = [...nav.stack.slice(0, -1), hash];
  nav.last = hash;
  saveNavState(hash);
}

async function route() {
  hideTip();
  const hash = location.hash || "#/";
  if (nav.last) nav.scroll.set(nav.last, window.scrollY);
  const st = history.state;
  let back = false;
  if (st?.navTab) {
    // Browser Back/Forward (or a reload): pick the tab and trail up as they were on that page.
    ({ navTab: nav.tab, navStack: nav.stack, navPrev: nav.prev } = st);
    back = true;
  } else {
    const tab = rootTab(hash);
    if (nav.pendingBack && nav.stack.includes(hash)) {
      nav.stack = nav.stack.slice(0, nav.stack.indexOf(hash) + 1);
      back = true;
    } else if (tab) {
      nav.tab = tab;
      nav.stack = [hash];
    } else if (!nav.last) {
      // Opened straight from a link: hang it under the tab it belongs to.
      nav.tab = hash.startsWith("#/note/") || hash.startsWith("#/type/") ? "dictionary" : "home";
      nav.stack = [TAB_ROOT[nav.tab], hash];
    } else if (nav.stack[nav.stack.length - 1] !== hash) {
      nav.stack = [...nav.stack, hash];
    }
    nav.prev = nav.last;
    saveNavState();
  }
  nav.pendingBack = false;
  nav.back = back;
  nav.last = hash;
  if (!back) window.scrollTo(0, 0);
  await render(hash);
  setNav();
  if (back && (location.hash || "#/") === hash) window.scrollTo(0, nav.scroll.get(hash) || 0);
}

function render(hash) {
  if (hash.startsWith("#/p/")) return renderSearch(decodeURIComponent(hash.slice(4)));
  if (hash.startsWith("#/perfume/")) return renderPerfumeSlug(decodeURIComponent(hash.slice(10)));
  if (hash.startsWith("#/dictionary")) return renderDictionary(hash.split("/")[2]);
  if (hash.startsWith("#/glossary")) return renderGlossary();
  if (hash.startsWith("#/note/") && hash.endsWith("/shelf")) return renderNoteShelf(decodeURIComponent(hash.slice(7, -6)));
  if (hash.startsWith("#/note/")) return renderNote(decodeURIComponent(hash.slice(7)));
  if (hash.startsWith("#/history")) return renderHistory();
  if (hash.startsWith("#/type/")) return renderType(decodeURIComponent(hash.slice(7)));
  return renderHome();
}

// ---------- light / dark ----------

const themeBtn = document.getElementById("theme-toggle");
const currentTheme = () => document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
function paintThemeButton() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  themeBtn.textContent = t(`theme.to_${next}`);
  themeBtn.setAttribute("aria-label", t(`theme.to_${next}_label`));
}
themeBtn?.addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("sniffy-theme", next); } catch { /* private mode: still switches for this visit */ }
  paintThemeButton();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => themeBtn && paintThemeButton());

(async function init() {
  try {
    const [content, dict, status] = await Promise.all([api("/api/site"), api("/api/dictionary"), api("/api/status")]);
    site.text = content.text;
    site.families = new Map(Object.entries(content.families).map(([k, v]) => [k.toLowerCase(), v]));
    site.types = new Map(Object.entries(content.types));
    state.notes = dict.notes;
    state.glossary = dict.glossary;
    state.status = status;
    buildIndexes();
  } catch (err) {
    console.error(err);
  }
  if (themeBtn) paintThemeButton();
  window.addEventListener("hashchange", route);
  route();
})();
