// 서비스워커: 웹 푸시 수신 + 알림 클릭 시 해당 글로 이동
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let d = {}; try { d = e.data.json(); } catch (_) { d = { title: '새 알림', body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || '새 알림', {
    body: d.body || '', icon: '/static/icons/icon-192.png', badge: '/static/icons/icon-192.png',
    data: { url: (d.url || '/notifications') + ((d.url || '').includes('?') ? '&' : '?') + 'src=push' },
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.openWindow(e.notification.data.url));
});
