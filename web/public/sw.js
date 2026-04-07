// Service Worker — KB 클리퍼 PWA
// 오프라인 fallback + PWA 설치 지원

const CACHE_NAME = "kb-clipper-v1";
const OFFLINE_URL = "/clip";

// 설치: 클리퍼 페이지를 캐시
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll([OFFLINE_URL, "/"])
    )
  );
  self.skipWaiting();
});

// 활성화: 이전 캐시 정리
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      )
    ).then(() => clients.claim())
  );
});

// fetch: 네비게이션 실패 시 클리퍼 페이지로 fallback
self.addEventListener("fetch", (e) => {
  if (e.request.mode === "navigate") {
    e.respondWith(
      fetch(e.request).catch(() =>
        caches.match(OFFLINE_URL).then((r) => r ?? Response.error())
      )
    );
  }
});
