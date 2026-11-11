// Offline cache for the app shell, so the installed web app opens without a network.
const CACHE = "openajazz-v6";
const SHELL = ["./", "index.html", "style.css", "app.js", "protocol.js", "effects.js", "data.js",
  "icon.svg", "manifest.webmanifest", "ticker.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

// network first so updates show up right away, cache as the fallback
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(fetch(e.request).then((res) => {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request)));
});
