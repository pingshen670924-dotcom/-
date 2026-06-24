const CACHE_NAME = "香港六合彩預測系統-20260625-v9-install";
const ASSETS = ["./香港六合彩預測系統_手機首頁.html","./香港六合彩預測系統_手機狀態.json","./香港六合彩預測系統_手機設定.json","./香港六合彩預測系統_手機圖示192.png","./香港六合彩預測系統_手機圖示512.png","./香港六合彩預測系統_完整戰報.html","./香港六合彩預測系統_最新預測.html","./香港六合彩預測系統_系統報告.html","./香港六合彩預測系統_歷史資料.csv"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)).catch(() => undefined));
  self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener("fetch", event => {
  event.respondWith(fetch(event.request).then(response => {
    const copy = response.clone();
    caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => undefined);
    return response;
  }).catch(() => caches.match(event.request).then(cached => cached || caches.match("./香港六合彩預測系統_手機首頁.html"))));
});
