/**
 * Service worker for explicitly public, same-origin resources.
 *
 * The route serving this file replaces the configuration placeholders below.
 */

const CACHE_VERSION = __NABICAT_CACHE_VERSION__;
const CACHE_PREFIX = __NABICAT_CACHE_PREFIX__;
const CACHE_NAME = `${CACHE_PREFIX}${CACHE_VERSION}`;
const VERSIONED_STATIC_PATH_PREFIXES = __NABICAT_STATIC_PATH_PREFIXES__;
const PUBLIC_MEDIA_PATH_PREFIXES = __NABICAT_PUBLIC_MEDIA_PATH_PREFIXES__;

function isVersionedStaticAsset(url) {
    return VERSIONED_STATIC_PATH_PREFIXES.some(
        (prefix) => url.pathname.startsWith(prefix)
    )
        && Boolean(url.searchParams.get('v'));
}

function isIntentionallyPublicMedia(url) {
    return PUBLIC_MEDIA_PATH_PREFIXES.some((prefix) => {
        if (!url.pathname.startsWith(prefix)) return false;
        return /^\d+$/.test(url.pathname.slice(prefix.length));
    });
}

function isCacheableResponse(response, requirePublic) {
    if (!response || !response.ok || response.status !== 200) {
        return false;
    }

    const cacheControl = (response.headers.get('cache-control') || '').toLowerCase();
    if (cacheControl.includes('private') || cacheControl.includes('no-store')) {
        return false;
    }
    if (response.headers.has('set-cookie')) {
        return false;
    }
    return !requirePublic || cacheControl.includes('public');
}

async function clearCaches({ keepCurrent = false } = {}) {
    const names = await caches.keys();
    await Promise.all(
        names
            .filter((name) => (
                name.startsWith(CACHE_PREFIX)
                && (!keepCurrent || name !== CACHE_NAME)
            ))
            .map((name) => caches.delete(name))
    );
}

self.addEventListener('install', (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        clearCaches({ keepCurrent: true }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    if (
        url.origin !== self.location.origin
        || request.method !== 'GET'
        || request.headers.has('Range')
    ) {
        return;
    }

    if (isVersionedStaticAsset(url)) {
        event.respondWith(networkFirst(request, false));
        return;
    }

    if (isIntentionallyPublicMedia(url)) {
        event.respondWith(cacheWithUpdate(request, true, event));
    }
});

async function networkFirst(request, requirePublic) {
    const cache = await caches.open(CACHE_NAME);
    try {
        const response = await fetch(request);
        if (isCacheableResponse(response, requirePublic)) {
            await cache.put(request, response.clone());
        } else {
            await cache.delete(request);
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

async function cacheWithUpdate(request, requirePublic, event) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    const networkResponse = fetch(request).then(async (response) => {
        if (isCacheableResponse(response, requirePublic)) {
            await cache.put(request, response.clone());
        } else {
            await cache.delete(request);
        }
        return response;
    });

    if (cached) {
        event.waitUntil(networkResponse.catch(() => undefined));
        return cached;
    }
    return networkResponse;
}

self.addEventListener('message', (event) => {
    const data = event.data || {};
    const port = event.ports[0];

    if (data.action === 'clearCache') {
        event.waitUntil(
            clearCaches().then(() => port?.postMessage({ success: true }))
        );
        return;
    }

    if (data.action === 'removeFromCache') {
        event.waitUntil(
            caches.open(CACHE_NAME)
                .then((cache) => cache.delete(data.url))
                .then(() => port?.postMessage({ success: true }))
        );
        return;
    }

    if (data.action === 'getCacheSize') {
        event.waitUntil(
            getCacheSize().then((result) => port?.postMessage(result))
        );
        return;
    }

    port?.postMessage({ error: 'Unknown action' });
});

async function getCacheSize() {
    const cache = await caches.open(CACHE_NAME);
    const requests = await cache.keys();
    let usage = 0;
    for (const request of requests) {
        const response = await cache.match(request);
        if (response) {
            usage += (await response.clone().arrayBuffer()).byteLength;
        }
    }
    const estimate = await navigator.storage.estimate();
    return { usage, quota: estimate.quota || 0 };
}
