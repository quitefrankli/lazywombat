/**
 * Service worker for client-side caching.
 * Handles Cache API for heavy downloads and static resources.
 */

const CACHE_VERSION = 'v2';
const CACHE_NAME = `nabicat-cache-${CACHE_VERSION}`;
const CACHE_PREFIX = 'nabicat-cache-';
const MAX_CACHE_SIZE = 10 * 1024 * 1024 * 1024;
const EVICTION_TARGET_RATIO = 0.9;
const CACHE_TIMESTAMP_HEADER = 'x-nabicat-cached-at';

const CACHE_STRATEGIES = {
  networkFirst: /\/(api|account)\//,
  cacheFirst: /\/(fonts)\//,
  cacheWithUpdate: /\/(static|download|thumbnail|audio)\//,
};

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => Promise.all(
        cacheNames
          .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
          .map((name) => caches.delete(name)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (url.origin !== self.location.origin || request.method !== 'GET') return;
  if (url.pathname.startsWith('/file_store/download/')) return;
  if (url.pathname.includes('/download_progress/')) return;
  if (url.pathname.includes('/audio/') && request.headers.has('Range')) return;

  let strategy = 'networkFirst';
  if (CACHE_STRATEGIES.cacheFirst.test(url.pathname)) {
    strategy = 'cacheFirst';
  } else if (CACHE_STRATEGIES.cacheWithUpdate.test(url.pathname)) {
    strategy = 'cacheWithUpdate';
  }

  event.respondWith(handleFetch(request, strategy));
});

async function handleFetch(request, strategy) {
  const cache = await caches.open(CACHE_NAME);

  switch (strategy) {
    case 'cacheFirst':
      return cacheFirst(request, cache);
    case 'cacheWithUpdate':
      return cacheWithUpdate(request, cache);
    default:
      return networkFirst(request, cache);
  }
}

function canCache(response) {
  return response && response.ok && response.status === 200 && response.type !== 'opaque';
}

async function responseSize(response) {
  const contentLength = Number(response.headers.get('content-length'));
  if (Number.isFinite(contentLength) && contentLength >= 0) return contentLength;
  return (await response.clone().arrayBuffer()).byteLength;
}

async function cacheEntries(cache) {
  const requests = await cache.keys();
  return Promise.all(requests.map(async (request) => {
    const response = await cache.match(request);
    if (!response) return null;

    const cachedAt = Number(response.headers.get(CACHE_TIMESTAMP_HEADER)) || 0;
    return {
      request,
      size: await responseSize(response),
      cachedAt,
    };
  })).then((entries) => entries.filter(Boolean));
}

async function enforceCacheSizeLimit(cache, incomingSize = 0) {
  const entries = await cacheEntries(cache);
  let usage = entries.reduce((total, entry) => total + entry.size, 0);

  if (usage + incomingSize <= MAX_CACHE_SIZE) return;

  const targetSize = Math.floor(MAX_CACHE_SIZE * EVICTION_TARGET_RATIO);
  entries.sort((a, b) => a.cachedAt - b.cachedAt);

  for (const entry of entries) {
    if (usage + incomingSize <= targetSize) break;
    if (await cache.delete(entry.request)) usage -= entry.size;
  }
}

async function stampResponse(response) {
  const body = await response.clone().blob();
  const headers = new Headers(response.headers);
  headers.set(CACHE_TIMESTAMP_HEADER, String(Date.now()));

  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function putInCache(cache, request, response) {
  if (!canCache(response)) return;

  const stamped = await stampResponse(response);
  const size = await responseSize(stamped);
  await enforceCacheSizeLimit(cache, size);
  await cache.put(request, stamped);
}

async function cacheFirst(request, cache) {
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  await putInCache(cache, request, response.clone());
  return response;
}

async function networkFirst(request, cache) {
  try {
    const response = await fetch(request);
    await putInCache(cache, request, response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw error;
  }
}

async function cacheWithUpdate(request, cache) {
  const cached = await cache.match(request);
  const networkUpdate = fetch(request)
    .then(async (response) => {
      await putInCache(cache, request, response.clone());
      return response;
    })
    .catch((error) => {
      console.error('[SW] Background fetch failed:', error);
      if (!cached) throw error;
      return null;
    });

  return cached || networkUpdate;
}

function reply(event, payload) {
  if (event.ports && event.ports[0]) event.ports[0].postMessage(payload);
}

self.addEventListener('message', (event) => {
  event.waitUntil(handleMessage(event));
});

async function handleMessage(event) {
  const { action, url } = event.data || {};

  try {
    switch (action) {
      case 'clearCache':
        await caches.delete(CACHE_NAME);
        reply(event, { success: true });
        return;

      case 'removeFromCache': {
        const cache = await caches.open(CACHE_NAME);
        const removed = await cache.delete(url);
        reply(event, { success: true, removed });
        return;
      }

      case 'getCacheSize': {
        const cache = await caches.open(CACHE_NAME);
        const entries = await cacheEntries(cache);
        const storageEstimate = await navigator.storage.estimate();
        reply(event, {
          usage: entries.reduce((total, entry) => total + entry.size, 0),
          quota: storageEstimate.quota || null,
        });
        return;
      }

      default:
        reply(event, { error: 'Unknown action' });
    }
  } catch (error) {
    console.error('[SW] Message handling failed:', error);
    reply(event, { error: error.message || 'Service worker operation failed' });
  }
}
