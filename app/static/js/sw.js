// Reminders M1.3 — Service Worker for web push.
//
// Two events handled:
//   - 'push': render a native notification with title/body/data/actions
//   - 'notificationclick': either snooze (action button) or open chat (body)
//
// All other fetches pass through (no caching, no interception).
// The SW is registered at root scope by pushSetup.js.

self.addEventListener('push', (event) => {
    let payload;
    try {
        payload = event.data ? event.data.json() : {};
    } catch (e) {
        payload = { title: 'Reminder', body: 'You have a reminder.' };
    }

    const title = payload.title || 'Reminder';
    const options = {
        body: payload.body || '',
        icon: '/static/img/icon-192.png',
        badge: '/static/img/icon-192.png',
        data: payload.data || {},
        actions: payload.actions || [],
        requireInteraction: true,
    };

    event.waitUntil(self.registration.showNotification(title, options));
});


self.addEventListener('notificationclick', (event) => {
    const notif = event.notification;
    const data = notif.data || {};
    const action = event.action;

    notif.close();

    // Action buttons: snooze 10m / 1h — fire GET to the M1.2 snooze route.
    if (action === 'snooze_10m' || action === 'snooze_1h') {
        const dur = action === 'snooze_10m' ? '10m' : '1h';
        const url = `/reminders/${encodeURIComponent(data.rid)}/snooze?dur=${dur}&token=${encodeURIComponent(data.snoozeToken)}`;
        event.waitUntil(
            fetch(url, { method: 'GET', credentials: 'omit' })
                .catch((err) => console.warn('snooze fetch failed', err))
        );
        return;
    }

    // Body click (no action): open or focus the chat tab.
    const chatId = data.chat;
    const target = chatId ? `/c/${encodeURIComponent(chatId)}` : '/';
    event.waitUntil(
        (async () => {
            const allClients = await self.clients.matchAll({
                type: 'window',
                includeUncontrolled: true,
            });
            for (const client of allClients) {
                if (client.url.includes(target) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (self.clients.openWindow) {
                return self.clients.openWindow(target);
            }
        })()
    );
});


// On install, activate immediately. Don't wait for the next page load.
self.addEventListener('install', (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});
