/* 단어 도감 서비스 워커
   - 앱 껍데기(HTML/JS/아이콘)는 설치할 때 미리 저장합니다.
   - 음원은 "쓴 것만" 저장합니다. 처음부터 1,800개를 통째로 받지 않습니다.
   - 캐시를 바꿀 때는 VERSION 숫자만 올리면 이전 캐시가 정리됩니다. */

const VERSION = 'v5.0.0';
const SHELL = 'shell-' + VERSION;
const MEDIA = 'media-' + VERSION;

const SHELL_FILES = [
  './',
  './index.html',
  './words.js',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/apple-touch-icon.png'
];

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL);
    // 하나가 실패해도 나머지는 저장되도록 개별 처리합니다
    await Promise.all(SHELL_FILES.map(f => c.add(f).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== SHELL && k !== MEDIA).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

const isAudio = url =>
  /\.mp3(\?|$)/i.test(url.pathname) || /dictionaryapi\.dev\/media/i.test(url.href);

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;

  let url;
  try { url = new URL(req.url); } catch (_) { return; }

  // 1) 음원: 캐시에 있으면 바로, 없으면 받아서 저장 (한 번 들은 단어는 오프라인에서도 재생)
  if (isAudio(url)) {
    e.respondWith((async () => {
      const c = await caches.open(MEDIA);
      const hit = await c.match(req);
      if (hit) return hit;
      try {
        const res = await fetch(req);
        if (res && (res.ok || res.type === 'opaque')) c.put(req, res.clone());
        return res;
      } catch (_) {
        return new Response('', { status: 504 });
      }
    })());
    return;
  }

  // 2) 사전 API 조회: 인터넷 우선, 실패하면 이전에 받은 결과 사용
  if (/dictionaryapi\.dev/i.test(url.href)) {
    e.respondWith((async () => {
      const c = await caches.open(MEDIA);
      try {
        const res = await fetch(req);
        if (res && res.ok) c.put(req, res.clone());
        return res;
      } catch (_) {
        return (await c.match(req)) || new Response('[]', {
          headers: { 'Content-Type': 'application/json' }
        });
      }
    })());
    return;
  }

  // 3) 그 외(앱 파일, 글꼴): 캐시 우선, 없으면 받아서 저장
  if (url.origin === location.origin ||
      /fonts\.(googleapis|gstatic)\.com/i.test(url.hostname)) {
    e.respondWith((async () => {
      const cached = await caches.match(req);
      if (cached) return cached;
      try {
        const res = await fetch(req);
        if (res && res.ok) {
          const c = await caches.open(SHELL);
          c.put(req, res.clone());
        }
        return res;
      } catch (_) {
        // 인터넷이 없고 캐시도 없을 때: 화면 요청이면 앱 첫 화면을 돌려줍니다
        if (req.mode === 'navigate') {
          return (await caches.match('./index.html')) ||
                 new Response('오프라인입니다', { status: 503 });
        }
        return new Response('', { status: 504 });
      }
    })());
  }
});

// 앱에서 "음원 미리 저장"을 요청할 때 사용합니다
self.addEventListener('message', e => {
  const data = e.data || {};
  if (data.type === 'CACHE_AUDIO' && Array.isArray(data.urls)) {
    e.waitUntil((async () => {
      const c = await caches.open(MEDIA);
      let ok = 0;
      for (const u of data.urls) {
        try { await c.add(u); ok++; } catch (_) {}
      }
      const clients = await self.clients.matchAll();
      clients.forEach(cl => cl.postMessage({ type: 'CACHE_AUDIO_DONE', ok, total: data.urls.length }));
    })());
  }
  if (data.type === 'CLEAR_MEDIA') {
    e.waitUntil(caches.delete(MEDIA));
  }
});
