const CACHE_NAME = "rit-reexam-pwa-v5";
const APP_SHELL = [
  "/",
  "/static/manifest.webmanifest",
  "/static/icons/rit-scheduler.svg"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  // Never cache authentication, generation, or file downloads.
  if (url.pathname.startsWith("/auth/") ||
      url.pathname.startsWith("/reset-password") ||
      url.pathname.startsWith("/generate") ||
      url.pathname.startsWith("/download/")) {
    return;
  }

  if (request.method !== "GET") return;

  event.respondWith(
    fetch(request).then(response => {
      if (response.ok && url.origin === self.location.origin) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
      }
      return response;
    }).catch(() => caches.match(request).then(cached => cached || caches.match("/")))
  );
});
