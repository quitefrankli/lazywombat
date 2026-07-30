/**
 * Client-side Cache Manager
 * Provides clean API for cache operations across all subpages
 */

class CacheManager {
    constructor() {
        this.swRegistration = null;
        this.isSupported = 'serviceWorker' in navigator && 'caches' in window;
        const config = window.NABICAT_CACHE_CONFIG || {};
        this.MAX_CACHE_SIZE = config.maxCacheSize || 0;
        this.readyTimeoutMs = config.serviceWorkerReadyTimeoutMs || 5000;
        this.messageTimeoutMs = config.serviceWorkerMessageTimeoutMs || 5000;
    }

    /**
     * Initialize service worker and caching
     */
    async init() {
        if (!this.isSupported) {
            console.warn('[Cache] Service Worker not supported');
            return false;
        }

        try {
            this.swRegistration = await navigator.serviceWorker.register('/service-worker.js', {
                scope: '/',
                updateViaCache: 'none',
            });

            console.log('[Cache] Service Worker registered');

            await this.waitForReady();
            return this.isAvailable();
        } catch (error) {
            console.error('[Cache] Service Worker registration failed:', error);
            return false;
        }
    }

    async waitForReady() {
        if (navigator.serviceWorker.controller || this.swRegistration.active) {
            return true;
        }

        return new Promise((resolve) => {
            let settled = false;

            const finish = () => {
                if (settled) return;
                settled = true;
                navigator.serviceWorker.removeEventListener('controllerchange', finish);
                console.log('[Cache] Service Worker ready');
                resolve(true);
            };

            const timeout = window.setTimeout(finish, this.readyTimeoutMs);
            navigator.serviceWorker.ready.then((registration) => {
                this.swRegistration = registration;
                window.clearTimeout(timeout);
                finish();
            }).catch(() => finish());
            navigator.serviceWorker.addEventListener('controllerchange', finish);
        });
    }

    /**
     * Check if caching is available
     */
    isAvailable() {
        return this.isSupported && this.swRegistration !== null && (
            navigator.serviceWorker.controller || this.swRegistration.active
        );
    }

    /**
     * Start a browser-managed download without buffering the response in memory.
     * @param {string} url - URL to download
     * @param {string} filename - Filename for saving
     * @returns {Promise<void>}
     */
    async downloadWithCache(url, filename) {
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    /**
     * Clear entire cache
     */
    async clearCache() {
        if (!this.isAvailable()) return;

        const channel = new MessageChannel();
        const response = await this.sendMessage({ action: 'clearCache' }, channel);
        console.log('[Cache] Cache cleared');
        return response;
    }

    /**
     * Remove specific URL from cache
     */
    async removeFromCache(url) {
        if (!this.isAvailable()) return;

        const channel = new MessageChannel();
        return this.sendMessage({ action: 'removeFromCache', url }, channel);
    }

    /**
     * Get cache size information
     */
    async getCacheInfo() {
        if (!this.isAvailable()) {
            return { usage: 0, quota: 0, available: 0, maxSize: this.MAX_CACHE_SIZE };
        }

        const channel = new MessageChannel();
        const response = await this.sendMessage({ action: 'getCacheSize' }, channel);

        return {
            usage: response.usage || 0,
            quota: response.quota || 0,
            available: (response.quota || 0) - (response.usage || 0),
            maxSize: this.MAX_CACHE_SIZE,
        };
    }

    /**
     * Send message to service worker
     */
    sendMessage(message, channel) {
        return new Promise((resolve, reject) => {
            channel.port1.onmessage = (event) => {
                window.clearTimeout(timeout);
                if (event.data.error) {
                    reject(new Error(event.data.error));
                } else {
                    resolve(event.data);
                }
            };

            const worker = navigator.serviceWorker.controller || this.swRegistration?.active;
            const timeout = window.setTimeout(() => {
                reject(new Error('Service worker did not respond'));
            }, this.messageTimeoutMs);

            if (worker) {
                worker.postMessage(message, [channel.port2]);
            } else {
                window.clearTimeout(timeout);
                reject(new Error('No service worker controller'));
            }
        });
    }

    /**
     * Format bytes to human readable
     */
    static formatSize(bytes) {
        const units = ['B', 'KB', 'MB', 'GB'];
        let i = 0;
        while (bytes >= 1024 && i < units.length - 1) {
            bytes /= 1024;
            i++;
        }
        return bytes.toFixed(1) + ' ' + units[i];
    }
}

// Global instance
window.cacheManager = new CacheManager();

// Auto-initialize on page load
function _initCacheManager() {
    window.cacheManager.init().then(() => {
        window.dispatchEvent(new Event('cacheManagerReady'));
    });

    if (window.cacheManager.isSupported) {
        navigator.serviceWorker.ready.then((registration) => {
            window.cacheManager.swRegistration = registration;
            window.dispatchEvent(new Event('cacheManagerReady'));
        });
        navigator.serviceWorker.addEventListener('controllerchange', () => {
            window.dispatchEvent(new Event('cacheManagerReady'));
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initCacheManager);
} else {
    _initCacheManager();
}
