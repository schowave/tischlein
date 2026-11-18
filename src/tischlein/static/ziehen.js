// Woche: einen Tag am Griff (.day-grip) auf einen anderen ziehen tauscht die beiden (Verschieben).
// Abgeschickt wird das Formular, das auch "Mehr → Verschieben auf" nutzt; ohne JavaScript oder
// mit der Tastatur bleibt das Menü der Weg. Delegiert am Dokument, damit es den Seitentausch von
// stay.js übersteht.
(() => {
  const EDGE = 90; // px from the top or bottom edge where the page scrolls along
  let drag = null;

  const dayAt = (x, y) => document.elementFromPoint(x, y)?.closest?.(".day[data-day]") || null;

  function move(e) {
    if (!drag || e.pointerId !== drag.id) return;
    drag.x = e.clientX;
    drag.y = e.clientY;
    render();
  }

  // follow the finger, and mark the day it would swap with
  function render() {
    const dy = drag.y - drag.startY + (window.scrollY - drag.startScroll);
    drag.day.style.transform = `translateY(${dy}px)`;
    drag.day.style.pointerEvents = "none"; // look through the dragged card
    const over = dayAt(drag.x, drag.y);
    drag.day.style.pointerEvents = "";
    const target = over && drag.targets.includes(over.dataset.day) ? over : null;
    if (target !== drag.target) {
      drag.target?.classList.remove("is-drop");
      target?.classList.add("is-drop");
      drag.target = target;
    }
  }

  function scrollAlong() {
    if (!drag) return;
    const speed = drag.y < EDGE ? -10 : drag.y > window.innerHeight - EDGE ? 10 : 0;
    if (speed) {
      window.scrollBy(0, speed);
      render();
    }
    drag.frame = requestAnimationFrame(scrollAlong);
  }

  function end(e, drop) {
    if (!drag || (e && e.pointerId !== undefined && e.pointerId !== drag.id)) return;
    const { day, target, grip, frame } = drag;
    cancelAnimationFrame(frame);
    drag = null;
    day.classList.remove("is-dragging");
    day.style.transform = "";
    target?.classList.remove("is-drop");
    document.body.classList.remove("is-dragging-day");
    try {
      grip.releasePointerCapture(e?.pointerId);
    } catch {
      /* was not captured */
    }
    if (!drop || !target) return;
    const field = [...day.querySelectorAll('form[action$="/verschieben"] input[name="nach"]')].find(
      (i) => i.value === target.dataset.day,
    );
    field?.form.requestSubmit(); // stay.js sends it and swaps the page
  }

  document.addEventListener("pointerdown", (e) => {
    const grip = e.target.closest?.(".day-grip");
    if (!grip || drag || (e.pointerType === "mouse" && e.button !== 0)) return;
    const day = grip.closest(".day");
    e.preventDefault();
    try {
      grip.setPointerCapture(e.pointerId); // keeps the moves coming when the finger leaves the grip
    } catch {
      /* no such pointer any more: the document listeners still follow it */
    }
    drag = {
      id: e.pointerId, grip, day, target: null,
      targets: (grip.dataset.targets || "").split(",").filter(Boolean),
      startY: e.clientY, startScroll: window.scrollY, x: e.clientX, y: e.clientY, frame: 0,
    };
    day.classList.add("is-dragging");
    document.body.classList.add("is-dragging-day");
    drag.frame = requestAnimationFrame(scrollAlong);
  });
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", (e) => end(e, true));
  document.addEventListener("pointercancel", (e) => end(e, false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && end(null, false));
})();
