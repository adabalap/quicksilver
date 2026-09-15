/* Quicksilver SW v5 — the app opens even when the Termux server is down. */
const CACHE = "hg-v5";
const SHELL = ["/", "/manifest.json", "/icons/icon-192.svg", "/icons/icon-512.svg",
               "/icons/favicon.svg", "/icons/favicon.ico",
               "/icons/apple-touch-icon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(()=>{})
    .then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return;            // data: network only
  if (e.request.mode === "navigate") {                     // shell: fresh, else cache
    e.respondWith(fetch(e.request).then(r => {
      caches.open(CACHE).then(c => c.put("/", r.clone()));
      return r;
    }).catch(() => caches.match("/")));
    return;
  }
  e.respondWith(caches.match(e.request).then(hit =>       // static/fonts: cache-first
    hit || fetch(e.request).then(r => {
      if (r.ok || r.type === "opaque")
        caches.open(CACHE).then(c => c.put(e.request, r.clone()));
      return r;
    })));
});

/* ── Notifications ────────────────────────────────────────────────────────────
   The page asks the SW to show these (via postMessage) rather than calling
   Notification directly: only a SW notification survives the app being
   backgrounded or closed, which is the whole point — the agent enriches in the
   background and that is exactly when you are not looking at the screen. */
self.addEventListener("message", e => {
  const d = e.data || {};
  if (d.type !== "notify") return;
  self.registration.showNotification(d.title || "Quicksilver", {
    body: d.body || "",
    icon: "/icons/icon-192.png",       // the Hg mark in the tray
    badge: "/icons/badge-72.png",      // monochrome silhouette for the status bar
    tag: d.tag || "hg",                // same tag REPLACES, never stacks up
    renotify: false,                   // ...and replacing shouldn't buzz again
    silent: true,                      // background enrichment is not urgent
    data: { url: d.url || "/" },
  });
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "/";
  // Focus an existing window if we have one — never pile up duplicate tabs.
  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true })
    .then(list => {
      for (const c of list) {
        if ("focus" in c) { c.navigate(target).catch(()=>{}); return c.focus(); }
      }
      if (clients.openWindow) return clients.openWindow(target);
    }));
});
