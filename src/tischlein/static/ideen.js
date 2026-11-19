// Neues finden: Die Suche lädt die Seite neu und die fremde Seite braucht einen Moment. Vom Klick bis zur
// Antwort stehen Platzhalter an der Stelle der Treffer, damit sichtbar ist, dass etwas passiert. Ein
// eingefügter Rezept-Link geht ohne Platzhalter direkt zur Vorschau (/rezept-holen).
document.addEventListener("alpine:init", () => {
  Alpine.data("ideas", () => ({
    searching: "",
    site: "",
    typed: "",
    cleared: false,

    init() {
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
      if (!query || /https?:\/\//.test(query)) return;
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
  }));
});
