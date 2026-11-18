// Einkaufsliste: abhaken auch ohne Netz (Warteschlange im Browser, wird nachgeholt) und live,
// wenn zwei gleichzeitig einkaufen (alle 5 Sekunden der Stand vom Server). Markup im Template.
// Der Zustand jedes Eintrags liegt hier zentral (items): Abgehaktes wandert so sofort nach
// "Im Wagen", und die Zähler rechnen sich selbst.
document.addEventListener("alpine:init", () => {
  const QUEUE = "tischlein-einkauf-warteschlange";
  const OFFLINE = "Keine Verbindung. Bitte noch einmal versuchen.";
  const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  function readQueue() {
    try {
      return JSON.parse(localStorage.getItem(QUEUE) || "[]");
    } catch {
      return [];
    }
  }
  function writeQueue(q) {
    try {
      localStorage.setItem(QUEUE, JSON.stringify(q));
    } catch {
      /* private mode: then the change is only kept while the page is open */
    }
  }

  Alpine.data("shopping", (which, state) => ({
    // {id: {done, by, gone}}; gone = deleted here, kept until the page reloads so Rückgängig can bring it back
    items: Object.fromEntries(Object.entries(state).map(([id, s]) => [id, { ...s, gone: false }])),
    cartOpen: false, name: "", amount: "", error: "", busy: false,
    toast: { text: "", undo: null }, toastTimer: null,
    offline: !navigator.onLine, pending: readQueue().length, timer: null, changedAt: 0,

    get live() {
      return Object.values(this.items).filter((i) => !i.gone);
    },
    get total() {
      return this.live.length;
    },
    get left() {
      return this.live.filter((i) => !i.done).length;
    },
    get inCart() {
      return this.live.filter((i) => i.done).length;
    },
    shown(id, inCart) {
      const i = this.items[id];
      return !!i && !i.gone && i.done === inCart;
    },
    groupOpen(ids) {
      return ids.some((id) => this.shown(id, false));
    },

    init() {
      addEventListener("online", () => { this.offline = false; this.sync(); });
      addEventListener("offline", () => (this.offline = true));
      document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && this.sync());
      this.timer = setInterval(() => document.visibilityState === "visible" && this.sync(), 5000);
      this.sync();
    },

    say(text, undo = null) {
      clearTimeout(this.toastTimer);
      this.toast = { text, undo };
      this.toastTimer = setTimeout(() => (this.toast = { text: "", undo: null }), undo ? 8000 : 4000);
    },

    // one checkbox: send it, or queue it when there is no connection
    async toggle(id, done) {
      this.changedAt = Date.now();  // a poll that started before this must not undo it
      this.items[id].done = done;
      try {
        const res = await fetch(`/api/einkauf/${id}/erledigt`, json({ erledigt: done }));
        if (res.ok) {
          const rest = readQueue().filter((x) => x.id !== id);  // an older offline tick is now outdated
          writeQueue(rest);
          this.pending = rest.length;
          this.items[id].by = (await res.json()).von;
          return;
        }
        if (res.status === 404) return;  // removed on another phone meanwhile
        throw new Error(String(res.status));
      } catch {
        const q = readQueue().filter((x) => x.id !== id);
        q.push({ id, done });
        writeQueue(q);
        this.pending = q.length;
        this.offline = true;
      }
    },

    async flush() {
      let q = readQueue();
      while (q.length) {
        const { id, done } = q[0];
        const res = await fetch(`/api/einkauf/${id}/erledigt`, json({ erledigt: done }));
        // a server error: try again later; anything else the server rejected (gone, invalid) is dropped,
        // so one bad entry never blocks the queue
        if (res.status >= 500) throw new Error(String(res.status));
        q = readQueue().filter((x) => !(x.id === id && x.done === done));
        writeQueue(q);
        this.pending = q.length;
      }
    },

    // queued changes first, then take over what the others ticked; a new or removed entry reloads
    async sync() {
      const started = Date.now();
      try {
        await this.flush().catch(() => {});  // still take over the others' ticks
        const res = await fetch(`/api/einkauf?woche=${which}`, { cache: "no-store", redirect: "error" });
        if (!res.ok) throw new Error(String(res.status));
        const rows = await res.json();
        this.offline = false;
        if (this.changedAt >= started) return;  // changed meanwhile: the next poll brings the full state
        const mine = new Set(Object.keys(this.items).filter((id) => !this.items[id].gone).map(Number));
        const typing = document.activeElement?.closest?.(".add-row");
        if (!typing && !this.toast.undo && (rows.length !== mine.size || rows.some((r) => !mine.has(r.id)))) {
          location.reload();
          return;
        }
        const queued = new Set(readQueue().map((x) => x.id));
        for (const r of rows) {
          const item = this.items[r.id];
          if (!item || queued.has(r.id)) continue;
          item.done = !!r.done;
          item.by = r.done_by || "";
        }
      } catch {
        this.offline = !navigator.onLine || this.pending > 0 || this.offline;
      }
    },

    async add() {
      this.error = "";
      this.busy = true;
      let res;
      try {
        res = await fetch(`/api/einkauf/${which}/eintrag`, json({ name: this.name, menge: this.amount }));
      } catch {
        this.busy = false;
        this.error = OFFLINE;
        return;
      }
      if (!res.ok) {
        this.busy = false;
        this.error = (await res.json().catch(() => ({}))).detail || "Konnte nicht speichern.";
        return;
      }
      location.reload();
    },

    async allDone(done) {
      this.$root.querySelectorAll("details[open]").forEach((d) => d.removeAttribute("open"));
      const res = await fetch(`/api/einkauf/${which}/alle-erledigt`, json({ erledigt: done })).catch(() => null);
      if (res && res.ok) location.reload();
      else this.error = OFFLINE;
    },

    // delete now, keep the entries for Rückgängig
    async removeDone() {
      this.changedAt = Date.now();
      const res = await fetch(`/api/einkauf/${which}/erledigte-entfernen`, { method: "POST" }).catch(() => null);
      if (!res || !res.ok) return (this.error = OFFLINE);
      const removed = (await res.json()).eintraege || [];
      if (!removed.length) return this.say("Nichts zu entfernen.");
      for (const r of removed) if (this.items[r.id]) this.items[r.id].gone = true;
      this.say(`${removed.length} ${removed.length === 1 ? "Eintrag" : "Einträge"} entfernt`, removed);
    },

    async remove(id) {
      this.changedAt = Date.now();
      const res = await fetch(`/api/einkauf/${id}/loeschen`, { method: "POST" }).catch(() => null);
      if (!res || !res.ok) return (this.error = OFFLINE);
      const removed = (await res.json()).eintraege || [];
      this.items[id].gone = true;
      this.say(removed.length ? `${removed[0].name} gelöscht` : "Gelöscht", removed.length ? removed : null);
    },

    async undo() {
      const rows = this.toast.undo;
      if (!rows) return;
      this.changedAt = Date.now();
      const res = await fetch(`/api/einkauf/${which}/wiederherstellen`, json({ eintraege: rows })).catch(() => null);
      if (!res || !res.ok) return this.say("Konnte nicht wiederherstellen.");
      if (!(await res.json()).wiederhergestellt) return location.reload();  // gone meanwhile elsewhere: show what is
      for (const r of rows) if (this.items[r.id]) Object.assign(this.items[r.id], { gone: false, done: !!r.done, by: r.done_by || "" });
      this.say("Wiederhergestellt");
    },

    // open items to the Bring list (Home Assistant puts them there); ticked ones stay here
    async toBring() {
      this.busy = true;
      const res = await fetch(`/api/einkauf/${which}/bring`, { method: "POST" }).catch(() => null);
      this.busy = false;
      if (!res) this.error = OFFLINE;
      else if (!res.ok) this.error = (await res.json().catch(() => ({}))).detail || "Konnte nicht senden.";
      else {
        const n = (await res.json()).gesendet;
        this.say(n ? `Mit Bring abgeglichen: ${n} ${n === 1 ? "Eintrag" : "Einträge"} offen.` : "Mit Bring abgeglichen, nichts offen.");
      }
    },

    // the open items as text for the share sheet (Erinnerungen, Bring, Nachrichten) or the clipboard
    async share(title) {
      const lines = [title, ""];
      for (const group of this.$root.querySelectorAll(".shop-group")) {
        const open = [...group.querySelectorAll("li[data-id]")].filter((li) => this.shown(li.dataset.id, false));
        if (!open.length) continue;
        lines.push(group.querySelector("h3").textContent.trim());
        for (const li of open) {
          const amount = li.querySelector(".shop-amount")?.textContent.trim();
          lines.push(`- ${li.querySelector(".shop-name").textContent.trim()}${amount ? " " + amount : ""}`);
        }
        lines.push("");
      }
      const text = lines.join("\n").trim();
      try {
        if (navigator.share) await navigator.share({ title, text });
        else {
          await navigator.clipboard.writeText(text);
          this.say("Liste kopiert.");
        }
      } catch {
        /* share sheet closed */
      }
    },
  }));
});
