// Service Worker：让 H5 可"添加到主屏幕"并具备基本离线能力。
// 策略：同源 GET、非 /api 请求走「网络优先 + 缓存兜底」；接口与写操作一律不缓存。
// 说明：不预缓存太多资源，避免开发时拿到旧文件；版本号变化时清理旧缓存。
'use strict';

const CACHE = 'shopkeeper-shell-v1';
const SHELL = ['/', '/static/style.css', '/static/js/core.js', '/static/js/init.js'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;          // 接口不缓存（数据要新）
  if (url.pathname.startsWith('/pay/')) return;           // 收款页不缓存

  event.respondWith(
    fetch(req)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(req).then((m) => m || caches.match('/')))
  );
});
