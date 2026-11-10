// Ideen-Seite: startet die Vorschläge über /api/ideen/start, zeigt „Gericht 2 von 6“ und die
// verstrichene Zeit und lädt die Seite am Ende neu. Ohne JavaScript schickt das Formular
// POST /ideen/erzeugen und wartet, bis alle Vorschläge da sind.
document.addEventListener("alpine:init", () => {
  Alpine.data("ideas", () => ({
    running: false,
    done: 0,
    total: 0,
    seconds: 0,
    error: "",
    timer: null,

    init() {
      fetch("/api/ideen/status", { cache: "no-store" })
        .then((r) => r.json())
        .then((s) => s.laeuft && this.follow(s))
        .catch(() => {});
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

    async start() {
      this.error = "";
      try {
        const res = await fetch("/api/ideen/start", { method: "POST" });
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
