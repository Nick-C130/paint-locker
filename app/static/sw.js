// Minimal service worker so the app can be installed to a phone home screen.
// Network-first: Paint Locker always needs the server for data.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
