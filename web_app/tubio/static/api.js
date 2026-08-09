(() => {
    'use strict';

    const Tubio = window.Tubio = window.Tubio || {};

    class ApiError extends Error {
        constructor(message, status, payload) {
            super(message);
            this.name = 'ApiError';
            this.status = status;
            this.payload = payload;
        }
    }

    function csrfToken() {
        return document.querySelector('input[name="csrf_token"]')?.value || '';
    }

    function formData(fields = {}) {
        const body = new FormData();
        Object.entries(fields).forEach(([key, value]) => {
            if (value !== undefined && value !== null) body.append(key, value);
        });
        const token = csrfToken();
        if (token) body.append('csrf_token', token);
        return body;
    }

    async function parseJson(response) {
        try {
            return await response.json();
        } catch (_error) {
            return {};
        }
    }

    async function request(url, options = {}) {
        const { headers = {}, ...requestOptions } = options;
        const response = await fetch(url, {
            credentials: 'same-origin',
            ...requestOptions,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                ...headers,
            },
        });
        const payload = await parseJson(response);
        if (!response.ok) {
            throw new ApiError(
                payload.error || `Request failed (${response.status})`,
                response.status,
                payload,
            );
        }
        return payload;
    }

    function get(url) {
        return request(url, { method: 'GET' });
    }

    function post(url, fields = {}) {
        return request(url, { method: 'POST', body: formData(fields) });
    }

    async function reportError(scope, error, context = {}) {
        try {
            await post('/tubio/client-log', {
                scope,
                message: error?.message || String(error),
                stack: error?.stack || '',
                context: JSON.stringify(context),
            });
        } catch (reportingError) {
            console.error('Could not report Tubio client error:', reportingError);
        }
    }

    Tubio.api = { ApiError, get, post, reportError };
})();
