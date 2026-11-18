// Dropdowns (details.free-menu: "Frei", "Mehr") behave like menus: only one is open, and a click
// elsewhere or Escape closes it. Delegated on document, so it survives stay.js swapping the page.
(() => {
  const MENU = "details.free-menu";
  const closeAll = (except) =>
    document.querySelectorAll(`${MENU}[open]`).forEach((d) => d !== except && d.removeAttribute("open"));

  // "toggle" does not bubble: listen in the capture phase
  document.addEventListener(
    "toggle",
    (e) => {
      if (e.target.matches?.(MENU) && e.target.open) closeAll(e.target);
    },
    true,
  );
  document.addEventListener("click", (e) => {
    if (!e.target.closest(MENU)) closeAll();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = document.querySelector(`${MENU}[open]`);
    if (!open) return;
    open.removeAttribute("open");
    open.querySelector("summary")?.focus();
  });
})();
