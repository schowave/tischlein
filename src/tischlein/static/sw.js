// Service worker: only so the shopping list opens in a shop without signal.
// The list page: network first, the last good copy (of the last week opened) as fallback. Its data (/api/einkauf) is never
// answered from a cache, so the page knows it is offline and keeps ticks in its own queue.
// Static files: from the cache, refreshed in the background; an older ?v= copy is dropped.
const CACHE = "tischlein";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) =>
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  ),
);

const cacheable = (res) => res && res.ok && res.type === "basic" && !res.redirected;

async function store(cache, req, res) {
  const url = new URL(req.url);
  for (const old of await cache.keys()) {
    const o = new URL(old.url);
    if (o.pathname === url.pathname && o.search !== url.search) await cache.delete(old);
  }
  await cache.put(req, res);
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname === "/einkauf") {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (cacheable(res)) caches.open(CACHE).then((c) => store(c, req, res.clone()));  // the last week opened
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || Response.error())),
    );
  } else if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.open(CACHE).then((c) =>
        c.match(req).then((hit) => {
          const fresh = fetch(req)
            .then((res) => {
              if (cacheable(res)) store(c, req, res.clone());
              return res;
            })
            .catch(() => hit || Response.error());
          return hit || fresh;
        }),
      ),
    );
  }
});
