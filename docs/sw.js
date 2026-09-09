/* Brickonomy service worker.
 *
 * 20260909062053 is replaced at serve/export time: the live app
 * stamps it with the asset version, the static exporter with the export
 * time. A republish therefore changes this file byte-for-byte, the browser
 * installs the new worker, and `activate` below drops every cache from the
 * previous version — fresh prices within one reload of a republish.
 *
 * Strategy: network first for pages and JSON (always current when online,
 * last-synced copy when not), cache first for /static/ assets, whose URLs
 * already carry a ?v= stamp. Cross-origin requests (set images live on
 * external CDNs) are left to the browser.
 */
const VERSION = "20260909062053";
const CACHE = "brickonomy-" + VERSION;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  const isStatic = url.pathname.includes("/static/");
  event.respondWith(isStatic ? cacheFirst(req) : networkFirst(req));
});

function cacheFirst(req) {
  return caches.match(req).then((hit) => hit || fetchAndCache(req));
}

function networkFirst(req) {
  return fetchAndCache(req).catch(() =>
    caches.match(req).then((hit) => {
      if (hit) return hit;
      throw new Error("offline and uncached: " + req.url);
    })
  );
}

function fetchAndCache(req) {
  return fetch(req).then((resp) => {
    if (resp.ok) {
      const copy = resp.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
    }
    return resp;
  });
}
