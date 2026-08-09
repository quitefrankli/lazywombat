(() => {
    'use strict';

    const Tubio = window.Tubio = window.Tubio || {};
    const api = () => Tubio.api;
    const player = () => Tubio.player;
    let suggestionSequence = 0;
    let suggestionTimer = null;

    function notify(message, type = 'info') {
        const notification = document.createElement('div');
        const alertType = type === 'error' ? 'danger' : type;
        notification.className = [
            'alert',
            `alert-${alertType}`,
            'alert-dismissible',
            'fade',
            'show',
            'position-fixed',
            'tubio-notification',
        ].join(' ');
        notification.setAttribute('role', 'status');
        notification.append(document.createTextNode(message || ''));
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close';
        close.dataset.bsDismiss = 'alert';
        close.setAttribute('aria-label', 'Close');
        notification.append(close);
        document.body.append(notification);
        window.setTimeout(() => notification.remove(), 5000);
    }

    function decorate(root = document) {
        root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => {
            if (window.bootstrap?.Tooltip && !bootstrap.Tooltip.getInstance(element)) {
                new bootstrap.Tooltip(element);
            }
        });
        root.querySelectorAll('.accordion-collapse').forEach(collapse => {
            if (collapse._tubioThumbnailBound) return;
            collapse._tubioThumbnailBound = true;
            collapse.addEventListener('show.bs.collapse', () => {
                const image = collapse.querySelector('.lazy-thumbnail[data-src]');
                if (image && !image.src) image.src = image.dataset.src;
            });
        });
    }

    function switchTab(tabName, { initializeDiscover = true } = {}) {
        document.querySelectorAll(
            '#search-nav-tab, #playlists-nav-tab, #discover-nav-tab'
        ).forEach(tab => {
            const active = tab.id === `${tabName}-nav-tab`;
            tab.classList.toggle('active', active);
            tab.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        document.querySelectorAll('#tubio-tab-content > .tab-pane').forEach(pane => {
            pane.classList.remove('show', 'active');
        });
        document.getElementById(tabName)?.classList.add('show', 'active');
        document.querySelectorAll('.tubio-playlists-action').forEach(item => {
            item.classList.toggle('d-none', tabName !== 'playlists');
        });
        document.querySelectorAll('.tubio-discover-action').forEach(item => {
            item.classList.toggle('d-none', tabName !== 'discover');
        });
        const actions = document.querySelector('.actions-dropdown')?.closest('.dropdown');
        actions?.classList.toggle(
            'd-none',
            tabName !== 'playlists' && tabName !== 'discover',
        );
        document.getElementById('tubio-trackbar')?.classList.toggle(
            'd-none',
            tabName === 'search',
        );
        if (tabName === 'discover' && initializeDiscover) {
            player()?.handleAction('initialize-discover');
        }
        window.location.hash = `#${tabName}`;
    }

    function isMobile() {
        return window.matchMedia('(max-width: 767.98px)').matches;
    }

    function sidebarPreferenceKeys() {
        const container = document.getElementById('tubio-tab-content');
        return {
            collapsed: container?.dataset.sidebarCollapsedStorageKey || '',
            selected: container?.dataset.sidebarSelectedStorageKey || '',
        };
    }

    function readSidebarPreference(key) {
        if (!key) return null;
        try {
            return sessionStorage.getItem(key);
        } catch (_error) {
            return null;
        }
    }

    function writeSidebarPreference(key, value) {
        if (!key) return;
        try {
            sessionStorage.setItem(key, value);
        } catch (_error) {}
    }

    function setSidebarCollapsed(collapsed, { persist = true } = {}) {
        const layout = document.querySelector('.tubio-layout');
        if (!layout) return;
        layout.classList.toggle('sidebar-collapsed', collapsed);
        document.body.classList.toggle(
            'tubio-sidebar-open',
            !collapsed && isMobile(),
        );
        if (persist) {
            writeSidebarPreference(
                sidebarPreferenceKeys().collapsed,
                collapsed ? '1' : '0',
            );
        }
    }

    function selectPlaylist(
        slug,
        { persist = true, collapseOnMobile = true } = {},
    ) {
        document.querySelectorAll('.playlist-panel').forEach(panel => {
            panel.classList.remove('active');
        });
        document.querySelectorAll('.sidebar-item').forEach(item => {
            item.classList.remove('active');
        });
        const panel = document.getElementById(`panel-${slug}`);
        const item = Array.from(document.querySelectorAll(
            '.sidebar-item[data-playlist-slug]'
        )).find(candidate => candidate.dataset.playlistSlug === slug);
        panel?.classList.add('active');
        item?.classList.add('active');
        document.getElementById('panel-empty')?.classList.toggle('active', !panel);
        if (panel && persist) {
            writeSidebarPreference(sidebarPreferenceKeys().selected, slug);
        }
        if (collapseOnMobile && isMobile()) setSidebarCollapsed(true);
    }

    function initializeSidebar(
        preferredSlug = null,
        { collapsed = null } = {},
    ) {
        const layout = document.querySelector('.tubio-layout');
        if (!layout) return;
        const keys = sidebarPreferenceKeys();
        const savedCollapsed = readSidebarPreference(keys.collapsed);
        const shouldCollapse = collapsed === null
            ? savedCollapsed === null ? isMobile() : savedCollapsed === '1'
            : collapsed;
        setSidebarCollapsed(shouldCollapse, { persist: false });

        const available = Array.from(document.querySelectorAll(
            '.sidebar-item[data-playlist-slug]'
        )).map(item => item.dataset.playlistSlug);
        const savedSelected = readSidebarPreference(keys.selected);
        const selected = [preferredSlug, savedSelected, available[0]].find(
            slug => slug && available.includes(slug)
        );
        if (selected) {
            selectPlaylist(selected, { collapseOnMobile: false });
        }
    }

    function replaceLibrary(html) {
        if (typeof html !== 'string') return;
        const container = document.getElementById('playlists');
        if (!container) return;
        const selected = document.querySelector(
            '.sidebar-item.active[data-playlist-slug]'
        )?.dataset.playlistSlug || null;
        const wasCollapsed = document.querySelector('.tubio-layout')
            ?.classList.contains('sidebar-collapsed');
        container.innerHTML = html;
        decorate(container);
        initializeSidebar(selected, { collapsed: Boolean(wasCollapsed) });
        player()?.reconcileDom();
    }

    function renderSearchError(message) {
        const container = document.getElementById('search-results');
        if (!container) return;
        container.replaceChildren();
        const wrapper = document.createElement('div');
        wrapper.className = 'text-center py-5';
        const heading = document.createElement('h2');
        heading.className = 'h5 text-danger';
        heading.textContent = message;
        wrapper.append(heading);
        container.append(wrapper);
    }

    async function runSearch(page = 0, { scroll = false } = {}) {
        const input = document.getElementById('youtube-query');
        const results = document.getElementById('search-results');
        if (!input?.value.trim() || !results) return;
        try {
            const payload = await api().post('/tubio/search', {
                youtube_query: input.value.trim(),
                page: String(page),
            });
            results.innerHTML = payload.results_html;
            decorate(results);
            if (scroll) results.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch (error) {
            renderSearchError(error.message || 'Search failed. Please try again.');
        }
    }

    function suggestionsList() {
        return document.getElementById('search-suggestions');
    }

    function hideSuggestions() {
        const list = suggestionsList();
        if (list) {
            list.replaceChildren();
            list.hidden = true;
        }
        document.getElementById('youtube-query')?.setAttribute(
            'aria-expanded',
            'false',
        );
    }

    function acceptSuggestion(value) {
        const input = document.getElementById('youtube-query');
        if (!input) return;
        input.value = value;
        hideSuggestions();
        runSearch();
    }

    function renderSuggestions(suggestions) {
        const list = suggestionsList();
        if (!list || !suggestions.length) {
            hideSuggestions();
            return;
        }
        list.replaceChildren(...suggestions.map((suggestion, index) => {
            const item = document.createElement('li');
            item.className = 'tubio-suggestion';
            item.setAttribute('role', 'option');
            item.dataset.index = String(index);
            item.dataset.value = suggestion;
            const icon = document.createElement('i');
            icon.className = 'bi bi-search';
            const text = document.createElement('span');
            text.textContent = suggestion;
            item.append(icon, text);
            return item;
        }));
        list.hidden = false;
        document.getElementById('youtube-query')?.setAttribute(
            'aria-expanded',
            'true',
        );
    }

    function moveSuggestion(delta) {
        const list = suggestionsList();
        if (!list || list.hidden) return;
        const items = Array.from(list.querySelectorAll('.tubio-suggestion'));
        if (!items.length) return;
        const current = items.findIndex(item => item.classList.contains('active'));
        const next = (current + delta + items.length) % items.length;
        items.forEach(item => item.classList.remove('active'));
        items[next].classList.add('active');
        items[next].scrollIntoView({ block: 'nearest' });
    }

    async function fetchSuggestions(query) {
        const sequence = ++suggestionSequence;
        try {
            const payload = await api().post('/tubio/suggest', {
                youtube_query: query,
            });
            if (sequence === suggestionSequence) {
                renderSuggestions(payload.suggestions || []);
            }
        } catch (_error) {
            if (sequence === suggestionSequence) hideSuggestions();
        }
    }

    function initializeSearch() {
        const form = document.getElementById('search-form');
        const input = document.getElementById('youtube-query');
        const list = suggestionsList();
        form?.addEventListener('submit', event => {
            event.preventDefault();
            hideSuggestions();
            runSearch();
        });
        if (!input || !list) return;
        const debounce = Number.parseInt(input.dataset.debounceMs, 10);
        const minimum = Number.parseInt(input.dataset.minLen, 10);
        input.addEventListener('input', () => {
            window.clearTimeout(suggestionTimer);
            const query = input.value.trim();
            if (query.length < minimum) {
                hideSuggestions();
                return;
            }
            suggestionTimer = window.setTimeout(
                () => fetchSuggestions(query),
                debounce,
            );
        });
        input.addEventListener('keydown', event => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                moveSuggestion(event.key === 'ArrowDown' ? 1 : -1);
            } else if (event.key === 'Enter') {
                const active = list.querySelector('.tubio-suggestion.active');
                if (active) {
                    event.preventDefault();
                    acceptSuggestion(active.dataset.value);
                }
            } else if (event.key === 'Escape') {
                hideSuggestions();
            }
        });
        list.addEventListener('mousedown', event => {
            const item = event.target.closest('.tubio-suggestion');
            if (!item) return;
            event.preventDefault();
            acceptSuggestion(item.dataset.value);
        });
        input.addEventListener('blur', () => window.setTimeout(hideSuggestions, 150));
    }

    async function downloadVideo(button) {
        const videoId = button.dataset.videoId;
        const original = button.innerHTML;
        const container = button.closest('.search-result-download');
        const progress = container?.querySelector('.progress');
        const bar = progress?.querySelector('.progress-bar');
        const status = container?.querySelector('small');
        button.disabled = true;
        button.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Starting…';
        progress?.classList.remove('d-none');
        status?.classList.remove('d-none');
        let events = null;
        try {
            events = new EventSource(
                `/tubio/download_progress/${encodeURIComponent(videoId)}`
            );
            events.onmessage = event => {
                const update = JSON.parse(event.data);
                if (typeof update.percent === 'number' && bar) {
                    bar.style.width = `${update.percent}%`;
                    bar.textContent = `${Math.round(update.percent)}%`;
                    bar.setAttribute('aria-valuenow', String(update.percent));
                }
                if (status) status.textContent = update.status || '';
                if (['complete', 'error', 'not_found'].includes(update.status)) {
                    events.close();
                }
            };
            const payload = await api().post('/tubio/youtube_download', {
                video_id: videoId,
                title: button.dataset.title,
            });
            replaceLibrary(payload.library_html);
            button.innerHTML = '<i class="bi bi-check-circle me-1"></i>Converted';
            button.classList.add('search-result-cached');
            notify(payload.message, 'success');
        } catch (error) {
            button.disabled = false;
            button.innerHTML = original;
            notify(error.message, 'error');
        } finally {
            events?.close();
        }
    }

    function openTrim(track) {
        document.getElementById('trim-audio-crc').value = track.dataset.audioCrc;
        document.getElementById('trim-audio-title').textContent = track.dataset.title;
        document.getElementById('trim-start-seconds').value = track.dataset.trimStart || '0';
        document.getElementById('trim-end-seconds').value = track.dataset.trimEnd || '0';
        document.getElementById('trim-audio-error').classList.add('d-none');
        bootstrap.Modal.getOrCreateInstance(
            document.getElementById('trimAudioModal')
        ).show();
    }

    async function submitTrim(event) {
        event.preventDefault();
        const crc = document.getElementById('trim-audio-crc').value;
        const start = Number(document.getElementById('trim-start-seconds').value);
        const end = Number(document.getElementById('trim-end-seconds').value);
        const errorElement = document.getElementById('trim-audio-error');
        const submit = document.getElementById('trim-audio-submit');
        const original = submit.innerHTML;
        submit.disabled = true;
        submit.textContent = 'Saving…';
        errorElement.classList.add('d-none');
        try {
            const payload = await api().post(`/tubio/audio/${crc}/trim`, {
                trim_start_s: String(start),
                trim_end_s: String(end),
            });
            bootstrap.Modal.getInstance(
                document.getElementById('trimAudioModal')
            )?.hide();
            replaceLibrary(payload.library_html);
            notify(payload.message, 'success');
        } catch (error) {
            errorElement.textContent = error.message;
            errorElement.classList.remove('d-none');
        } finally {
            submit.disabled = false;
            submit.innerHTML = original;
        }
    }

    async function resyncTrack(track, button) {
        const original = button.innerHTML;
        button.disabled = true;
        button.textContent = 'Syncing…';
        try {
            const payload = await api().post(`/tubio/resync/${track.dataset.audioCrc}`);
            replaceLibrary(payload.library_html);
            notify(payload.message, 'success');
        } catch (error) {
            button.disabled = false;
            button.innerHTML = original;
            notify(error.message, 'error');
        }
    }

    async function removeTrack(track, button) {
        if (!window.confirm('Delete this track from your library?')) return;
        const original = button.innerHTML;
        button.disabled = true;
        button.textContent = 'Removing…';
        try {
            const payload = await api().post(
                `/tubio/delete_audio/${track.dataset.audioCrc}`
            );
            player()?.handleAction('forget-track', track);
            replaceLibrary(payload.library_html);
            notify('Track removed', 'success');
        } catch (error) {
            button.disabled = false;
            button.innerHTML = original;
            notify(error.message, 'error');
        }
    }

    function preparePlaylist() {
        const selected = Array.from(document.querySelectorAll(
            '.song-checkbox:checked'
        )).map(input => input.value);
        const input = document.getElementById('move_playlist_tracks_crcs');
        if (input) input.value = selected.join(',');
        const submit = document.querySelector(
            '#move-playlist-form button[type="submit"]'
        );
        if (submit) submit.disabled = selected.length === 0;
    }

    function handlePageAction(action, element) {
        const track = element.closest('.playlist-track');
        switch (action) {
            case 'switch-tab':
                switchTab(element.dataset.tab); return true;
            case 'prepare-playlist':
                preparePlaylist(); return true;
            case 'close-sidebar':
                setSidebarCollapsed(true); return true;
            case 'toggle-sidebar': {
                const layout = document.querySelector('.tubio-layout');
                if (layout) setSidebarCollapsed(!layout.classList.contains('sidebar-collapsed'));
                return true;
            }
            case 'select-playlist':
                selectPlaylist(element.dataset.playlistSlug); return true;
            case 'download-video':
                downloadVideo(element); return true;
            case 'search-page':
                runSearch(Number(element.dataset.page), { scroll: true }); return true;
            case 'open-trim':
                if (track) openTrim(track); return true;
            case 'resync-track':
                if (track) resyncTrack(track, element); return true;
            case 'remove-track':
                if (track) removeTrack(track, element); return true;
            default:
                return false;
        }
    }

    function bindDelegatedEvents() {
        document.addEventListener('click', event => {
            const element = event.target.closest('[data-tubio-action]');
            if (!element) return;
            if (element.matches('a')) event.preventDefault();
            const action = element.dataset.tubioAction;
            const handledByPlayer = player()?.handleAction(action, element);
            if (handledByPlayer !== false) return;
            handlePageAction(action, element);
        });
        document.addEventListener('submit', event => {
            const form = event.target.closest('form[data-confirm]');
            if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
        });
        document.getElementById('trim-audio-form')?.addEventListener(
            'submit',
            submitTrim,
        );
    }

    Tubio.ui = {
        decorate,
        notify,
        replaceLibrary,
        selectPlaylist,
        switchTab,
    };

    document.addEventListener('DOMContentLoaded', () => {
        bindDelegatedEvents();
        initializeSearch();
        decorate();
        initializeSidebar();
        player()?.init();
        const initialTab = window.location.hash.replace('#', '') || 'playlists';
        switchTab(['playlists', 'search', 'discover'].includes(initialTab)
            ? initialTab
            : 'playlists');
    });
})();
