self.addEventListener('push', event => {
    const payload = event.data.json();
    const options = {
        body: payload.body,
        icon: '/static/logo.png',
        badge: '/static/logo.png',
        data: { url: payload.url },
        tag: payload.tag,
        renotify: true
    };

    event.waitUntil(self.registration.showNotification(payload.title, options));
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    const targetUrl = new URL(event.notification.data.url || '/delivery', self.location.origin);
    if (targetUrl.origin !== self.location.origin) return;

    event.waitUntil((async () => {
        const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        for (const client of windows) {
            if (new URL(client.url).origin === self.location.origin) {
                await client.navigate(targetUrl.href);
                return client.focus();
            }
        }
        return self.clients.openWindow(targetUrl.href);
    })());
});
