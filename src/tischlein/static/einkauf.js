// Einkaufsliste: abhaken auch ohne Netz (Warteschlange im Browser, wird nachgeholt) und live,
// wenn zwei gleichzeitig einkaufen (alle 5 Sekunden der Stand vom Server). Markup im Template.
document.addEventListener("alpine:init", () => {
  const QUEUE = "tischlein-einkauf-warteschlange";
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

  Alpine.data("shopping", (which) => ({
    hideDone: false, name: "", amount: "", error: "", busy: false,
    offline: !navigator.onLine, pending: readQueue().length, timer: null, changedAt: 0,

    init() {
      addEventListener("online", () => { this.offline = false; this.sync(); });
      addEventListener("offline", () => (this.offline = true));
      document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && this.sync());
      this.timer = setInterval(() => document.visibilityState === "visible" && this.sync(), 5000);
      this.sync();
    },

    // one checkbox: send it, or queue it when there is no connection
    async toggle(id, done) {
      this.changedAt = Date.now();  // a poll that started before this must not undo it
      try {
        const res = await fetch(`/api/einkauf/${id}/erledigt`, json({ erledigt: done }));
        if (res.ok) {
          const rest = readQueue().filter((x) => x.id !== id);  // an older offline tick is now outdated
          writeQueue(rest);
          this.pending = rest.length;
          return (await res.json()).von;
        }
        if (res.status === 404) return "";  // removed on another phone meanwhile
        throw new Error(String(res.status));
      } catch {
        const q = readQueue().filter((x) => x.id !== id);
        q.push({ id, done });
        writeQueue(q);
        this.pending = q.length;
        this.offline = true;
        return "";
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
        const items = await res.json();
        this.offline = false;
        if (this.changedAt >= started) return;  // ticked meanwhile: the next poll brings the full state
        const rows = [...this.$root.querySelectorAll("li[data-id]")];
        const shown = new Set(rows.map((li) => Number(li.dataset.id)));
        const typing = document.activeElement?.closest?.(".add-row");
        if (!typing && (items.length !== shown.size || items.some((i) => !shown.has(i.id)))) {
          location.reload();
          return;
        }
        const queued = new Set(readQueue().map((x) => x.id));
        for (const li of rows) {
          const item = items.find((i) => i.id === Number(li.dataset.id));
          if (!item || queued.has(item.id)) continue;
          const data = Alpine.$data(li);
          data.done = !!item.done;
          data.by = item.done_by || "";
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
        this.error = "Keine Verbindung. Bitte noch einmal versuchen.";
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
      const res = await fetch(`/api/einkauf/${which}/alle-erledigt`, json({ erledigt: done })).catch(() => null);
      if (res && res.ok) location.reload();
      else this.error = "Keine Verbindung. Bitte noch einmal versuchen.";
    },

    async removeDone() {
      if (!confirm("Alle abgehakten Einträge von der Liste entfernen?")) return;
      const res = await fetch(`/api/einkauf/${which}/erledigte-entfernen`, { method: "POST" }).catch(() => null);
      if (res && res.ok) location.reload();
      else this.error = "Keine Verbindung. Bitte noch einmal versuchen.";
    },

    async remove(id, el) {
      const res = await fetch(`/api/einkauf/${id}/loeschen`, { method: "POST" }).catch(() => null);
      if (res && res.ok) el.remove();
      else this.error = "Keine Verbindung. Bitte noch einmal versuchen.";
    },

    // open items to the Bring list (Home Assistant puts them there); ticked ones stay here
    async toBring() {
      this.busy = true;
      const res = await fetch(`/api/einkauf/${which}/bring`, { method: "POST" }).catch(() => null);
      this.busy = false;
      if (!res) this.error = "Keine Verbindung. Bitte noch einmal versuchen.";
      else if (!res.ok) this.error = (await res.json().catch(() => ({}))).detail || "Konnte nicht senden.";
      else {
        const n = (await res.json()).gesendet;
        this.error = n ? `Mit Bring abgeglichen: ${n} ${n === 1 ? "Eintrag" : "Einträge"} offen.` : "Mit Bring abgeglichen, nichts offen.";
      }
    },

    // the open items as text for the share sheet (Erinnerungen, Bring, Nachrichten) or the clipboard
    async share(title) {
      const lines = [title, ""];
      for (const group of this.$root.querySelectorAll(".shop-group")) {
        const open = [...group.querySelectorAll("li[data-id]")].filter((li) => !Alpine.$data(li).done);
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
          this.error = "Liste kopiert.";
        }
      } catch {
        /* share sheet closed */
      }
    },
  }));
});
