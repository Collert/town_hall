{% load static %}// Town Hall service worker (base/push_views.service_worker). Makes the site installable,
// shows a page of its own when there's no connection, and displays web push
// notifications (base/push.py).
const CACHE = 'town-hall-v1';
const OFFLINE_URL = '{{ offline_url }}';
const PRECACHE = [OFFLINE_URL, '{% static "base/styles.css" %}', '{% static "base/offline.css" %}'];
const ICON = '{% static "base/favicon/web-app-manifest-192x192.png" %}';
const BADGE = '{% static "base/favicon/favicon-96x96.png" %}';
const PUBLIC_KEY = '{{ public_key }}';
const RENEW_URL = '{{ renew_url }}';
const ORG = '{{ company_name|escapejs }}';

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(caches.keys()
        .then((keys) => Promise.all(keys.filter((key) => key.startsWith('town-hall-') && key !== CACHE).map((key) => caches.delete(key))))
        .then(() => self.clients.claim()));
});

// Pages always come from the network; the offline page only stands in when that fails.
self.addEventListener('fetch', (event) => {
    if (event.request.mode !== 'navigate') return;
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE_URL)));
});

self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { body: event.data.text() };
    }
    const options = { body: data.body || '', icon: ICON, badge: BADGE, data: { url: data.url || '/' } };
    if (data.tag) {
        options.tag = data.tag;
        options.renotify = true;
    }
    const work = [self.registration.showNotification(data.title || ORG, options)];
    // listmonk campaigns: load the open-tracking pixel so listmonk counts the view.
    if (data.view) work.push(fetch(data.view, { mode: 'no-cors', credentials: 'omit' }).catch(() => {}));
    event.waitUntil(Promise.all(work));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = new URL(event.notification.data.url || '/', self.location.origin).href;
    event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
        const open = windows.find((w) => w.url === url && 'focus' in w);
        return open ? open.focus() : self.clients.openWindow(url);
    }));
});

function keyBytes(key) {
    const raw = atob((key + '='.repeat((4 - key.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

// The browser replaced the subscription: tell Town Hall the new one for the same device.
self.addEventListener('pushsubscriptionchange', (event) => {
    const old = event.oldSubscription;
    const fresh = event.newSubscription
        ? Promise.resolve(event.newSubscription)
        : self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(PUBLIC_KEY) });
    event.waitUntil(fresh.then((subscription) => fetch(RENEW_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_endpoint: old && old.endpoint, subscription: subscription.toJSON() }),
    })).catch(() => {}));
});
