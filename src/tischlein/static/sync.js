// Paprika-Abgleich im Hintergrund: startet über /api/sync/start, zeigt den Fortschritt im Banner
// oben auf jeder Seite und lädt die Seite nach dem Ende neu. Ohne JavaScript schicken die
// Formulare (data-sync) einfach POST /sync, der Server startet den Abgleich ebenfalls im Hintergrund.
(() => {
  const banner = document.getElementById("sync-banner");
  if (!banner) return;
  const text = document.getElementById("sync-text");
  const count = document.getElementById("sync-count");
  const fill = document.getElementById("sync-fill");
  const buttons = () => document.querySelectorAll("form[data-sync] button");
  let polling = false;

  const phases = {
    start: "Paprika-Abgleich startet …",
    kategorien: "Kategorien werden geladen …",
    liste: "Rezeptliste wird geprüft …",
    rezepte: "Geänderte Rezepte werden geladen",
  };

  function show(state) {
    banner.hidden = false;
    banner.classList.remove("err", "ok", "indeterminate");
    if (state.laeuft) {
      const determinate = state.phase === "rezepte" && state.gesamt > 0;
      banner.classList.toggle("indeterminate", !determinate);
      text.textContent = phases[state.phase] || phases.start;
      count.textContent = determinate ? `${state.fertig} von ${state.gesamt}` : "";
      fill.style.width = determinate ? `${Math.round((state.fertig / state.gesamt) * 100)}%` : "";
      buttons().forEach((b) => (b.disabled = true));
    } else if (state.fehler) {
      banner.classList.add("err");
      text.textContent = "Abgleich fehlgeschlagen";
      count.textContent = state.fehler;
      buttons().forEach((b) => (b.disabled = false));
    } else {
      banner.classList.add("ok");
      text.textContent = "Abgleich fertig";
      count.textContent = state.ergebnis || "";
    }
  }

  async function poll() {
    if (polling) return;
    polling = true;
    for (;;) {
      let state;
      try {
        state = await (await fetch("/api/sync/status", { cache: "no-store" })).json();
      } catch {
        await new Promise((r) => setTimeout(r, 3000));
        continue;
      }
      show(state);
      if (!state.laeuft) {
        polling = false;
        if (!state.fehler) setTimeout(() => location.reload(), 1200);
        return;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-sync]");
    if (!form) return;
    e.preventDefault();
    try {
      const res = await fetch("/api/sync/start", { method: "POST" });
      const state = await res.json();
      if (!res.ok) throw new Error(state.detail || `Fehler ${res.status}`);
      show(state);
      poll();
    } catch (err) {
      show({ laeuft: false, fehler: err.message });
    }
  });

  // a sync may already be running, e.g. started right after connecting Paprika in the settings
  fetch("/api/sync/status", { cache: "no-store" })
    .then((r) => r.json())
    .then((state) => state.laeuft && (show(state), poll()))
    .catch(() => {});
})();
