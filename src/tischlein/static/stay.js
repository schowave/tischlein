// Formulare mit data-stay schicken im Hintergrund ab und tauschen nur den Seiteninhalt aus:
// keine neu geladene Seite, kein Sprung nach oben, der Fokus bleibt beim gedrückten Knopf.
// Ohne JavaScript (oder bei einem Fehler) läuft das Formular ganz normal.
(() => {
  const main = () => document.querySelector("main .col");

  // where the focus was, so it can come back after the swap: the day and the button's text
  function focusKey(el) {
    const day = el?.closest?.("[id]")?.id;
    return el && day ? { day, label: el.getAttribute("aria-label") || el.textContent.trim() } : null;
  }
  function restoreFocus(key) {
    if (!key) return;
    const scope = document.getElementById(key.day);
    const match = [...(scope?.querySelectorAll("button, summary, a") || [])].find(
      (b) => (b.getAttribute("aria-label") || b.textContent.trim()) === key.label,
    );
    match?.focus({ preventScroll: true });
  }

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (e.defaultPrevented || !form.matches("form[data-stay]") || !main()) return;
    e.preventDefault();
    const button = e.submitter;
    const key = focusKey(button || document.activeElement);
    const y = window.scrollY;
    form.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const body = new FormData(form);
    if (button?.name) body.append(button.name, button.value);
    let res;
    try {
      res = await fetch(form.action, { method: "POST", body, credentials: "same-origin" });
    } catch {
      form.removeAttribute("data-stay");
      form.submit();  // nothing arrived: the plain way, as without JavaScript
      return;
    }
    try {
      if (!res.ok || !res.headers.get("content-type")?.includes("text/html")) throw new Error(String(res.status));
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const fresh = doc.querySelector("main .col");
      if (!fresh) throw new Error("no content");
      main().replaceWith(fresh);
      const url = new URL(res.url);
      url.hash = "";
      history.replaceState(null, "", url.pathname + url.search);
      document.title = doc.title;
      window.scrollTo(0, y);
      restoreFocus(key);
    } catch {
      location.reload();  // the server has done it already: show its state instead of sending twice
    }
  });
})();
