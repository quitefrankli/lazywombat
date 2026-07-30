/**
 * Service Worker for client-side caching
 * Handles Cache API for heavy downloads and resources
 */

const CACHE_VERSION = 'v1';
const CACHE_NAME = `nabicat-cache-${CACHE_VERSION}`;
const MAX_CACHE_SIZE = 10 * 1024 * 1024 * 1024; // 10GB limit

// URLs that should be cached
const CACHE_STRATEGIES = {
    // App static assets must be revalidated on every visit so a deployment is
    // visible on the first refresh. The cache remains an offline fallback.
    networkFirst: /\/(api|account|static)\//,

    // Cache-first for truly immutable assets (fonts, etc.)
    cacheFirst: /\/(fonts)\//,

    // Heavy media can be served immediately while its cached copy is updated.
    cacheWithUpdate: /\/(download|thumbnail|audio)\//,
};

self.addEventListener('install', (event) => {
    console.log('[SW] Installing service worker...');
    self.skipWaiting(); // Activate immediately
});

self.addEventListener('activate', (event) => {
    console.log('[SW] Activating service worker...');
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((name) => name.startsWith('nabicat-cache-') && name !== CACHE_NAME)
                    .map((name) => caches.delete(name))
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    // Only handle same-origin GET requests (Cache API doesn't support POST)
    if (url.origin !== location.origin || request.method !== 'GET') {
        return;
    }

    // File Store downloads are private and may be too large for Cache Storage.
    if (url.pathname.startsWith('/file_store/download/')) {
        return;
    }

    // Skip SSE endpoints (text/event-stream)
    if (url.pathname.includes('/download_progress/')) {
        return;
    }

    // Skip audio requests with Range headers (206 partial responses can't be cached)
    if (url.pathname.includes('/audio/') && request.headers.has('Range')) {
        return;
    }

    // Determine strategy
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

        case 'networkFirst':
        default:
            return networkFirst(request, cache);
    }
}

// Check and enforce cache size limit
async function enforceCacheSizeLimit(cache) {
    const estimate = await navigator.storage.estimate();
    const currentUsage = estimate.usage || 0;

    if (currentUsage <= MAX_CACHE_SIZE) {
        return; // Under limit
    }

    console.log(`[SW] Cache size ${currentUsage} exceeds limit ${MAX_CACHE_SIZE}, evicting oldest entries`);

    // Get all cached requests and sort by date
    const requests = await cache.keys();
    const entries = await Promise.all(
        requests.map(async (request) => {
            const response = await cache.match(request);
            const date = response.headers.get('date');
            return { request, date: date ? new Date(date) : new Date(0) };
        })
    );

    // Sort by date (oldest first)
    entries.sort((a, b) => a.date - b.date);

    // Delete oldest 10% of entries
    const toDelete = Math.ceil(entries.length * 0.1);
    for (let i = 0; i < toDelete; i++) {
        await cache.delete(entries[i].request);
    }
}

// Cache-first: Check cache, fallback to network
async function cacheFirst(request, cache) {
    const cached = await cache.match(request);
    if (cached) {
        return cached;
    }

    try {
        const response = await fetch(request);
        // Only cache full 200 responses (not 206 partial)
        if (response.ok && response.status === 200) {
            await enforceCacheSizeLimit(cache);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        console.error('[SW] Network fetch failed:', error);
        throw error;
    }
}

// Network-first: Try network, fallback to cache
async function networkFirst(request, cache) {
    try {
        const response = await fetch(request);
        // Only cache full 200 responses (not 206 partial)
        if (response.ok && response.status === 200) {
            await enforceCacheSizeLimit(cache);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await cache.match(request);
        if (cached) {
            return cached;
        }
        throw error;
    }
}

// Cache with update: Serve from cache, update in background
async function cacheWithUpdate(request, cache) {
    const cached = await cache.match(request);

    // Fetch and update cache in background
    const fetchPromise = fetch(request).then(async (response) => {
        // Only cache full 200 responses (not 206 partial)
        if (response.ok && response.status === 200) {
            await enforceCacheSizeLimit(cache);
            cache.put(request, response.clone());
        }
        return response;
    }).catch((error) => {
        console.error('[SW] Background fetch failed:', error);
    });

    // Return cached immediately if available, otherwise wait for network
    if (cached) {
        return cached;
    }

    return fetchPromise;
}

// Listen for cache control messages from clients
self.addEventListener('message', async (event) => {
    const { action, url } = event.data;

    switch (action) {
        case 'clearCache':
            await caches.delete(CACHE_NAME);
            event.ports[0].postMessage({ success: true });
            break;

        case 'removeFromCache':
            const cache = await caches.open(CACHE_NAME);
            await cache.delete(url);
            event.ports[0].postMessage({ success: true });
            break;

        case 'getCacheSize':
            const cacheForSize = await caches.open(CACHE_NAME);
            const cacheKeys = await cacheForSize.keys();
            let totalSize = 0;
            for (const req of cacheKeys) {
                const res = await cacheForSize.match(req);
                if (res) {
                    const buf = await res.clone().arrayBuffer();
                    totalSize += buf.byteLength;
                }
            }
            const storageEstimate = await navigator.storage.estimate();
            event.ports[0].postMessage({
                usage: totalSize,
                quota: storageEstimate.quota,
            });
            break;

        default:
            event.ports[0].postMessage({ error: 'Unknown action' });
    }
});
