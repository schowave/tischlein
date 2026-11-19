// Ideen-Seite: startet die Vorschläge über /api/ideen/start, zeigt „Gericht 2 von 6“ und die
// verstrichene Zeit und lädt die Seite am Ende neu. Ohne JavaScript schickt das Formular
// POST /ideen/erzeugen und wartet, bis alle Vorschläge da sind.
// Stöbern: Die Suche lädt die Seite neu und die fremde Seite braucht einen Moment. Vom Klick bis zur Antwort
// stehen Platzhalter an der Stelle der Treffer, damit sichtbar ist, dass etwas passiert.
document.addEventListener("alpine:init", () => {
  Alpine.data("ideas", () => ({
    running: false,
    done: 0,
    total: 0,
    seconds: 0,
    error: "",
    timer: null,
    searching: "",
    site: "",
    typed: "",
    cleared: false,

    init() {
      fetch("/api/ideen/status", { cache: "no-store" })
        .then((r) => r.json())
        .then((s) => s.laeuft && this.follow(s))
        .catch(() => {});
      // back from the browser's page cache: the placeholders of the last search must not linger
      window.addEventListener("pageshow", (e) => {
        if (!e.persisted) return;
        this.searching = "";
        document.querySelectorAll(".browse-list .is-opening").forEach((a) => a.classList.remove("is-opening"));
      });
    },

    // a click that opens a new tab or window leaves this page as it is
    stays(event) {
      return event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button);
    },

    // the navigation itself stays the browser's; this only shows what is loading
    // site: the source being switched to; empty means the one the page already shows
    search(query, event, site = "") {
      if (this.stays(event)) return;
      query = query.trim();
      if (!query) return;
      this.cleared = false;
      this.site = site;
      this.searching = query;
      if (event) this.$refs.q.value = query;
    },

    // the X empties the field and hides the hits without reloading, so the keyboard stays open
    clear(source) {
      this.typed = "";
      this.cleared = true;
      this.$refs.q.value = "";
      this.$refs.q.focus();
      history.replaceState(null, "", "/ideen?quelle=" + encodeURIComponent(source));
    },

    // a hit is fetched from its site before the preview shows
    opening(event) {
      if (!this.stays(event)) event.currentTarget.classList.add("is-opening");
    },

    get label() {
      if (this.done >= this.total && this.total) return "Vorschläge werden angezeigt …";
      return `Gericht ${Math.min(this.done + 1, this.total)} von ${this.total} wird ausgedacht …`;
    },

    get elapsed() {
      const m = Math.floor(this.seconds / 60);
      const s = String(this.seconds % 60).padStart(2, "0");
      return `${m}:${s}`;
    },

    // a sliver of the current step so the bar moves before the first dish arrives
    get percent() {
      return this.total ? Math.round(((this.done + 0.15) / this.total) * 100) : 5;
    },

    // theme: only ideas for this theme ("Ideen dazu" in Vorrat pro Thema), else for the thin ones
    async start(theme = "") {
      this.error = "";
      try {
        const res = await fetch("/api/ideen/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ thema: theme }),
        });
        const state = await res.json();
        if (!res.ok) throw new Error(state.detail || `Fehler ${res.status}`);
        this.follow(state);
      } catch (err) {
        this.error = err.message;
      }
    },

    async follow(state) {
      this.running = true;
      this.apply(state);
      this.timer ??= setInterval(() => this.seconds++, 1000);
      while (state.laeuft) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          state = await (await fetch("/api/ideen/status", { cache: "no-store" })).json();
        } catch {
          continue;
        }
        this.apply(state);
      }
      clearInterval(this.timer);
      this.timer = null;
      let hint = state.fehler || "";
      if (!hint && !state.ergebnis) hint = "Das Modell hat keine brauchbaren Vorschläge geliefert. Bitte noch einmal versuchen.";
      else if (!hint && state.ergebnis < state.gesamt) hint = `${state.ergebnis} von ${state.gesamt} Vorschlägen sind gekommen, der Rest ist am Modell gescheitert.`;
      location.href = "/ideen" + (hint ? `?hinweis=${encodeURIComponent(hint)}` : "");
    },

    apply(state) {
      this.done = state.fertig || 0;
      this.total = state.gesamt || 0;
    },
  }));
});
