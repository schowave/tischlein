// Rezeptverwaltung: Liste mit Filtern, Kinder-OK-Schalter, Kategorien-Auswahl.
// Jede Änderung wird sofort nach Paprika geschrieben (POST /api/rezepte/<uid>/tags).

const DATA = JSON.parse(document.getElementById("data").textContent);
const byUid = new Map(DATA.recipes.map((r) => [r.uid, r]));
const fold = (s) => s.toLocaleLowerCase("de");
const hasTag = (r, t) => r.tags.some((x) => fold(x) === fold(t));

const state = {
  q: "",
  scope: "main",
  theme: null, // null = alle, "__none" = ohne Thema
  onlyOpen: false,
  selected: null,
};
const details = new Map(); // uid -> details from /api/rezepte/<uid> (ingredients, servings, time)
const expanded = new Set(); // uids whose full ingredient list is open
const queues = new Map(); // uid -> Promise chain, so edits to one recipe run in order
const status = new Map(); // uid -> {kind: "busy"|"ok"|"err", text, retry}

const $ = (sel) => document.querySelector(sel);
const listEl = $("#list");
const detailEl = $("#detail");
const detailWrap = $("#detail-wrap");
const phone = window.matchMedia("(max-width: 640px)");

// ---------- URL presets (from "Vorrat pro Thema") ----------
const params = new URLSearchParams(location.search);
if (params.get("thema")) state.theme = params.get("thema");
if (params.get("kinder") === "nein") state.onlyOpen = true;
if (params.get("rezept") && byUid.has(params.get("rezept"))) {
  state.selected = params.get("rezept");
  if (!hasTag(byUid.get(state.selected), DATA.mainTag)) state.scope = "all";
}

// ---------- filtering ----------
function visible() {
  const q = fold(state.q.trim());
  return DATA.recipes.filter((r) => {
    if (state.scope === "main" && !hasTag(r, DATA.mainTag)) return false;
    if (state.onlyOpen && r.kinder_ok) return false;
    if (state.theme === "__none" && r.themen.length) return false;
    if (state.theme && state.theme !== "__none" && !hasTag(r, state.theme)) return false;
    if (q && !fold(r.name).includes(q) && !r.tags.some((t) => fold(t).includes(q))) return false;
    return true;
  });
}

function scopeBase() {
  return DATA.recipes.filter((r) => state.scope !== "main" || hasTag(r, DATA.mainTag));
}

// ---------- rendering ----------
function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) n.append(c);
  return n;
}

function renderThemeChips() {
  const base = scopeBase();
  const wrap = $("#theme-chips");
  wrap.replaceChildren();
  const mk = (label, value, n) =>
    el("button", {
      type: "button", class: "chip", "aria-pressed": String(state.theme === value),
      onclick: () => { state.theme = state.theme === value ? null : value; renderAll(); },
    }, label, el("span", { class: "n num" }, String(n)));
  wrap.append(mk("Alle Themen", null, base.length));
  for (const t of DATA.themes) wrap.append(mk(t, t, base.filter((r) => hasTag(r, t)).length));
  wrap.append(mk("Ohne Thema", "__none", base.filter((r) => !r.themen.length).length));
}

function tagLine(r) {
  const themes = r.themen;
  const rest = r.tags.filter((t) => !themes.some((x) => fold(x) === fold(t)) && fold(t) !== fold(DATA.kidTag));
  const parts = [];
  themes.forEach((t, i) => { if (i) parts.push(" · "); parts.push(el("span", { class: "th" }, t)); });
  if (rest.length) parts.push((themes.length ? " · " : "") + rest.join(" · "));
  if (!parts.length) parts.push("keine Kategorie");
  return parts;
}

function switchFor(r) {
  const st = status.get(r.uid);
  return el("button", {
    type: "button", role: "switch", class: "switch",
    "aria-checked": String(r.kinder_ok), "aria-label": `Kinder-OK: ${r.name}`,
    "data-busy": st?.kind === "busy" ? "true" : false,
    disabled: !DATA.canWrite || (r.kinder_ok && !r.kinder_tag) ? true : false,
    title: r.kinder_ok && !r.kinder_tag ? "Kinder-OK durch Rückmeldung im Wochenplan" : false,
    onclick: (e) => { e.stopPropagation(); toggleTag(r.uid, DATA.kidTag, !r.kinder_tag); },
  });
}

function renderList() {
  const rows = visible();
  $("#result-count").textContent = String(rows.length);
  listEl.replaceChildren(
    ...rows.map((r) =>
      el("div", {
        class: "ritem", role: "option", tabindex: "0", "data-uid": r.uid,
        "aria-selected": String(state.selected === r.uid),
        onclick: () => select(r.uid),
        onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(r.uid); } },
      },
        thumb(r),
        el("div", {}, el("div", { class: "nm" }, r.name), el("div", { class: "tg" }, tagLine(r))),
        el("div", { class: "sw-cell" }, switchFor(r)),
      ),
    ),
  );
  const foot = $("#list-foot");
  if (!rows.length) {
    foot.textContent = state.onlyOpen
      ? "Alles markiert: In dieser Auswahl ist jedes Gericht Kinder-OK."
      : "Kein Rezept passt zu Suche und Filter.";
  } else {
    foot.textContent = DATA.canWrite ? "Änderungen landen sofort in Paprika." : "Paprika ist nicht verbunden, Änderungen sind deshalb nicht möglich.";
  }
}

function placeholder(r, cls) {
  return el("span", { class: `${cls} ph`, "aria-hidden": "true" }, r.name.trim().charAt(0).toUpperCase());
}

function thumb(r) {
  if (!r.bild) return placeholder(r, "thumb");
  const img = el("img", { class: "thumb", src: `/bilder/${encodeURIComponent(r.uid)}.jpg`, alt: "", loading: "lazy", width: "48", height: "48", decoding: "async" });
  img.addEventListener("error", () => img.replaceWith(placeholder(r, "thumb")), { once: true });
  return img;
}

function loadDetails(uid) {
  if (details.has(uid)) return;
  details.set(uid, null); // loading
  fetch(`/api/rezepte/${encodeURIComponent(uid)}`)
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.status))))
    .then((d) => details.set(uid, d))
    .catch(() => details.set(uid, { error: true, zutaten: [] }))
    .finally(() => { if (state.selected === uid) renderDetail(); });
}

function ingredients(r) {
  const d = details.get(r.uid);
  if (d === undefined || d === null) {
    return el("div", { class: "ingredients" }, el("h3", {}, "Zutaten"), el("p", { class: "muted" }, "Zutaten werden geladen …"));
  }
  if (d.error) {
    return el("div", { class: "ingredients" }, el("h3", {}, "Zutaten"),
      el("p", { class: "muted" }, "Zutaten konnten nicht geladen werden. ",
        el("button", { type: "button", class: "linkish", onclick: () => { details.delete(r.uid); renderDetail(); } }, "Nochmal versuchen")));
  }
  if (!d.zutaten.length) {
    return el("div", { class: "ingredients" }, el("h3", {}, "Zutaten"), el("p", { class: "muted" }, "Keine Zutaten in Paprika hinterlegt."));
  }
  const limit = d.zutaten.length <= 10 ? d.zutaten.length : 8; // never hide just one or two lines
  const open = expanded.has(r.uid);
  const shown = open ? d.zutaten : d.zutaten.slice(0, limit);
  const count = d.zutaten.filter((z) => !z.kopf).length;
  return el("div", { class: "ingredients" },
    el("h3", {}, `Zutaten`, el("span", { class: "n num" }, ` ${count}`)),
    el("ul", {}, shown.map((z) => el("li", { class: z.kopf ? "kopf" : "" }, z.text))),
    d.zutaten.length > limit
      ? el("button", { type: "button", class: "btn btn-ghost btn-sm more", "aria-expanded": String(open),
          onclick: () => { open ? expanded.delete(r.uid) : expanded.add(r.uid); renderDetail(); } },
          open ? "Weniger anzeigen" : `Alle ${count} Zutaten anzeigen`)
      : null,
  );
}

function facts(r) {
  const d = details.get(r.uid) || {};
  const parts = [r.rating ? `Bewertung ${r.rating} von 5` : "Ohne Bewertung"];
  if (d.portionen) parts.push(/^\d+$/.test(d.portionen) ? `${d.portionen} Portionen` : d.portionen);
  if (d.zeit) parts.push(d.zeit);
  return parts.join(" · ");
}

function chipFor(r, tag, extraClass = "") {
  const on = hasTag(r, tag);
  return el("button", {
    type: "button", class: `chip ${extraClass}`, "aria-pressed": String(on), disabled: !DATA.canWrite,
    onclick: () => toggleTag(r.uid, tag, !on),
  },
    on ? el("span", { "aria-hidden": "true", html: false }, checkIcon()) : null,
    tag,
  );
}

function checkIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 12 12");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", "M2.5 6.5 5 9l4.5-6");
  p.setAttribute("fill", "none");
  p.setAttribute("stroke", "currentColor");
  p.setAttribute("stroke-width", "1.6");
  s.append(p);
  return s;
}

function photo(r) {
  const img = el("img", { class: "photo", src: `/bilder/${encodeURIComponent(r.uid)}.jpg`, alt: `Foto: ${r.name}`, width: "640", height: "400" });
  img.addEventListener("error", () => img.remove(), { once: true });
  return img;
}

function renderDetail() {
  const r = byUid.get(state.selected);
  if (r) loadDetails(r.uid);
  if (!r) {
    detailEl.replaceChildren(overview());
    detailWrap.classList.remove("open");
    return;
  }
  const known = new Set(DATA.groups.flatMap((g) => g.tags.map(fold)));
  const groups = DATA.groups.map((g) => ({ ...g, tags: [...g.tags] }));
  // a tag on the recipe that no group lists yet goes into the last group
  for (const t of r.tags) if (!known.has(fold(t))) groups[groups.length - 1].tags.push(t);

  const st = status.get(r.uid);
  const input = el("input", { class: "field", type: "text", placeholder: "Neue Kategorie", "aria-label": "Neue Kategorie anlegen", maxlength: "40" });
  const addNew = (e) => {
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    if (!DATA.groups.some((g) => g.tags.some((t) => fold(t) === fold(name)))) DATA.groups[DATA.groups.length - 1].tags.push(name);
    input.value = "";
    if (!hasTag(r, name)) toggleTag(r.uid, name, true);
  };

  detailEl.replaceChildren(...[
    el("button", { type: "button", class: "btn btn-ghost detail-back", onclick: () => select(null) }, "← Zurück zur Liste"),
    r.bild ? photo(r) : null,
    el("h2", { id: "detail-h", tabindex: "-1" }, r.name),
    el("p", { class: "sub" }, facts(r)),
    ingredients(r),
    ...groups.filter((g) => g.tags.length).map((g) =>
      el("div", { class: "group", role: "group", "aria-label": g.name },
        el("h3", {}, g.name),
        el("div", { class: "chips" }, g.tags.map((t) => chipFor(r, t, fold(t) === fold(DATA.kidTag) ? "kid" : ""))),
      ),
    ),
    el("form", { class: "field-row new-tag", onsubmit: addNew }, input, el("button", { class: "btn", disabled: !DATA.canWrite }, "Anlegen")),
    el("p", { class: `status${st?.kind === "err" ? " err" : ""}`, role: "status" },
      st ? st.text : "",
      st?.kind === "err" && st.retry ? [" ", el("button", { type: "button", onclick: st.retry }, "Nochmal versuchen")] : null,
    ),
    DATA.canWrite
      ? el("div", { class: "sub-actions" },
          el("button", { type: "button", class: "btn btn-ghost btn-sm danger", onclick: () => trash(r.uid) }, "In den Papierkorb"))
      : null,
  ].flat().filter((c) => c != null));
  if (phone.matches) detailWrap.classList.add("open");
}

// Without a selection the side panel shows Kinder-OK per theme, live, as the list is edited.
function overview() {
  const need = DATA.rotationNeed;
  const rows = DATA.themes.map((t) => {
    const themed = DATA.recipes.filter((r) => hasTag(r, t) && r.rueckmeldung !== "nein");
    return { t, n: themed.length, kid: themed.filter((r) => r.kinder_ok).length };
  });
  const slots = (k) => Array.from({ length: need }, (_, i) => el("span", { class: `slot${i < k ? " on" : ""}` }));
  return el("div", { class: "overview" },
    el("h2", {}, "Kinder-OK pro Thema"),
    el("p", { class: "detail-empty" }, `Jedes Thema braucht ${need} Kinder-OK-Gerichte, damit sich nichts zu oft wiederholt. Wähl ein Rezept, um seine Kategorien zu bearbeiten.`),
    el("div", { class: "supply" }, rows.map((x) =>
      el("button", {
        type: "button", "aria-pressed": String(state.theme === x.t),
        "aria-label": `${x.t}: ${x.kid} von ${need} Kinder-OK, ${x.n} Rezepte. Liste filtern`,
        onclick: () => { state.theme = x.t; state.onlyOpen = true; state.scope = "all"; renderAll(); },
      },
        el("span", { class: "name" }, x.t),
        el("span", { class: `ratio num${x.kid >= need ? " ok" : ""}` }, el("b", {}, String(x.kid)), ` von ${need}`),
        el("span", { class: "slots", "aria-hidden": "true" }, slots(x.kid), el("span", { class: "of num" }, `${x.n} Rezepte`)),
      ))),
  );
}

function renderAll() {
  for (const b of document.querySelectorAll("[data-scope]")) b.setAttribute("aria-pressed", String(b.dataset.scope === state.scope));
  $("#only-open").setAttribute("aria-pressed", String(state.onlyOpen));
  renderThemeChips();
  renderList();
  renderDetail();
}

function select(uid) {
  if (uid && details.get(uid)?.error) details.delete(uid); // a failed load is retried on the next open
  state.selected = uid;
  renderList();
  renderDetail();
  if (uid && !phone.matches) detailEl.querySelector("h2")?.focus?.();
}

// ---------- writing ----------
function time() {
  return new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

function toggleTag(uid, tag, on) {
  const r = byUid.get(uid);
  if (!r || !DATA.canWrite) return;
  // optimistic: show the change at once
  const before = { tags: [...r.tags], kinder_ok: r.kinder_ok, kinder_tag: r.kinder_tag, themen: [...r.themen] };
  if (on && !hasTag(r, tag)) r.tags.push(tag);
  if (!on) r.tags = r.tags.filter((t) => fold(t) !== fold(tag));
  r.kinder_tag = hasTag(r, DATA.kidTag);
  r.kinder_ok = r.kinder_tag || r.rueckmeldung === "ok";
  r.themen = DATA.themes.filter((t) => hasTag(r, t));
  status.set(uid, { kind: "busy", text: "Wird in Paprika gespeichert …" });
  rerenderRow(uid);

  const body = JSON.stringify(on ? { hinzufuegen: [tag], entfernen: [] } : { hinzufuegen: [], entfernen: [tag] });
  const run = () =>
    fetch(`/api/rezepte/${encodeURIComponent(uid)}/tags`, { method: "POST", headers: { "Content-Type": "application/json" }, body })
      .then(async (res) => {
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
        Object.assign(r, json);
        status.set(uid, { kind: "ok", text: `In Paprika gespeichert · ${time()}` });
      })
      .catch((err) => {
        Object.assign(r, before);
        status.set(uid, { kind: "err", text: `Nicht gespeichert: ${err.message}.`, retry: () => toggleTag(uid, tag, on) });
      })
      .finally(() => rerenderRow(uid));
  const chain = (queues.get(uid) || Promise.resolve()).then(run, run);
  queues.set(uid, chain);
}

// ---------- trash with undo ----------
const toast = el("div", { class: "toast", role: "status", "aria-live": "polite", hidden: true });
document.body.append(toast);
let toastTimer;

function showToast(text, action) {
  clearTimeout(toastTimer);
  toast.replaceChildren(el("span", {}, text), action || "");
  toast.hidden = false;
  toastTimer = setTimeout(() => (toast.hidden = true), 10000);
}

async function trash(uid) {
  const r = byUid.get(uid);
  if (!r || !confirm(`„${r.name}“ in den Papierkorb von Paprika legen?\n\nDu kannst es danach hier rückgängig machen oder in Paprika wiederherstellen.`)) return;
  status.set(uid, { kind: "busy", text: "Wird in den Papierkorb gelegt …" });
  renderDetail();
  try {
    const res = await fetch(`/api/rezepte/${encodeURIComponent(uid)}/papierkorb`, { method: "POST" });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
  } catch (err) {
    status.set(uid, { kind: "err", text: `Nicht verschoben: ${err.message}.`, retry: () => trash(uid) });
    renderDetail();
    return;
  }
  const index = DATA.recipes.indexOf(r);
  DATA.recipes.splice(index, 1);
  byUid.delete(uid);
  status.delete(uid);
  state.selected = null;
  renderAll();
  showToast(`„${r.name}“ liegt im Papierkorb.`,
    el("button", { type: "button", class: "linkish", onclick: () => restore(r, index) }, "Rückgängig"));
}

async function restore(r, index) {
  showToast(`„${r.name}“ wird wiederhergestellt …`);
  try {
    const res = await fetch(`/api/rezepte/${encodeURIComponent(r.uid)}/wiederherstellen`, { method: "POST" });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    Object.assign(r, json);
  } catch (err) {
    showToast(`Wiederherstellen fehlgeschlagen: ${err.message}. In Paprika liegt es im Papierkorb.`);
    return;
  }
  DATA.recipes.splice(Math.min(index, DATA.recipes.length), 0, r);
  byUid.set(r.uid, r);
  showToast(`„${r.name}“ ist wieder da.`);
  select(r.uid);
  renderThemeChips();
}

function rerenderRow(uid) {
  // keep the list stable while editing: re-render without re-filtering the edited row away
  const row = listEl.querySelector(`[data-uid="${CSS.escape(uid)}"]`);
  const r = byUid.get(uid);
  if (row && r) {
    row.querySelector(".tg").replaceChildren(...tagLine(r));
    row.querySelector(".sw-cell").replaceChildren(switchFor(r));
  }
  if (state.selected === uid || !state.selected) renderDetail();
  renderThemeChips();
}

// ---------- wiring ----------
$("#q").addEventListener("input", (e) => { state.q = e.target.value; renderList(); });
for (const b of document.querySelectorAll("[data-scope]")) {
  b.addEventListener("click", () => { state.scope = b.dataset.scope; renderAll(); });
}
$("#only-open").addEventListener("click", () => { state.onlyOpen = !state.onlyOpen; renderAll(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && detailWrap.classList.contains("open")) select(null); });
phone.addEventListener("change", renderDetail);

renderAll();
if (state.selected) listEl.querySelector(`[data-uid="${CSS.escape(state.selected)}"]`)?.scrollIntoView({ block: "center" });
