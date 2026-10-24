// Lantern's service worker: shows push notifications and opens the app when one is tapped.
// It caches only the app shell (for an offline page). API responses are never cached: recall status must be live.
const SHELL = "lantern-shell-v1";

self.addEventListener("install", (event) => {
  // Best effort: if storage is unavailable (private mode, quota), skip the offline page but still install,
  // because notifications matter more than offline use.
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/", "/manifest.webmanifest", "/icons/icon-192.png"]))
    .catch((err) => console.warn("Lantern: offline cache skipped", err)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .catch((err) => console.warn("Lantern: offline cache skipped", err)));
  event.waitUntil(self.clients.claim().catch((err) => console.warn("Lantern: clients.claim failed", err)));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  if (event.request.mode === "navigate") {
    // Network first, so a new version always shows; the cached shell only when offline.
    // After 10 s (e.g. a stalled cold start) the cached shell is shown; its API calls still go to the network.
    event.respondWith(fetch(event.request, { signal: AbortSignal.timeout(10_000) })
      .catch(async () => (await caches.match("/")) || new Response(
        "<!doctype html><meta name=viewport content='width=device-width'><title>Lantern</title>" +
        "<p style='font:16px system-ui;padding:24px'>You're offline. Lantern needs a connection to check recalls.</p>",
        { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } })));
  }
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Lantern", {
    body: data.body || "", icon: "/icons/icon-192.png", badge: "/icons/icon-192.png",
    data: { url: data.url || "/" }, tag: "lantern-recall", renotify: true,
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    for (const w of wins) { if (w.url.startsWith(location.origin)) { w.navigate(target); return w.focus(); } }
    return self.clients.openWindow(target);
  }));
});
