// Rezeptverwaltung (Alpine.js): Zustand und Logik. Das Markup steht in templates/rezepte.html.
// Jede Änderung wird sofort nach Paprika geschrieben und bei einem Fehler zurückgenommen.

document.addEventListener("alpine:init", () => {
  const DATA = JSON.parse(document.getElementById("data").textContent);
  const fold = (s) => s.toLocaleLowerCase("de");
  const same = (a, b) => fold(a) === fold(b);
  const hasTag = (r, t) => r.tags.some((x) => same(x, t));
  const time = () => new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const phoneQuery = window.matchMedia("(max-width: 640px)");

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  Alpine.data("recipes", () => ({
    recipes: DATA.recipes,
    groups: DATA.groups,
    themes: DATA.themes,
    kidTag: DATA.kidTag,
    mainTag: DATA.mainTag,
    canWrite: DATA.canWrite,
    need: DATA.rotationNeed,

    q: "",
    scope: "main",
    theme: null, // null = alle, "__none" = ohne Thema
    onlyOpen: false,
    selectedUid: null,
    newTag: "",
    details: {}, // uid -> { loading } | { error } | { zutaten, portionen, zeit, ... }
    expanded: {},
    status: {}, // uid -> { kind: "busy" | "ok" | "err", text, retry }
    toast: { visible: false, text: "", action: null },
    phone: phoneQuery.matches,
    queues: {},

    init() {
      phoneQuery.addEventListener("change", (e) => (this.phone = e.matches));
      // presets from links: "Vorrat pro Thema" (?thema=&kinder=nein) and the week plan (?rezept=)
      const params = new URLSearchParams(location.search);
      if (params.get("thema")) this.theme = params.get("thema");
      if (params.get("kinder") === "nein") this.onlyOpen = true;
      const uid = params.get("rezept");
      const preset = uid && this.recipes.find((r) => r.uid === uid);
      if (preset) {
        if (!hasTag(preset, this.mainTag)) this.scope = "all";
        this.select(uid);
        this.$nextTick(() =>
          this.$root.querySelector(`[data-uid="${CSS.escape(uid)}"]`)?.scrollIntoView({ block: "center" }),
        );
      }
    },

    // ---------- helpers for the template ----------
    same,
    hasTag,
    initial: (r) => r.name.trim().charAt(0).toUpperCase(),
    photoUrl: (r) => `/bilder/${encodeURIComponent(r.uid)}.jpg`,
    otherTags(r) {
      const rest = r.tags.filter((t) => !r.themen.some((x) => same(x, t)) && !same(t, this.kidTag));
      if (!rest.length) return r.themen.length ? "" : "keine Kategorie";
      return (r.themen.length ? " · " : "") + rest.join(" · ");
    },

    // ---------- filtering ----------
    get base() {
      return this.recipes.filter((r) => this.scope !== "main" || hasTag(r, this.mainTag));
    },
    get words() {
      return fold(this.q).split(/[\s,]+/).filter(Boolean);
    },
    inIngredients: (r, w) => (r.zutaten || []).some((z) => fold(z).includes(w)),
    // ingredients that a search word found, shown under the name ("mit Zucchini, Feta")
    hits(r) {
      if (!this.words.length) return [];
      return (r.zutaten || []).filter((z) => this.words.some((w) => fold(z).includes(w) && !fold(r.name).includes(w)));
    },
    get visible() {
      return this.base.filter((r) => {
        if (this.onlyOpen && r.kinder_ok) return false;
        if (this.theme === "__none" && r.themen.length) return false;
        if (this.theme && this.theme !== "__none" && !hasTag(r, this.theme)) return false;
        // every word must match the name, a category or an ingredient: "zucchini feta"
        return this.words.every((w) => fold(r.name).includes(w) || r.tags.some((t) => fold(t).includes(w)) || this.inIngredients(r, w));
      });
    },
    countTheme(t) {
      return this.base.filter((r) => hasTag(r, t)).length;
    },
    countNoTheme() {
      return this.base.filter((r) => !r.themen.length).length;
    },
    get listFoot() {
      if (!this.visible.length) {
        return this.onlyOpen
          ? "Alles markiert: In dieser Auswahl ist jedes Gericht Kinder-OK."
          : "Kein Rezept passt zu Suche und Filter.";
      }
      return this.canWrite
        ? "Änderungen landen sofort in Paprika."
        : "Paprika ist nicht verbunden, Änderungen sind deshalb nicht möglich.";
    },
    get supply() {
      return this.themes.map((t) => {
        const themed = this.recipes.filter((r) => hasTag(r, t) && r.rueckmeldung !== "nein");
        return { t, n: themed.length, kid: themed.filter((r) => r.kinder_ok).length };
      });
    },

    // ---------- detail ----------
    get selected() {
      return this.recipes.find((r) => r.uid === this.selectedUid) || null;
    },
    select(uid) {
      this.selectedUid = uid;
      this.newTag = "";
      if (uid) this.loadDetails(uid);
    },
    loadDetails(uid, retry = false) {
      const d = this.details[uid];
      if (d && !d.error && !retry) return;
      this.details[uid] = { loading: true };
      fetch(`/api/rezepte/${encodeURIComponent(uid)}`)
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.status))))
        .then((data) => (this.details[uid] = data))
        .catch(() => (this.details[uid] = { error: true, zutaten: [] }));
    },
    ingredientCount: (d) => (d?.zutaten || []).filter((z) => !z.kopf).length,
    shownIngredients(d) {
      if (!d?.zutaten) return [];
      // up to 10 lines show in full; longer lists start with 8 and expand
      return d.zutaten.length <= 10 || this.expanded[this.selectedUid] ? d.zutaten : d.zutaten.slice(0, 8);
    },
    facts(r) {
      const d = this.details[r.uid] || {};
      const parts = [r.rating ? `Bewertung ${r.rating} von 5` : "Ohne Bewertung"];
      if (d.portionen) parts.push(/^\d+$/.test(d.portionen) ? `${d.portionen} Portionen` : d.portionen);
      if (d.zeit) parts.push(d.zeit);
      return parts.join(" · ");
    },
    get detailGroups() {
      const r = this.selected;
      const groups = this.groups.map((g) => ({ name: g.name, tags: [...g.tags] }));
      const known = new Set(groups.flatMap((g) => g.tags.map(fold)));
      // a tag on the recipe that no group lists yet goes into the last group
      if (r) for (const t of r.tags) if (!known.has(fold(t))) groups[groups.length - 1].tags.push(t);
      return groups;
    },

    // ---------- writing ----------
    toggleTag(r, tag, on) {
      if (!this.canWrite) return;
      const before = { tags: [...r.tags], kinder_ok: r.kinder_ok, kinder_tag: r.kinder_tag, themen: [...r.themen] };
      if (on && !hasTag(r, tag)) r.tags.push(tag);
      if (!on) r.tags = r.tags.filter((t) => !same(t, tag));
      r.kinder_tag = hasTag(r, this.kidTag);
      r.kinder_ok = r.kinder_tag || r.rueckmeldung === "ok";
      r.themen = this.themes.filter((t) => hasTag(r, t));
      this.status[r.uid] = { kind: "busy", text: "Wird in Paprika gespeichert …" };

      const body = on ? { hinzufuegen: [tag], entfernen: [] } : { hinzufuegen: [], entfernen: [tag] };
      const run = () =>
        post(`/api/rezepte/${encodeURIComponent(r.uid)}/tags`, body)
          .then((json) => {
            Object.assign(r, json);
            this.status[r.uid] = { kind: "ok", text: `In Paprika gespeichert · ${time()}` };
          })
          .catch((err) => {
            Object.assign(r, before);
            this.status[r.uid] = {
              kind: "err",
              text: `Nicht gespeichert: ${err.message}.`,
              retry: () => this.toggleTag(r, tag, on),
            };
          });
      // edits to one recipe run in order
      this.queues[r.uid] = (this.queues[r.uid] || Promise.resolve()).then(run, run);
    },
    addNewTag() {
      const name = this.newTag.trim();
      const r = this.selected;
      if (!name || !r) return;
      const last = this.groups[this.groups.length - 1];
      if (!this.groups.some((g) => g.tags.some((t) => same(t, name)))) last.tags.push(name);
      this.newTag = "";
      if (!hasTag(r, name)) this.toggleTag(r, name, true);
    },

    // ---------- trash with undo ----------
    showToast(text, action = null) {
      clearTimeout(this._toastTimer);
      this.toast = { visible: true, text, action };
      this._toastTimer = setTimeout(() => (this.toast.visible = false), 10000);
    },
    async trash(r) {
      const question = `„${r.name}“ in den Papierkorb von Paprika legen?\n\nDu kannst es danach hier rückgängig machen oder in Paprika wiederherstellen.`;
      if (!confirm(question)) return;
      this.status[r.uid] = { kind: "busy", text: "Wird in den Papierkorb gelegt …" };
      try {
        await post(`/api/rezepte/${encodeURIComponent(r.uid)}/papierkorb`);
      } catch (err) {
        this.status[r.uid] = { kind: "err", text: `Nicht verschoben: ${err.message}.`, retry: () => this.trash(r) };
        return;
      }
      const index = this.recipes.indexOf(r);
      this.recipes.splice(index, 1);
      delete this.status[r.uid];
      this.select(null);
      this.showToast(`„${r.name}“ liegt im Papierkorb.`, { label: "Rückgängig", run: () => this.restore(r, index) });
    },
    async restore(r, index) {
      this.showToast(`„${r.name}“ wird wiederhergestellt …`);
      try {
        Object.assign(r, await post(`/api/rezepte/${encodeURIComponent(r.uid)}/wiederherstellen`));
      } catch (err) {
        this.showToast(`Wiederherstellen fehlgeschlagen: ${err.message}. In Paprika liegt es im Papierkorb.`);
        return;
      }
      this.recipes.splice(Math.min(index, this.recipes.length), 0, r);
      this.showToast(`„${r.name}“ ist wieder da.`);
      this.select(r.uid);
    },
  }));
});
