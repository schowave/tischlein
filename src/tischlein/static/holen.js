// Rezept holen: „Nach Paprika übernehmen“ läuft im Hintergrund (/api/rezept-holen/start) und zeigt jeden
// Schritt, weil Foto und Paprika einen Moment brauchen. Am Ende geht es zum eingeplanten Tag (mit
// Rückgängig) oder zum Rezept. Ohne JavaScript schickt das Formular POST /rezept-holen in einem Rutsch.
(() => {
  const form = document.querySelector("form[data-accept]");
  if (!form) return;
  const note = form.querySelector("[data-day-note]");
  const progress = form.querySelector("[data-progress]");
  const error = form.querySelector("[data-error]");
  const button = form.querySelector("button[type=submit]");

  form.addEventListener("change", (e) => {
    if (e.target.name === "tag") note.textContent = e.target.dataset.note || "";
  });

  function lock(on) {
    form.querySelectorAll("[data-fields]").forEach((f) => (f.disabled = on));
    button.disabled = on;
    button.textContent = on ? "Wird übernommen …" : "Nach Paprika übernehmen";
  }

  function show(state, done = false) {
    const steps = state.schritte || [];
    const at = done ? steps.length : state.schritt || 0;
    progress.hidden = false;
    progress.querySelector(".bar span").style.width = `${Math.round(((at + (done ? 0 : 0.5)) / Math.max(steps.length, 1)) * 100)}%`;
    progress.querySelector("ol").replaceChildren(
      ...steps.map((label, i) => {
        const li = document.createElement("li");
        li.textContent = label;
        li.className = i < at ? "is-done" : i === at ? "is-now" : "";
        if (i === at) li.setAttribute("aria-current", "step");
        return li;
      }),
    );
  }

  function fail(text) {
    lock(false);
    progress.hidden = true;
    error.hidden = false;
    error.textContent = text || "Das hat nicht geklappt. Bitte noch einmal versuchen.";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = new FormData(form);
    const body = { url: data.get("url"), kategorien: data.getAll("kategorien"), tag: data.get("tag") || null };
    error.hidden = true;
    lock(true);
    let state;
    try {
      const res = await fetch("/api/rezept-holen/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      state = await res.json();
      if (!res.ok) throw new Error(typeof state.detail === "string" ? state.detail : `Fehler ${res.status}`);
    } catch (err) {
      fail(err.message);
      return;
    }
    while (state.laeuft) {
      show(state);
      await new Promise((r) => setTimeout(r, 400));
      try {
        state = await (await fetch("/api/rezept-holen/status", { cache: "no-store" })).json();
      } catch {
        continue;
      }
    }
    if (state.fehler) {
      fail(state.fehler);
      return;
    }
    show(state, true);
    location.href = state.ziel || "/rezepte";
  });
})();
