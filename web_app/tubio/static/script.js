function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content;
}

function createFormData(fields = {}) {
    const formData = new FormData();
    Object.entries(fields).forEach(([key, value]) => {
        formData.append(key, value);
    });
    const csrfToken = getCsrfToken();
    if (csrfToken) formData.append('csrf_token', csrfToken);
    return formData;
}

function jsonPost(url, fields = {}) {
    return fetch(url, {
        method: 'POST',
        body: createFormData(fields),
        headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        }
    });
}

function reportTubioClientError(scope, error, context = {}) {
    const message = error instanceof Error ? error.message : String(error || 'Unknown client error');
    const stack = error instanceof Error ? error.stack || '' : '';
    jsonPost('/tubio/client-log', {
        scope,
        message,
        stack,
        context: JSON.stringify(context)
    }).catch(() => {});
}

window.addEventListener('error', event => {
    reportTubioClientError('window-error', event.error || event.message, {
        filename: event.filename || '',
        line: event.lineno || 0,
        column: event.colno || 0
    });
});

window.addEventListener('unhandledrejection', event => {
    reportTubioClientError('unhandled-rejection', event.reason);
});

function renderSearchError(message) {
    const resultsDiv = document.getElementById('search-results');
    if (resultsDiv) {
        resultsDiv.innerHTML = `<div class="text-center py-5"><h5 class="text-danger">${escapeHtml(message)}</h5></div>`;
    }
}

// Single persistent <audio> element (in the trackbar, outside #playlists) so
// playlist re-renders never destroy the playing stream. Per-track state (src,
// trim bounds, which crc is loaded) is carried on the element's dataset.
function getAudio() {
    return document.getElementById('tubio-audio');
}

// The single audio element IFF it currently holds this crc, else null.
function getAudioForCrc(crc) {
    const audio = getAudio();
    return audio && audio.dataset.crc === String(crc) ? audio : null;
}

function getTrackItems() {
    return Array.from(document.querySelectorAll('.playlist-track[data-track-key]'));
}

function resolveTrackItem(trackRef) {
    if (trackRef && typeof trackRef === 'object' && trackRef.matches?.('.playlist-track')) {
        return trackRef;
    }

    const ref = String(trackRef ?? '');
    const exact = getTrackItems().find(item => item.dataset.trackKey === ref);
    if (exact) return exact;

    // CRC lookup remains as a compatibility fallback for callers that do not
    // have a playlist occurrence. Prefer the currently loaded occurrence so a
    // duplicate in another playlist cannot steal its controls or metadata.
    const loaded = getLoadedTrackItem();
    if (loaded && loaded.dataset.audioCrc === ref) return loaded;
    return getTrackItems().find(item => item.dataset.audioCrc === ref) || null;
}

function getLoadedTrackItem() {
    const audio = getAudio();
    if (!audio) return null;
    const trackKey = audio.dataset.trackKey;
    return trackKey
        ? getTrackItems().find(item => item.dataset.trackKey === trackKey) || null
        : null;
}

// Point the single audio element at one exact playlist occurrence, reading src
// and trim bounds from that row. Reusing the same element and, where possible,
// the same resource keeps WebKit's established media pipeline alive.
// Returns the audio element, or null if the track isn't in the DOM.
function loadTrack(trackRef, { force = false } = {}) {
    const audio = getAudio();
    if (!audio) return null;
    const item = resolveTrackItem(trackRef);
    if (!item) return null;

    const crc = String(item.dataset.audioCrc);
    const sourceChanged = force ||
        audio.dataset.crc !== crc ||
        audio.getAttribute('src') !== item.dataset.audioSrc;

    audio.dataset.trackKey = item.dataset.trackKey;
    audio.dataset.crc = crc;
    audio.dataset.playlist = item.dataset.playlist || '';
    audio.dataset.playlistKind = item.dataset.playlistKind || '';
    audio.dataset.trimStart = item.dataset.trimStart || '0';
    audio.dataset.trimEnd = item.dataset.trimEnd || '0';
    audio._trimEnded = false;

    if (sourceChanged) {
        audio._metadataReady = false;
        audio.src = item.dataset.audioSrc;
        // Setting src selects the new resource; the immediately following
        // play() drives loading. An extra load() would reset the element again
        // in the most timing-sensitive part of the iOS handoff.
        applyTrackbarVolume(audio);
    }
    return audio;
}

// Unified 'ended' handler for the single audio element. Behavior depends on the
// global loop mode and whether a playlist is driving playback.
function handleTrackEnded() {
    const audio = getAudio();
    setCommittedPlaybackUI(false);
    updateMediaSessionPlaybackState('paused');

    if (globalLoopMode === 'single' && audio) {
        const item = getLoadedTrackItem();
        if (item) {
            requestTrackPlayback(item, getLoadedPlaybackContext(), { restart: true });
        }
        return;
    }

    if (surpriseMode) {
        playNextSurprise();
        return;
    }

    if (isPlayingPlaylist) {
        playNextInQueue(currentPlaylistIndex + 1);
    }
}

function setPlayButtonState(playButton, isPlaying) {
    if (!playButton) return;
    playButton.innerHTML = isPlaying
        ? '<i class="bi bi-pause-fill"></i>'
        : '<i class="bi bi-play-fill"></i>';
    playButton.classList.toggle('btn-success', isPlaying);
    playButton.classList.toggle('btn-outline-primary', !isPlaying);
}

let trackbarVolumePercent = null;
let trackbarMuted = false;

function getTrackbarVolumeRange() {
    return document.getElementById('trackbar-volume');
}

function getTrackbarVolumeControl() {
    return document.querySelector('.trackbar-volume');
}

function setTrackbarVolumePopoverOpen(isOpen) {
    const control = getTrackbarVolumeControl();
    const button = document.getElementById('trackbar-mute');
    if (!control || !button) return;

    control.classList.toggle('is-open', isOpen);
    button.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
}

function isTouchVolumePopover() {
    return window.matchMedia('(hover: none), (pointer: coarse)').matches;
}

function clampTrackbarVolumePercent(value) {
    const range = getTrackbarVolumeRange();
    if (!range) return value;

    const min = Number(range.min);
    const max = Number(range.max);
    const fallback = Number(range.value);
    let volume = Number(value);

    if (!Number.isFinite(volume)) {
        volume = Number.isFinite(fallback) ? fallback : min;
    }

    return Math.min(max, Math.max(min, volume));
}

function getTrackbarVolumeConfig() {
    const trackbar = document.getElementById('tubio-trackbar');
    const range = getTrackbarVolumeRange();
    return {
        defaultVolume: clampTrackbarVolumePercent(trackbar ? trackbar.dataset.defaultVolume : range.value),
        volumeStorageKey: trackbar ? trackbar.dataset.volumeStorageKey : '',
        mutedStorageKey: trackbar ? trackbar.dataset.mutedStorageKey : ''
    };
}

function normalizeTrackbarVolume(percent) {
    const range = getTrackbarVolumeRange();
    if (!range) return 1;

    const min = Number(range.min);
    const max = Number(range.max);
    if (max <= min) return 0;

    return (clampTrackbarVolumePercent(percent) - min) / (max - min);
}

function readStoredTrackbarVolume(config) {
    try {
        const stored = localStorage.getItem(config.volumeStorageKey);
        return stored === null ? config.defaultVolume : clampTrackbarVolumePercent(stored);
    } catch (e) {
        return config.defaultVolume;
    }
}

function readStoredTrackbarMuted(config) {
    try {
        return localStorage.getItem(config.mutedStorageKey) === '1';
    } catch (e) {
        return false;
    }
}

function persistTrackbarVolume(config) {
    try {
        localStorage.setItem(config.volumeStorageKey, String(Math.round(trackbarVolumePercent)));
        localStorage.setItem(config.mutedStorageKey, trackbarMuted ? '1' : '0');
    } catch (e) {}
}

function applyTrackbarVolume(audio) {
    if (!audio || trackbarVolumePercent === null) return;
    audio.volume = normalizeTrackbarVolume(trackbarVolumePercent);
    audio.muted = trackbarMuted;
}

function applyTrackbarVolumeToAll() {
    document.querySelectorAll('audio').forEach(audio => applyTrackbarVolume(audio));
}

function updateTrackbarVolumeUI() {
    const range = getTrackbarVolumeRange();
    const button = document.getElementById('trackbar-mute');
    if (!range || !button || trackbarVolumePercent === null) return;

    const min = Number(range.min);
    const max = Number(range.max);
    const midpoint = min + ((max - min) / 2);
    const icon = button.querySelector('i');

    range.value = String(Math.round(trackbarVolumePercent));
    range.setAttribute('aria-valuetext', `${Math.round(trackbarVolumePercent)}%`);
    button.title = trackbarMuted ? 'Unmute' : 'Mute';
    button.setAttribute('aria-label', button.title);

    if (!icon) return;
    if (trackbarMuted || trackbarVolumePercent <= min) {
        icon.className = 'bi bi-volume-mute-fill';
    } else if (trackbarVolumePercent <= midpoint) {
        icon.className = 'bi bi-volume-down-fill';
    } else {
        icon.className = 'bi bi-volume-up-fill';
    }
}

function initializeTrackbarVolume() {
    const range = getTrackbarVolumeRange();
    if (!range) return;

    const config = getTrackbarVolumeConfig();
    trackbarVolumePercent = readStoredTrackbarVolume(config);
    trackbarMuted = readStoredTrackbarMuted(config);
    updateTrackbarVolumeUI();
    applyTrackbarVolumeToAll();
}

function initializeTrackbarVolumePopover() {
    const control = getTrackbarVolumeControl();
    const button = document.getElementById('trackbar-mute');
    const range = getTrackbarVolumeRange();
    if (!control || !button || !range) return;

    button.addEventListener('click', function(event) {
        if (isTouchVolumePopover() && !control.classList.contains('is-open')) {
            event.preventDefault();
            setTrackbarVolumePopoverOpen(true);
            return;
        }

        toggleTrackbarMute();
    });

    control.addEventListener('mouseenter', () => {
        button.setAttribute('aria-expanded', 'true');
    });
    control.addEventListener('mouseleave', () => {
        if (!control.classList.contains('is-open') && !control.contains(document.activeElement)) {
            button.setAttribute('aria-expanded', 'false');
        }
    });
    control.addEventListener('focusin', () => {
        button.setAttribute('aria-expanded', 'true');
    });
    control.addEventListener('focusout', (event) => {
        if (!control.classList.contains('is-open') && !control.contains(event.relatedTarget)) {
            button.setAttribute('aria-expanded', 'false');
        }
    });

    document.addEventListener('click', function(event) {
        if (!control.contains(event.target)) {
            setTrackbarVolumePopoverOpen(false);
        }
    });

    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            setTrackbarVolumePopoverOpen(false);
            button.blur();
        }
    });
}

function setTrackbarVolume(value) {
    const config = getTrackbarVolumeConfig();
    const range = getTrackbarVolumeRange();
    const min = range ? Number(range.min) : trackbarVolumePercent;

    trackbarVolumePercent = clampTrackbarVolumePercent(value);
    if (trackbarVolumePercent > min) {
        trackbarMuted = false;
    }

    persistTrackbarVolume(config);
    updateTrackbarVolumeUI();
    applyTrackbarVolumeToAll();
}

function toggleTrackbarMute() {
    const config = getTrackbarVolumeConfig();
    trackbarMuted = !trackbarMuted;
    persistTrackbarVolume(config);
    updateTrackbarVolumeUI();
    applyTrackbarVolumeToAll();
}

function updateTrackbarTitleOverflow() {
    const titleEl = document.getElementById('trackbar-title');
    const titleWrap = document.querySelector('.trackbar-title-wrap');
    if (!titleEl || !titleWrap) return;

    titleEl.classList.remove('is-overflowing');
    titleEl.style.removeProperty('--trackbar-title-shift');

    const overflowPx = titleEl.scrollWidth - titleWrap.clientWidth;
    if (overflowPx <= 4) return;

    titleEl.style.setProperty('--trackbar-title-shift', `${overflowPx}px`);
    titleEl.classList.add('is-overflowing');
}

function switchTab(tabName, initializeDiscoverPane = true) {
    // Remove active class from all navbar tabs
    document.querySelectorAll('#search-nav-tab, #playlists-nav-tab, #discover-nav-tab').forEach(tab => {
        tab.classList.remove('active');
    });

    // Add active class to clicked tab
    const navTab = document.getElementById(tabName + '-nav-tab');
    if (navTab) {
        navTab.classList.add('active');
    }

    // Hide all tab content
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('show', 'active');
    });

    // Show the selected tab content
    const targetPane = document.getElementById(tabName);
    if (targetPane) {
        targetPane.classList.add('show', 'active');
    }

    document.querySelectorAll('.tubio-playlists-action').forEach(item => {
        item.classList.toggle('d-none', tabName !== 'playlists');
    });
    document.querySelectorAll('.tubio-discover-action').forEach(item => {
        item.classList.toggle('d-none', tabName !== 'discover');
    });

    // Search has no contextual actions. Playlists and Discover each expose
    // their own action set.
    const actionsDropdown = document.querySelector('.actions-dropdown');
    const actionsContainer = actionsDropdown ? actionsDropdown.closest('.dropdown') : null;
    if (actionsContainer) {
        actionsContainer.classList.toggle(
            'd-none',
            tabName !== 'playlists' && tabName !== 'discover'
        );
    }

    // Hide the trackbar in search mode.
    const trackbar = document.getElementById('tubio-trackbar');
    if (trackbar) trackbar.classList.toggle('d-none', tabName === 'search');

    if (tabName === 'discover' && initializeDiscoverPane) {
        initializeDiscover();
    }

    // Update URL hash
    window.location.hash = '#' + tabName;
}

function displaySearchResults(data, { targetId = 'search-results', paginate = true, emptyMessage = 'No results found' } = {}) {
    const resultsDiv = document.getElementById(targetId);
    if (!resultsDiv) return;

    const results = Array.isArray(data) ? data : (data && data.results) || [];
    const page = (data && typeof data.page === 'number') ? data.page : 0;
    const totalPages = (data && typeof data.total_pages === 'number') ? data.total_pages : 1;
    const filteredTooLong = (data && typeof data.filtered_too_long === 'number') ? data.filtered_too_long : 0;
    const maxMinutes = (data && typeof data.max_video_length_minutes === 'number') ? data.max_video_length_minutes : 0;

    const filterNotice = filteredTooLong > 0
        ? `<div class="alert alert-warning d-flex align-items-center gap-2 py-2" role="alert">
                <i class="bi bi-clock-history"></i>
                <span>${filteredTooLong} result${filteredTooLong === 1 ? '' : 's'} hidden for exceeding the ${maxMinutes}-minute limit. Consider appending ' short' in the search.</span>
           </div>`
        : '';

    if (!results || results.length === 0) {
        resultsDiv.innerHTML = `${filterNotice}<div class="text-center py-5"><h5 class="text-muted">${escapeHtml(emptyMessage)}</h5></div>`;
        return;
    }

    const accordionId = `${targetId}-accordion`;
    let html = filterNotice + `<div class="accordion" id="${accordionId}">`;

    results.forEach((video, index) => {
        const isDisabled = video.cached ? 'disabled style="background-color: #adb5bd; border-color: #adb5bd;"' : '';
        const safeTitle = escapeHtml(video.title);
        const truncatedTitle = video.title.length > 60 ? escapeHtml(video.title.substring(0, 60) + '...') : safeTitle;
        const safeDesc = escapeHtml(video.description);
        const safeViews = escapeHtml(String(video.view_count));
        const safePublished = escapeHtml(String(video.published));
        const safeLength = escapeHtml(String(video.length));
        const safeVideoId = escapeHtml(video.video_id);
        const safeThumbnail = video.thumbnail_url ? escapeHtml(video.thumbnail_url) : '';

        html += `
            <div class="accordion-item mb-3 border-0 shadow-sm">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed bg-gradient text-primary fw-semibold search-result-btn"
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#collapse-${targetId}-${index}"
                            aria-expanded="false"
                            aria-controls="collapse-${targetId}-${index}"
                            style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);">
                        <i class="bi bi-youtube me-2 flex-shrink-0"></i>
                        <span class="search-result-title">${truncatedTitle}</span>
                        <small class="badge bg-secondary ms-2 flex-shrink-0">${safeLength}</small>
                    </button>
                </h2>
                <div id="collapse-${targetId}-${index}"
                     class="accordion-collapse collapse"
                     data-bs-parent="#${accordionId}">
                    <div class="accordion-body bg-light">
                        <div class="row">
                            <div class="col-md-4 mb-3 text-center">
                                ${safeThumbnail ? `<img src="${safeThumbnail}" alt="Thumbnail" class="img-fluid rounded shadow-sm" style="max-height: 180px;">` : ''}
                            </div>
                            <div class="col-md-8">
                                <div class="mb-3">
                                    <h6 class="text-primary mb-2">Full Title:</h6>
                                    <p class="text-dark fw-medium">${safeTitle}</p>
                                </div>
                                <div class="row">
                                    <div class="col-md-12 mb-3">
                                        <h6 class="text-primary mb-2">Description:</h6>
                                        <p class="text-muted small" style="max-height: 100px; overflow-y: auto;">${safeDesc}</p>
                                    </div>
                                </div>
                                <div class="row">
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Views:</h6>
                                        <p class="text-dark">${safeViews}</p>
                                    </div>
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Published:</h6>
                                        <p class="text-dark">${safePublished}</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="text-center mt-3">
                            <button onclick="downloadVideo('${safeVideoId}', '${safeTitle.replace(/'/g, "\\'")}', this)"
                                    class="btn btn-primary" ${isDisabled}>
                                <i class="bi bi-download me-1"></i>Add To Favourites
                            </button>
                            <div class="progress mt-2 d-none" style="height: 20px;">
                                <div class="progress-bar progress-bar-striped progress-bar-animated"
                                     role="progressbar" style="width: 0%;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">0%</div>
                            </div>
                            <small class="text-muted d-none"></small>
                        </div>
                    </div>
                </div>
            </div>
        `;
    });
    
    html += '</div>';

    if (paginate && totalPages > 1) {
        let buttons = '';
        for (let i = 0; i < totalPages; i++) {
            const isActive = i === page;
            buttons += `
                <button type="button"
                        class="btn ${isActive ? 'btn-primary' : 'btn-outline-primary'}"
                        onclick="searchPage(${i})"
                        ${isActive ? 'disabled' : ''}>
                    ${i + 1}
                </button>
            `;
        }
        html += `
            <div class="d-flex justify-content-center gap-2 mt-3 mb-4">
                ${buttons}
            </div>
        `;
    }

    resultsDiv.innerHTML = html;
}

async function searchPage(page) {
    await runSearch(page, { scrollToResults: true });
}

let suggestRequestSeq = 0;

function hideSuggestions() {
    const list = document.getElementById('search-suggestions');
    const input = document.getElementById('youtube-query');
    if (list) {
        list.innerHTML = '';
        list.hidden = true;
    }
    if (input) input.setAttribute('aria-expanded', 'false');
}

function acceptSuggestion(value) {
    const input = document.getElementById('youtube-query');
    if (!input) return;
    input.value = value;
    hideSuggestions();
    runSearch(0);
}

function renderSuggestions(suggestions) {
    const list = document.getElementById('search-suggestions');
    const input = document.getElementById('youtube-query');
    if (!list) return;
    if (!Array.isArray(suggestions) || suggestions.length === 0) {
        hideSuggestions();
        return;
    }
    list.innerHTML = suggestions.map((s, i) =>
        `<li class="tubio-suggestion" role="option" data-index="${i}" data-value="${escapeHtml(s)}">
            <i class="bi bi-search"></i><span>${escapeHtml(s)}</span>
        </li>`
    ).join('');
    list.hidden = false;
    if (input) input.setAttribute('aria-expanded', 'true');
}

function moveSuggestionActive(delta) {
    const list = document.getElementById('search-suggestions');
    if (!list || list.hidden) return;
    const items = Array.from(list.querySelectorAll('.tubio-suggestion'));
    if (items.length === 0) return;
    const currentIndex = items.findIndex(el => el.classList.contains('active'));
    let nextIndex = currentIndex + delta;
    if (nextIndex < 0) nextIndex = items.length - 1;
    if (nextIndex >= items.length) nextIndex = 0;
    items.forEach(el => el.classList.remove('active'));
    const active = items[nextIndex];
    active.classList.add('active');
    active.scrollIntoView({ block: 'nearest' });
}

async function fetchSuggestions(query) {
    const seq = ++suggestRequestSeq;
    try {
        const response = await jsonPost('/tubio/suggest', { youtube_query: query });
        const data = await response.json();
        if (seq !== suggestRequestSeq) return; // stale response
        if (response.ok) renderSuggestions(data.suggestions);
    } catch (error) {
        console.error('Error fetching suggestions:', error);
    }
}

function setupSuggestions() {
    const input = document.getElementById('youtube-query');
    const list = document.getElementById('search-suggestions');
    if (!input || !list) return;

    const debounceMs = parseInt(input.dataset.debounceMs, 10) || 200;
    const minLen = parseInt(input.dataset.minLen, 10) || 2;
    let debounceTimer = null;

    input.addEventListener('input', function() {
        if (debounceTimer) clearTimeout(debounceTimer);
        const query = input.value.trim();
        if (query.length < minLen) {
            hideSuggestions();
            return;
        }
        debounceTimer = setTimeout(() => fetchSuggestions(query), debounceMs);
    });

    input.addEventListener('keydown', function(e) {
        if (list.hidden) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            moveSuggestionActive(1);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            moveSuggestionActive(-1);
        } else if (e.key === 'Enter') {
            const active = list.querySelector('.tubio-suggestion.active');
            if (active) {
                e.preventDefault();
                acceptSuggestion(active.dataset.value);
            }
        } else if (e.key === 'Escape') {
            hideSuggestions();
        }
    });

    list.addEventListener('mousedown', function(e) {
        // mousedown (not click) so it fires before the input's blur
        const item = e.target.closest('.tubio-suggestion');
        if (item) {
            e.preventDefault();
            acceptSuggestion(item.dataset.value);
        }
    });

    input.addEventListener('blur', function() {
        setTimeout(hideSuggestions, 150);
    });
}

async function runSearch(page, { scrollToResults = false } = {}) {
    const queryInput = document.getElementById('youtube-query');
    if (!queryInput) return;
    const query = queryInput.value;
    if (!query) return;

    const resultsDiv = document.getElementById('search-results');
    try {
        const response = await jsonPost('/tubio/search', {
            youtube_query: query,
            page: String(page)
        });

        const data = await response.json();
        if (response.ok) {
            displaySearchResults(data);
            if (scrollToResults && resultsDiv) {
                resultsDiv.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        } else {
            const errorMsg = data.error || 'Search failed. Please try again.';
            renderSearchError(errorMsg);
        }
    } catch (error) {
        console.error('Error during search:', error);
        renderSearchError('Error occurred while searching.');
    }
}

// Initialize everything when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Initialize navbar tab state from URL hash
    const hash = window.location.hash.replace('#', '') || 'playlists';
    switchTab(hash);

    // Setup search form handler if it exists
    const searchForm = document.getElementById('search-form');
    if (searchForm) {
        searchForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            hideSuggestions();
            await runSearch(0);
        });
    }

    setupSuggestions();
});

async function downloadVideo(videoId, title, buttonElement) {
    const originalText = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Starting...';

    // Find progress elements relative to button's parent
    const container = buttonElement.closest('.text-center');
    const progressContainer = container ? container.querySelector('.progress') : null;
    const progressBar = progressContainer ? progressContainer.querySelector('.progress-bar') : null;
    const progressStatus = container ? container.querySelector('small.text-muted') : null;

    if (progressContainer) progressContainer.classList.remove('d-none');
    if (progressStatus) progressStatus.classList.remove('d-none');

    let eventSource = null;

    function updateProgress(percent, status) {
        if (progressBar) {
            progressBar.style.width = `${percent}%`;
            progressBar.setAttribute('aria-valuenow', percent);
            progressBar.textContent = `${Math.round(percent)}%`;
        }
        if (progressStatus) {
            const statusText = status === 'downloading' ? 'Converting...' :
                               status === 'processing' ? 'Processing audio...' :
                               status === 'complete' ? 'Converted!' : status;
            progressStatus.textContent = statusText;
        }
    }

    function hideProgress() {
        if (progressContainer) progressContainer.classList.add('d-none');
        if (progressStatus) progressStatus.classList.add('d-none');
    }

    try {
        eventSource = new EventSource(`/tubio/download_progress/${videoId}`);
        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.status === 'not_found' || data.status === 'complete' || data.status === 'error') {
                eventSource.close();
            }
            if (data.percent !== undefined) {
                updateProgress(data.percent, data.status);
            }
        };
        eventSource.onerror = () => eventSource.close();

        const response = await jsonPost('/tubio/youtube_download', {
            video_id: videoId,
            title
        });

        const data = await response.json();
        if (eventSource) eventSource.close();

        if (response.ok && data.success) {
            updateProgress(100, 'complete');
            showNotification(data.message, 'success');
            await updateContent(data);

            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i>Converted';
            buttonElement.style.backgroundColor = '#adb5bd';
            buttonElement.style.borderColor = '#adb5bd';
            setTimeout(hideProgress, 1500);
        } else {
            throw new Error(data.error || 'Conversion failed');
        }

    } catch (error) {
        if (eventSource) eventSource.close();
        console.error('Error converting video:', error);
        showNotification(error.message || 'Error converting video', 'error');
        hideProgress();
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalText;
    }
}

async function updateContent(data) {
    // Instead of rendering HTML in JavaScript, fetch server-rendered HTML
    try {
        const response = await fetch('/tubio/', {
            method: 'GET',
            headers: {
                'Accept': 'text/html',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        if (response.ok) {
            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            const newPlaylistsContent = doc.getElementById('playlists');
            const currentPlaylistsTab = document.getElementById('playlists');

            if (newPlaylistsContent && currentPlaylistsTab) {
                // The single <audio> lives in the trackbar (outside #playlists),
                // so replacing the tab no longer touches playback. Re-render the
                // list, then restore the playing track's row UI.
                currentPlaylistsTab.innerHTML = newPlaylistsContent.innerHTML;

                initializeAudioEventListeners();
                initializeLazyThumbnails();
                initializeTooltips();
                initializeTrimForm();
                initializeSidebar();
                initializeTrackbarVolume();

                const audio = getAudio();
                let loadedItem = getLoadedTrackItem();
                if (!loadedItem && audio?.dataset.crc) {
                    const previousTrackKey = audio.dataset.trackKey;
                    loadedItem = getTrackItems().find(item =>
                        item.dataset.audioCrc === audio.dataset.crc &&
                        item.dataset.playlist === audio.dataset.playlist
                    ) || null;
                    if (loadedItem) {
                        audio.dataset.trackKey = loadedItem.dataset.trackKey;
                        if (currentTrackKey === previousTrackKey) {
                            currentTrackKey = loadedItem.dataset.trackKey;
                        }
                        if (pendingPlayback?.trackKey === previousTrackKey) {
                            pendingPlayback.trackKey = loadedItem.dataset.trackKey;
                        }
                    }
                }
                if (loadedItem) {
                    // Trim bounds may have changed in the re-rendered DOM.
                    audio.dataset.trimStart = loadedItem.dataset.trimStart || '0';
                    audio.dataset.trimEnd = loadedItem.dataset.trimEnd || '0';
                    if (
                        currentTrackKey === loadedItem.dataset.trackKey &&
                        !audio.paused
                    ) {
                        syncAudioButtonUI(loadedItem);
                        loadedItem.classList.add('track-playing');
                    }
                    updateTrackbar(currentTrackKey);
                    updateTrackbarScrubber();
                } else {
                    updateTrackbar(currentTrackKey);
                    updateTrackbarScrubber();
                }
            }
        }
    } catch (error) {
        console.error('Error updating content:', error);
        window.location.reload();
    }
}

// Sidebar / panel selection
function isMobileViewport() {
    return window.matchMedia('(max-width: 768px)').matches;
}

function setSidebarCollapsed(collapsed) {
    const layout = document.querySelector('.tubio-layout');
    if (!layout) return;
    layout.classList.toggle('sidebar-collapsed', collapsed);
    try {
        sessionStorage.setItem('tubioSidebarCollapsed', collapsed ? '1' : '0');
    } catch (e) {}
}

function toggleSidebar() {
    const layout = document.querySelector('.tubio-layout');
    if (!layout) return;
    setSidebarCollapsed(!layout.classList.contains('sidebar-collapsed'));
}

function closeSidebar() {
    setSidebarCollapsed(true);
}

function selectPlaylist(slug) {
    const sidebar = document.getElementById('playlist-sidebar-list');
    if (sidebar) {
        sidebar.querySelectorAll('.sidebar-item').forEach(item => {
            item.classList.toggle('active', item.dataset.playlistSlug === slug);
        });
    }
    document.querySelectorAll('.playlist-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `panel-${slug}`);
    });
    const emptyPanel = document.getElementById('panel-empty');
    if (emptyPanel) emptyPanel.classList.add('hidden');

    try { sessionStorage.setItem('tubioSelectedPlaylist', slug); } catch (e) {}

    if (isMobileViewport()) closeSidebar();
}

function initializeSidebar() {
    const layout = document.querySelector('.tubio-layout');
    if (layout) {
        let collapsed;
        try {
            const saved = sessionStorage.getItem('tubioSidebarCollapsed');
            collapsed = saved === null ? isMobileViewport() : saved === '1';
        } catch (e) {
            collapsed = isMobileViewport();
        }
        layout.classList.toggle('sidebar-collapsed', collapsed);
    }

    const sidebar = document.getElementById('playlist-sidebar-list');
    if (!sidebar) return;

    const items = sidebar.querySelectorAll('.sidebar-item[data-playlist-slug]');
    if (items.length === 0) return;

    let target = null;
    try {
        const saved = sessionStorage.getItem('tubioSelectedPlaylist');
        if (saved) {
            target = sidebar.querySelector(`.sidebar-item[data-playlist-slug="${CSS.escape(saved)}"]`);
        }
    } catch (e) {}

    if (!target) target = items[0];

    // selectPlaylist may auto-close the sidebar on mobile; preserve the prior collapsed
    // state by skipping autoclose during init.
    const wasMobile = isMobileViewport();
    const layoutEl = document.querySelector('.tubio-layout');
    const wasCollapsed = layoutEl && layoutEl.classList.contains('sidebar-collapsed');
    selectPlaylist(target.dataset.playlistSlug);
    if (wasMobile && layoutEl) {
        layoutEl.classList.toggle('sidebar-collapsed', wasCollapsed);
    }
}

function updateSidebarCounts() {
    const sidebar = document.getElementById('playlist-sidebar-list');
    if (!sidebar) return;
    sidebar.querySelectorAll('.sidebar-item[data-playlist-slug]').forEach(item => {
        const slug = item.dataset.playlistSlug;
        const panel = document.getElementById(`panel-${slug}`);
        if (!panel) return;
        const count = panel.querySelectorAll('.accordion-item[data-audio-crc]').length;
        const meta = item.querySelector('.sidebar-item-meta');
        if (meta) meta.textContent = `${count} song${count === 1 ? '' : 's'}`;
        const headerBadge = panel.querySelector('.playlist-panel-title .badge');
        if (headerBadge) headerBadge.textContent = `${count} songs`;
    });
}

async function removeTrack(crc, buttonElement) {
    if (!confirm('Remove this track from your playlists?')) return;

    const originalHTML = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Removing...';

    const tooltip = bootstrap.Tooltip.getInstance(buttonElement);
    if (tooltip) tooltip.dispose();

    try {
        // Stop audio if currently playing this track
        const audio = getAudioForCrc(crc);
        if (audio && !audio.paused) pauseCurrentPlayback();

        const response = await jsonPost(`/tubio/delete_audio/${crc}`);

        if (!response.ok) throw new Error('Failed to remove track');

        document.querySelectorAll(`.accordion-item[data-audio-crc="${crc}"]`).forEach(el => el.remove());
        if (String(currentTrackCrc) === String(crc)) {
            const player = getAudio();
            if (player) {
                player.pause();
                player.removeAttribute('src');
                delete player.dataset.crc;
                delete player.dataset.trackKey;
            }
            currentTrackCrc = null;
            currentTrackKey = null;
            pendingPlayback = null;
            isPlayingPlaylist = false;
            updateTrackbar(null);
            updateTrackbarScrubber();
        }
        updateSidebarCounts();
        showNotification('Track removed', 'success');
    } catch (error) {
        console.error('Error removing track:', error);
        showNotification(error.message || 'Error removing track', 'error');
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalHTML;
    }
}

// Individual track playback controls
function togglePlayTrack(trackRef) {
    if (surpriseMode) exitSurpriseMode();
    const trackItem = resolveTrackItem(trackRef);
    if (!trackItem) {
        console.error('Track not found:', trackRef);
        return Promise.resolve(false);
    }

    const crc = trackItem.dataset.audioCrc;
    const loaded = getAudioForCrc(crc);

    // A committed playing occurrence toggles to pause. A loaded-but-pending
    // occurrence is retried, including WebKit's paused=false/silent state.
    if (loaded && !loaded.paused) {
        if (
            currentTrackKey === trackItem.dataset.trackKey &&
            !pendingPlayback
        ) {
            pauseCurrentPlayback();
            return Promise.resolve(true);
        }
        pauseCurrentPlayback();
    }

    if (trackItem.dataset.playlist) {
        currentPlaylistName = trackItem.dataset.playlist;
    }

    return requestTrackPlayback(trackItem, {
        playlistIndex: null,
        surpriseIndex: null,
    });
}

// Global playback state — controlled from the bottom trackbar
let globalShuffle = false;
let globalLoopMode = 'off'; // 'off' | 'playlist' | 'single'

function toggleShuffle() {
    globalShuffle = !globalShuffle;
    const btn = document.getElementById('trackbar-shuffle');
    if (btn) {
        btn.classList.toggle('active', globalShuffle);
        btn.title = globalShuffle ? 'Shuffle: On' : 'Shuffle: Off';
    }

    // If a playlist is currently playing, reshuffle the upcoming queue
    if (isPlayingPlaylist && currentPlaylistQueue.length > 1) {
        const played = currentPlaylistQueue.slice(0, currentPlaylistIndex + 1);
        const upcoming = currentPlaylistQueue.slice(currentPlaylistIndex + 1);
        currentPlaylistQueue = played.concat(globalShuffle ? shuffleArray(upcoming) : upcoming);
        prefetchNextPlaylistTrack();
    }
}

// Fisher-Yates shuffle algorithm
function shuffleArray(array) {
    const shuffled = [...array];
    for (let i = shuffled.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
}

function cycleLoopMode() {
    const order = ['off', 'playlist', 'single'];
    globalLoopMode = order[(order.indexOf(globalLoopMode) + 1) % order.length];

    const btn = document.getElementById('trackbar-loop');
    if (!btn) return;
    btn.classList.remove('active');
    if (globalLoopMode === 'off') {
        btn.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
        btn.title = 'Loop: Off';
    } else if (globalLoopMode === 'playlist') {
        btn.classList.add('active');
        btn.innerHTML = '<i class="bi bi-arrow-repeat"></i><small>All</small>';
        btn.title = 'Loop: Playlist';
    } else {
        btn.classList.add('active');
        btn.innerHTML = '<i class="bi bi-arrow-repeat"></i><small>1</small>';
        btn.title = 'Loop: Single';
    }
    if (isPlayingPlaylist || surpriseMode) prefetchNextPlaylistTrack();
}

function formatTime(seconds) {
    if (isNaN(seconds) || !isFinite(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function seekTrack(crc, value) {
    const audio = getAudioForCrc(crc);
    if (!audio) return;

    if (audio.readyState === 0) {
        audio.addEventListener('loadedmetadata', function onMeta() {
            audio.removeEventListener('loadedmetadata', onMeta);
            const bounds = getPlaybackBounds(audio);
            audio.currentTime = bounds.start + ((value / 100) * (bounds.end - bounds.start));
            updateTrackbarScrubber();
            updateMediaSessionPositionState();
        }, { once: true });
        audio.load();
    } else if (audio.duration && isFinite(audio.duration)) {
        const bounds = getPlaybackBounds(audio);
        audio.currentTime = bounds.start + ((value / 100) * (bounds.end - bounds.start));
        updateTrackbarScrubber();
        updateMediaSessionPositionState();
    }
}

// Committed playback identifies the last occurrence that reached `playing`.
// A separate pending request keeps a failed handoff retryable without making
// the UI or Media Session claim that the new track is already playing.
let currentTrackCrc = null;
let currentTrackKey = null;
let pendingPlayback = null;
let playbackAttemptSequence = 0;

// Playlist playback functionality
let currentPlaylistQueue = [];
let currentPlaylistIndex = 0;
let isPlayingPlaylist = false;
let currentPlaylistName = '';

let surpriseMode = false;
let surprisePlaylist = null;
let surpriseCurrentIndex = -1;
let surpriseExhausted = false;
let surpriseFilling = false;

function getLoadedPlaybackContext() {
    const audio = getAudio();
    if (
        pendingPlayback &&
        audio &&
        pendingPlayback.trackKey === audio.dataset.trackKey
    ) {
        return {
            playlistIndex: pendingPlayback.playlistIndex,
            surpriseIndex: pendingPlayback.surpriseIndex,
        };
    }
    if (surpriseMode && surpriseCurrentIndex >= 0) {
        return { playlistIndex: null, surpriseIndex: surpriseCurrentIndex };
    }
    if (isPlayingPlaylist && currentPlaylistQueue.length > 0) {
        return { playlistIndex: currentPlaylistIndex, surpriseIndex: null };
    }
    return { playlistIndex: null, surpriseIndex: null };
}

function setCommittedPlaybackUI(isPlaying) {
    const item = resolveTrackItem(currentTrackKey);
    if (item) {
        setPlayButtonState(item.querySelector('.track-play-btn'), isPlaying);
        item.classList.toggle('track-playing', isPlaying);
    }
    updateTrackbarPlayPauseUI(isPlaying);
    if (isPlayingPlaylist && currentPlaylistName) {
        updatePlaylistPlayButton(currentPlaylistName, isPlaying);
    }
    if (surpriseMode) renderSurprisePlaylist();
}

function resetAllTrackPlayingUI() {
    getTrackItems().forEach(item => {
        item.classList.remove('track-playing');
        setPlayButtonState(item.querySelector('.track-play-btn'), false);
    });
}

function commitLoadedPlayback(audio) {
    const item = getLoadedTrackItem();
    if (!item || audio.paused) return false;

    const context = pendingPlayback?.trackKey === item.dataset.trackKey
        ? pendingPlayback
        : getLoadedPlaybackContext();

    resetAllTrackPlayingUI();
    currentTrackKey = item.dataset.trackKey;
    currentTrackCrc = item.dataset.audioCrc;

    if (Number.isInteger(context.playlistIndex)) {
        currentPlaylistIndex = context.playlistIndex;
        currentPlaylistName = item.dataset.playlist || currentPlaylistName;
        isPlayingPlaylist = true;
        surpriseMode = false;
    } else if (Number.isInteger(context.surpriseIndex)) {
        surpriseCurrentIndex = context.surpriseIndex;
        surpriseMode = true;
        isPlayingPlaylist = false;
        const title = item.dataset.title || 'track';
        setSurpriseStatus(`Surprise Playlist · now playing “${title}”`);
        fillSurpriseBuffer();
    }

    pendingPlayback = null;
    setCommittedPlaybackUI(true);
    updateTrackbar(currentTrackKey);
    updateTrackbarScrubber();
    updateMediaSessionMetadata(item);
    updateMediaSessionPlaybackState('playing');
    updateMediaSessionPositionState();
    prefetchNextPlaylistTrack();

    if (document.visibilityState === 'visible') {
        item.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    return true;
}

function handlePlaybackFailure(error, attempt) {
    const audio = getAudio();
    if (!attempt || attempt.attemptId !== playbackAttemptSequence) return false;
    if (!audio || audio.dataset.trackKey !== attempt.trackKey) return false;

    attempt.failed = true;
    pendingPlayback = attempt;
    if (!audio.paused) audio.pause();
    setCommittedPlaybackUI(false);
    updateMediaSessionPlaybackState('paused');
    updateMediaSessionPositionState();

    console.error('Tubio playback request failed:', error);
    reportTubioClientError('playback-request', error, {
        crc: attempt.crc,
        trackKey: attempt.trackKey,
        playlistIndex: attempt.playlistIndex,
        surpriseIndex: attempt.surpriseIndex,
        visibility: document.visibilityState,
    });
    if (document.visibilityState === 'visible') {
        showNotification('Playback could not start. Press play to retry.', 'error');
    }
    return false;
}

function requestTrackPlayback(
    trackRef,
    context = { playlistIndex: null, surpriseIndex: null },
    { restart = false } = {}
) {
    const item = resolveTrackItem(trackRef);
    if (!item) {
        console.error('Track not found:', trackRef);
        return Promise.resolve(false);
    }

    const attempt = {
        attemptId: ++playbackAttemptSequence,
        trackKey: item.dataset.trackKey,
        crc: item.dataset.audioCrc,
        playlistIndex: Number.isInteger(context.playlistIndex)
            ? context.playlistIndex
            : null,
        surpriseIndex: Number.isInteger(context.surpriseIndex)
            ? context.surpriseIndex
            : null,
        failed: false,
    };
    pendingPlayback = attempt;

    if (currentTrackKey && currentTrackKey !== attempt.trackKey) {
        setCommittedPlaybackUI(false);
    }

    const audio = loadTrack(item);
    if (!audio) return Promise.resolve(false);
    applyPlaybackStart(audio, { restart });
    updateMediaSessionPlaybackState('paused');

    let playResult;
    try {
        // Call play synchronously in the ended/action-handler stack. Awaiting
        // metadata or a timer first can lose iOS's background media assertion.
        playResult = audio.play();
    } catch (error) {
        return Promise.resolve(handlePlaybackFailure(error, attempt));
    }

    return Promise.resolve(playResult).then(
        () => {
            if (attempt.attemptId !== playbackAttemptSequence) return false;
            // `playing` is the success signal and owns the UI commit. A resolved
            // play() with a still-paused element is an unsuccessful request.
            if (audio.paused) {
                return handlePlaybackFailure(
                    new Error('The media element remained paused after play() resolved.'),
                    attempt
                );
            }
            return true;
        },
        error => handlePlaybackFailure(error, attempt)
    ).catch(error => {
        // Keep every UI, inline onclick, and Media Session caller safe from an
        // unhandled rejection even if failure reporting itself encounters an
        // unexpected browser-specific error.
        console.error('Unexpected error while settling playback:', error);
        setCommittedPlaybackUI(false);
        updateMediaSessionPlaybackState('paused');
        return false;
    });
}

function pauseCurrentPlayback() {
    playbackAttemptSequence++;
    const audio = getAudio();
    if (audio && !audio.paused) audio.pause();
    setCommittedPlaybackUI(false);
    updateMediaSessionPlaybackState('paused');
    updateMediaSessionPositionState();
}

function resumeLoadedPlayback() {
    const item = getLoadedTrackItem() || resolveTrackItem(currentTrackKey);
    if (!item) return Promise.resolve(false);
    const audio = getAudio();
    if (pendingPlayback && audio && !audio.paused) {
        // Recovery for the WebKit state where play() was accepted but the
        // element never reached `playing`: reset only in response to an
        // explicit user/Media Session play action, then retry immediately.
        audio.pause();
    }
    return requestTrackPlayback(item, getLoadedPlaybackContext());
}

function surpriseBufferSize() {
    const container = document.getElementById('surprise-playlist');
    const n = container ? parseInt(container.dataset.bufferSize, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 5;
}

function surpriseCachePollIntervalMs() {
    const container = document.getElementById('surprise-playlist');
    const n = container ? parseInt(container.dataset.cachePollIntervalMs, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 750;
}

function surpriseCrcs() {
    return surprisePlaylist?.audio_crcs || [];
}

function normalizeSurprisePlaylist(payload, source) {
    if (payload === null || payload === undefined) return null;
    if (!Array.isArray(payload.audio_crcs)) {
        const error = new Error('Surprise Playlist data is invalid. Reload Discover to try again.');
        reportTubioClientError('surprise-payload', error, {
            source,
            hasPayload: Boolean(payload),
            audioCrcsType: typeof payload?.audio_crcs
        });
        throw error;
    }
    return payload;
}

function setSurpriseStatus(message) {
    const el = document.getElementById('surprise-status');
    if (el) el.textContent = message || '';
}

function exitSurpriseMode() {
    surpriseMode = false;
    setSurpriseStatus('');
}

async function fillSurpriseBuffer({ requirePlayback = true } = {}) {
    if (surpriseFilling || !surprisePlaylist) return;
    surpriseFilling = true;
    try {
        while ((!requirePlayback || surpriseMode) && !surpriseExhausted &&
               surpriseCrcs().length - surpriseCurrentIndex - 1 < surpriseBufferSize()) {
            const response = await jsonPost('/tubio/surprise/grow');
            const data = await response.json();
            if (data.exhausted) {
                surpriseExhausted = true;
                break;
            }
            if (response.status === 404 ||
                response.status === 409) {
                if (!await loadSurprisePlaylist()) break;
                continue;
            }
            if (!response.ok || !data.playlist) break;
            surprisePlaylist = normalizeSurprisePlaylist(data.playlist, 'grow');
            renderSurprisePlaylist();
            prefetchNextPlaylistTrack();
        }
    } finally {
        surpriseFilling = false;
    }
}

async function playNextSurprise() {
    if (!surpriseMode || !surprisePlaylist) return;
    const pendingIndex = pendingPlayback?.surpriseIndex;
    const nextIndex = Number.isInteger(pendingIndex)
        ? pendingIndex + 1
        : surpriseCurrentIndex + 1;
    if (nextIndex >= surpriseCrcs().length && !surpriseExhausted) {
        setSurpriseStatus('Loading next track…');
        await fillSurpriseBuffer();
    }
    if (nextIndex >= surpriseCrcs().length) {
        showNotification('Surprise playlist finished', 'info');
        exitSurpriseMode();
        return;
    }
    playSurpriseTrack(nextIndex);
}

function sleep(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}

const trackConversionPromises = new Map();
const trackPrefetchPromises = new Map();
const prefetchedAudioSources = new Set();

function markTrackCached(crc) {
    getTrackItems()
        .filter(item => item.dataset.audioCrc === String(crc))
        .forEach(item => {
            item.dataset.isCached = 'true';
        });
}

function convertTrackForPlayback(item) {
    if (!item) return Promise.reject(new Error('Track is no longer available'));
    if (item.dataset.isCached !== 'false') return Promise.resolve(true);

    const crc = String(item.dataset.audioCrc);
    const existing = trackConversionPromises.get(crc);
    if (existing) return existing;

    const conversion = (async () => {
        while (true) {
            const response = await jsonPost(
                `/tubio/audio/${encodeURIComponent(crc)}/cache`
            );
            const data = await response.json();
            if (response.status === 202) {
                await sleep(surpriseCachePollIntervalMs());
                continue;
            }
            if (!response.ok || !data.is_cached) {
                throw new Error(data.error || 'Could not convert this track');
            }
            markTrackCached(crc);
            return true;
        }
    })();

    trackConversionPromises.set(crc, conversion);
    conversion.finally(() => {
        if (trackConversionPromises.get(crc) === conversion) {
            trackConversionPromises.delete(crc);
        }
    }).catch(() => {});
    return conversion;
}

function prefetchTrackAudio(item) {
    if (!item?.dataset.audioSrc) return Promise.resolve(false);

    const source = item.dataset.audioSrc;
    if (prefetchedAudioSources.has(source)) return Promise.resolve(true);

    const existing = trackPrefetchPromises.get(source);
    if (existing) return existing;

    const prefetch = (async () => {
        await convertTrackForPlayback(item);
        const response = await fetch(source, {
            credentials: 'same-origin',
            headers: { 'Accept': 'audio/mp4' }
        });
        if (!response.ok) {
            throw new Error(`Could not prefetch track (${response.status})`);
        }
        // Consuming the full response lets the browser retain the complete,
        // cacheable audio resource for the upcoming media request.
        await response.blob();
        prefetchedAudioSources.add(source);
        return true;
    })();

    trackPrefetchPromises.set(source, prefetch);
    prefetch.finally(() => {
        if (trackPrefetchPromises.get(source) === prefetch) {
            trackPrefetchPromises.delete(source);
        }
    }).catch(() => {});
    return prefetch;
}

function getNextPlaylistTrackItem() {
    if (surpriseMode) {
        return getSurpriseTrackItem(surpriseCurrentIndex + 1);
    }
    if (!isPlayingPlaylist || currentPlaylistQueue.length === 0) return null;

    let nextIndex = currentPlaylistIndex + 1;
    if (nextIndex >= currentPlaylistQueue.length) {
        if (globalLoopMode !== 'playlist' || globalShuffle) return null;
        nextIndex = 0;
    }
    return resolveTrackItem(currentPlaylistQueue[nextIndex]);
}

function prefetchNextPlaylistTrack() {
    const item = getNextPlaylistTrackItem();
    if (!item) return Promise.resolve(false);

    return prefetchTrackAudio(item).catch(error => {
        console.error('Could not prepare next Tubio track:', error);
        reportTubioClientError('track-prefetch', error, {
            crc: item.dataset.audioCrc,
            trackKey: item.dataset.trackKey,
            playlistKind: item.dataset.playlistKind,
        });
        return false;
    });
}

async function ensureSurpriseTrackCached(crc) {
    const item = getTrackItems().find(
        track => track.dataset.playlistKind === 'surprise' &&
            track.dataset.audioCrc === String(crc)
    );
    if (!item) {
        showNotification('This Surprise track is no longer available', 'error');
        return false;
    }
    const needsDownload = item.dataset.isCached !== 'true';
    const button = item.querySelector('.track-play-btn');
    const original = button ? button.innerHTML : '';
    if (button && needsDownload) {
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
    }
    if (needsDownload) {
        setSurpriseStatus(`Converting “${item.dataset.title || 'track'}”…`);
    }
    const videoId = item.dataset.videoId;
    const progress = needsDownload && videoId
        ? new EventSource(`/tubio/download_progress/${encodeURIComponent(videoId)}`)
        : null;
    if (progress) {
        progress.onmessage = event => {
            const data = JSON.parse(event.data);
            if (typeof data.percent === 'number') {
                setSurpriseStatus(`Converting “${item.dataset.title || 'track'}”… ${Math.round(data.percent)}%`);
            }
        };
    }
    try {
        return await convertTrackForPlayback(item);
    } catch (error) {
        showNotification(error.message, 'error');
        setSurpriseStatus(error.message);
        return false;
    } finally {
        if (progress) progress.close();
        if (button && needsDownload) {
            button.disabled = false;
            button.innerHTML = original;
        }
    }
}

function getSurpriseTrackItem(index) {
    return getTrackItems().filter(
        track => track.dataset.playlistKind === 'surprise'
    )[index] || null;
}

function startSurpriseTrackPlayback(index) {
    const existingAudio = getAudio();
    const item = getSurpriseTrackItem(index);
    if (!item) return;

    if (
        surpriseMode &&
        index === surpriseCurrentIndex &&
        existingAudio?.dataset.trackKey === item.dataset.trackKey
    ) {
        if (existingAudio.paused) {
            return requestTrackPlayback(item, {
                playlistIndex: null,
                surpriseIndex: index,
            });
        } else {
            pauseCurrentPlayback();
        }
        renderSurprisePlaylist();
        return;
    }
    surpriseMode = true;
    isPlayingPlaylist = false;
    resetAllPlaylistPlayButtons();
    setSurpriseStatus(`Starting “${item.dataset.title || 'track'}”…`);
    renderSurprisePlaylist();
    return requestTrackPlayback(item, {
        playlistIndex: null,
        surpriseIndex: index,
    });
}

function playSurpriseTrack(index) {
    const crc = surpriseCrcs()[index];
    if (crc === undefined) return Promise.resolve(false);

    const item = getSurpriseTrackItem(index);
    if (!item) return Promise.resolve(false);
    if (item.dataset.isCached === 'true') {
        // Keep cached handoffs in the ended/action-handler call stack.
        return startSurpriseTrackPlayback(index);
    }

    return ensureSurpriseTrackCached(crc).then(isCached => {
        if (!isCached) return false;
        return startSurpriseTrackPlayback(index);
    });
}

function toggleSurpriseTrack(trackRef) {
    const item = resolveTrackItem(trackRef);
    const surpriseItems = getTrackItems().filter(
        track => track.dataset.playlistKind === 'surprise'
    );
    const index = surpriseItems.indexOf(item);
    if (index >= 0) playSurpriseTrack(index);
}

function toggleSurprisePlaylistPlayback() {
    const audio = getAudio();
    if (
        surpriseMode &&
        audio &&
        (surpriseCurrentIndex >= 0 || Number.isInteger(pendingPlayback?.surpriseIndex))
    ) {
        togglePlayPause();
        return;
    }
    playSurpriseTrack(0);
}

function renderSurprisePlaylist() {
    const container = document.getElementById('surprise-playlist');
    if (!container) return;
    if (!surprisePlaylist || !surprisePlaylist.html) {
        container.hidden = true;
        return;
    }
    container.innerHTML = surprisePlaylist.html;
    container.hidden = false;
    container.setAttribute('aria-busy', 'false');
    initializeLazyThumbnails();
    initializeTooltips();
    const item = getSurpriseTrackItem(surpriseCurrentIndex);
    const audio = getAudio();
    if (item && audio?.dataset.trackKey === item.dataset.trackKey) {
        const isPlaying = currentTrackKey === item.dataset.trackKey && !audio.paused;
        item.classList.toggle('track-playing', isPlaying);
        setPlayButtonState(item.querySelector('.track-play-btn'), isPlaying);
    }
}

async function loadSurprisePlaylist() {
    const response = await fetch('/tubio/surprise', { headers: { 'Accept': 'application/json' } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not restore Surprise playlist');
    if (surpriseMode && surprisePlaylist && data.playlist) {
        const currentCrc = surpriseCrcs()[surpriseCurrentIndex];
        const restored = normalizeSurprisePlaylist(data.playlist, 'restore-playing');
        const restoredIndex = restored.audio_crcs.findIndex(crc => crc === currentCrc);
        surprisePlaylist = restored;
        surpriseCurrentIndex = restoredIndex;
        if (restoredIndex < 0) {
            const audio = getAudioForCrc(currentCrc);
            if (audio) pauseCurrentPlayback();
            exitSurpriseMode();
        }
        renderSurprisePlaylist();
        return surprisePlaylist;
    }
    if (surpriseMode && surprisePlaylist && !data.playlist) {
        const audio = getAudio();
        if (audio && surpriseCrcs().some(crc => String(crc) === audio.dataset.crc)) {
            pauseCurrentPlayback();
        }
        exitSurpriseMode();
    }
    surprisePlaylist = normalizeSurprisePlaylist(data.playlist, 'restore');
    surpriseCurrentIndex = -1;
    surpriseExhausted = false;
    renderSurprisePlaylist();
    return surprisePlaylist;
}

async function createSurprisePlaylist(seedCrc = null) {
    const fields = seedCrc === null
        ? {}
        : { seed_crc: String(seedCrc) };
    const response = await jsonPost('/tubio/surprise', fields);
    const data = await response.json();
    if (!response.ok || !data.playlist) {
        throw new Error(data.error || (data.empty_reason === 'no_library'
            ? 'Add some songs to your library first to generate a Surprise Playlist.'
            : 'No fresh tracks found right now.'));
    }
    surprisePlaylist = normalizeSurprisePlaylist(
        data.playlist,
        seedCrc === null ? 'create' : 'seeded-create'
    );
    surpriseCurrentIndex = -1;
    surpriseExhausted = false;
    renderSurprisePlaylist();
    return surprisePlaylist;
}

let discoverInitialization = null;

function initializeDiscover() {
    if (discoverInitialization) return discoverInitialization;
    discoverInitialization = (async () => {
        if (!surpriseMode) setSurpriseStatus('Building your Surprise Playlist…');
        try {
            const restored = await loadSurprisePlaylist();
            if (!restored) await createSurprisePlaylist();
            if (!surpriseMode && surprisePlaylist) {
                const count = surpriseCrcs().length;
                setSurpriseStatus(`${count} track${count === 1 ? '' : 's'} ready to play.`);
            }
        } catch (error) {
            console.error('Could not initialize Discover:', error);
            reportTubioClientError('discover-initialize', error);
            if (!surpriseMode) setSurpriseStatus(error.message || 'Could not build a Surprise Playlist.');
        } finally {
            discoverInitialization = null;
        }
    })();
    return discoverInitialization;
}

async function favouriteSurpriseTrack(crc, button) {
    if (!surprisePlaylist) return;
    button.disabled = true;
    try {
        const response = await jsonPost(
            `/tubio/surprise/tracks/${encodeURIComponent(crc)}/favourite`
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not favourite track');
        surprisePlaylist = normalizeSurprisePlaylist(data.playlist, 'favourite');
        renderSurprisePlaylist();
        showNotification('Added to Favourites', 'success');
        updateContent({});
    } catch (error) {
        button.disabled = false;
        showNotification(error.message, 'error');
    }
}

async function saveSurprisePlaylist() {
    if (!surprisePlaylist) return;
    const name = window.prompt('Name this playlist:');
    if (!name || !name.trim()) return;
    try {
        const response = await jsonPost(
            '/tubio/surprise/save',
            { playlist_name: name.trim() }
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not save playlist');
        exitSurpriseMode();
        surprisePlaylist = null;
        renderSurprisePlaylist();
        await updateContent(data);
        switchTab('playlists');
        const slug = data.playlist_name.replace(/ /g, '-').replace(/'/g, '');
        selectPlaylist(slug);
        const skipped = Array.isArray(data.skipped) ? data.skipped.length : 0;
        showNotification(skipped ? `${data.message}; ${skipped} track(s) skipped` : data.message, skipped ? 'info' : 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

async function replaceSurprisePlaylist(
    button,
    {
        seedCrc = null,
        statusMessage = 'Refreshing your Surprise Playlist…',
        buttonMessage = 'Refreshing…',
        successMessage = 'Surprise Playlist refreshed',
    } = {}
) {
    const previousPlaylist = surprisePlaylist;
    const previousCrcs = [...surpriseCrcs()];
    const original = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
        button.innerHTML = `<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>${buttonMessage}</span>`;
    }
    setSurpriseStatus(statusMessage);
    try {
        await createSurprisePlaylist(seedCrc);
        const audio = getAudio();
        if (audio && previousCrcs.some(crc => String(crc) === audio.dataset.crc)) {
            pauseCurrentPlayback();
            audio.removeAttribute('src');
            delete audio.dataset.crc;
            delete audio.dataset.trackKey;
            currentTrackCrc = null;
            currentTrackKey = null;
            pendingPlayback = null;
            updateTrackbar(null);
            updateTrackbarScrubber();
        }
        exitSurpriseMode();
        surpriseCurrentIndex = -1;
        renderSurprisePlaylist();
        const count = surpriseCrcs().length;
        setSurpriseStatus(`${count} track${count === 1 ? '' : 's'} ready to play.`);
        showNotification(successMessage, 'success');
        return true;
    } catch (error) {
        surprisePlaylist = previousPlaylist;
        renderSurprisePlaylist();
        setSurpriseStatus(error.message || 'Could not refresh the Surprise Playlist.');
        reportTubioClientError('surprise-refresh', error);
        showNotification(error.message || 'Could not refresh the Surprise Playlist', 'error');
        return false;
    } finally {
        if (button) {
            button.disabled = false;
            button.innerHTML = original;
        }
    }
}

function refreshSurprisePlaylist(button) {
    return replaceSurprisePlaylist(button);
}

async function suggestMoreFromTrack(button) {
    const item = button?.closest('.playlist-track');
    const seedCrc = item?.dataset.audioCrc;
    if (!seedCrc) {
        showNotification('This track cannot be used for suggestions', 'error');
        return false;
    }

    switchTab('discover', false);
    if (discoverInitialization) {
        try {
            await discoverInitialization;
        } catch (_error) {
            // A seeded replacement below can still succeed if normal restore
            // or generation failed.
        }
    }
    const title = item.dataset.title || 'this track';
    return replaceSurprisePlaylist(button, {
        seedCrc,
        statusMessage: `Finding more tracks like “${title}”…`,
        buttonMessage: 'Suggesting…',
        successMessage: `Built a Surprise Playlist from “${title}”`,
    });
}

function togglePlaylistPlayback(playlistName) {
    // Check if this playlist is currently playing
    if (isPlayingPlaylist && currentPlaylistName === playlistName) {
        const audioElement = getAudio();
        const isCommittedPlaying =
            audioElement &&
            !audioElement.paused &&
            !pendingPlayback &&
            currentTrackKey === audioElement.dataset.trackKey;

        if (isCommittedPlaying) {
            // Pause the playlist
            pausePlaylist();
        } else {
            // Resume the playlist
            resumePlaylist();
        }
    } else {
        // Start playing this playlist
        playAllInPlaylist(playlistName);
    }
}

function pausePlaylist() {
    pauseCurrentPlayback();
}

function resumePlaylist() {
    const pendingIndex = pendingPlayback?.playlistIndex;
    const index = Number.isInteger(pendingIndex)
        ? pendingIndex
        : currentPlaylistIndex;
    return playNextInQueue(index);
}

function updatePlaylistPlayButton(playlistName, isPlaying) {
    const panel = Array.from(
        document.querySelectorAll('.playlist-panel[data-playlist-name]')
    ).find(candidate =>
        candidate.dataset.playlistName === playlistName &&
        candidate.dataset.playlistKind === 'regular'
    );
    const button = panel?.querySelector('.btn-play-all');

    if (button) {
        if (isPlaying) {
            button.innerHTML = '<i class="bi bi-pause-fill"></i>';
            button.title = 'Pause';
        } else {
            button.innerHTML = '<i class="bi bi-play-fill"></i>';
            button.title = 'Play All';
        }
    }
}

function resetAllPlaylistPlayButtons() {
    document.querySelectorAll('.btn-play-all').forEach(button => {
        button.innerHTML = '<i class="bi bi-play-fill"></i>';
        button.title = 'Play All';
    });
}

function playAllInPlaylist(playlistName) {
    if (surpriseMode) exitSurpriseMode();
    const accordion = Array.from(
        document.querySelectorAll('.playlist-accordion[data-playlist-name]')
    ).find(candidate =>
        candidate.dataset.playlistName === playlistName &&
        candidate.dataset.playlistKind === 'regular'
    );
    
    if (!accordion) {
        console.error(`Playlist accordion not found: ${playlistName}`);
        showNotification('Error: Playlist not found', 'error');
        return;
    }
    
    // Get all audio elements in this playlist
    const audioItems = accordion.querySelectorAll('.accordion-item[data-audio-crc]');
    
    if (audioItems.length === 0) {
        showNotification('No songs in this playlist', 'info');
        return;
    }
    
    // Stop any currently playing track first
    const playingAudio = getAudio();
    if (playingAudio && !playingAudio.paused) {
        pauseCurrentPlayback();
    }
    resetAllTrackPlayingUI();

    // Queue exact DOM occurrences, not CRCs. The same file may legitimately
    // appear more than once in one playlist or across several playlists.
    currentPlaylistQueue = Array.from(audioItems).map(item => item.dataset.trackKey);

    if (globalShuffle) {
        currentPlaylistQueue = shuffleArray(currentPlaylistQueue);
    }

    currentPlaylistIndex = 0;
    isPlayingPlaylist = true;
    currentPlaylistName = playlistName;

    // The play-all control stays in its truthful "play" state until the audio
    // element emits `playing`.
    resetAllPlaylistPlayButtons();

    const shuffleText = globalShuffle ? ' (shuffled)' : '';
    showNotification(`Starting ${currentPlaylistQueue.length} songs in "${playlistName}"${shuffleText}`, 'success');

    // Start playing first song
    return playNextInQueue(0);
}

function finishPlaylistPlayback() {
    isPlayingPlaylist = false;
    if (pendingPlayback && Number.isInteger(pendingPlayback.playlistIndex)) {
        pendingPlayback = null;
    }
    resetAllPlaylistPlayButtons();
    updateMediaSessionPlaybackState('none');
    showNotification('Playlist finished', 'info');
}

function playNextInQueue(startIndex = currentPlaylistIndex) {
    if (!isPlayingPlaylist || currentPlaylistQueue.length === 0) {
        finishPlaylistPlayback();
        return Promise.resolve(false);
    }

    let index = startIndex;
    let checked = 0;
    let wrapped = false;

    while (checked < currentPlaylistQueue.length) {
        if (index >= currentPlaylistQueue.length) {
            if (globalLoopMode !== 'playlist') {
                finishPlaylistPlayback();
                return Promise.resolve(false);
            }
            if (globalShuffle) {
                currentPlaylistQueue = shuffleArray(currentPlaylistQueue);
            }
            index = 0;
            wrapped = true;
        }

        const trackItem = resolveTrackItem(currentPlaylistQueue[index]);
        if (trackItem) {
            if (wrapped) showNotification('Looping playlist from beginning', 'info');
            return requestTrackPlayback(trackItem, {
                playlistIndex: index,
                surpriseIndex: null,
            });
        }

        console.error(`Playlist entry not found: ${currentPlaylistQueue[index]}`);
        index++;
        checked++;
    }

    finishPlaylistPlayback();
    return Promise.resolve(false);
}

// Stop playlist playback if user manually interacts with play button
document.addEventListener('click', function(e) {
    // Check if a play button was clicked
    if (e.target.closest('.track-play-btn')) {
        // User manually interacted with a track, stop playlist mode
        if (isPlayingPlaylist) resetAllPlaylistPlayButtons();
        isPlayingPlaylist = false;
    }
}, true);

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `alert alert-${type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info'} alert-dismissible fade show position-fixed`;
    notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
    notification.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    
    document.body.appendChild(notification);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.remove();
        }
    }, 5000);
}

// Sync button UI with audio element state
function syncAudioButtonUI(trackRef) {
    const item = resolveTrackItem(trackRef);
    const audioElement = getAudio();
    const playButton = item?.querySelector('.track-play-btn');

    if (!item || !audioElement || !playButton) {
        return;
    }

    const isLoaded = audioElement.dataset.trackKey === item.dataset.trackKey;
    setPlayButtonState(playButton, isLoaded && !audioElement.paused);
}

// Bind playback listeners ONCE to the single persistent audio element. Handlers
// read audio.dataset.crc so they always act on whatever track is loaded.
function initializeAudioEventListeners() {
    const audio = getAudio();
    if (!audio || audio._listenersBound) {
        if (audio) applyTrackbarVolume(audio);
        return;
    }
    audio._listenersBound = true;

    audio.addEventListener('play', () => {
        applyPlaybackStart(audio);
    });
    audio.addEventListener('playing', () => {
        commitLoadedPlayback(audio);
    });
    audio.addEventListener('pause', () => {
        setCommittedPlaybackUI(false);
        updateMediaSessionPlaybackState('paused');
        updateMediaSessionPositionState();
    });
    audio.addEventListener('timeupdate', () => {
        const bounds = getPlaybackBounds(audio);
        if (!audio.paused && audio.currentTime >= bounds.end && !audio._trimEnded) {
            audio._trimEnded = true;
            audio.pause();
            audio.dispatchEvent(new Event('ended'));
            return;
        }
        updateTrackbarScrubber();
        updateMediaSessionPositionState();
    });
    audio.addEventListener('loadedmetadata', () => {
        audio._metadataReady = true;
        applyPlaybackStart(audio);
        updateTrackbarScrubber();
        updateMediaSessionPositionState();
    });
    audio.addEventListener('ratechange', updateMediaSessionPositionState);
    audio.addEventListener('ended', handleTrackEnded);
    audio.addEventListener('error', () => {
        const mediaError = audio.error;
        const error = new Error(
            mediaError ? `Media error ${mediaError.code}` : 'Unknown media error'
        );
        if (pendingPlayback) {
            handlePlaybackFailure(error, pendingPlayback);
        } else {
            setCommittedPlaybackUI(false);
            updateMediaSessionPlaybackState('paused');
            reportTubioClientError('media-element', error, {
                crc: audio.dataset.crc,
                trackKey: audio.dataset.trackKey,
            });
        }
    });
    applyTrackbarVolume(audio);
}

function updateMediaSessionMetadata(trackRef) {
    if (!('mediaSession' in navigator)) return;
    if (typeof MediaMetadata !== 'function') return;
    const trackItem = resolveTrackItem(trackRef);
    if (!trackItem) return;

    const title = trackItem.dataset.title || 'Unknown Track';
    const artwork = [];
    if (trackItem.dataset.thumbnailUrl) {
        const thumbnailUrl = trackItem.dataset.thumbnailUrl;
        artwork.push({ src: thumbnailUrl, sizes: '512x512', type: 'image/jpeg' });
    }
    try {
        navigator.mediaSession.metadata = new MediaMetadata({
            title,
            album: trackItem.dataset.playlist || '',
            artwork,
        });
    } catch (error) {
        reportTubioClientError('media-session-metadata', error, {
            crc: trackItem.dataset.audioCrc,
            trackKey: trackItem.dataset.trackKey,
        });
    }
}

function updateMediaSessionPlaybackState(state) {
    if ('mediaSession' in navigator) {
        try {
            navigator.mediaSession.playbackState = state;
        } catch (error) {
            // Playback remains functional in partial Media Session
            // implementations that expose but do not accept this property.
        }
    }
}

function updateMediaSessionPositionState() {
    if (!('mediaSession' in navigator)) return;
    if (typeof navigator.mediaSession.setPositionState !== 'function') return;

    const audio = getAudio();
    const loadedTrackIsCommitted =
        audio &&
        currentTrackKey &&
        audio.dataset.trackKey === currentTrackKey;
    if (
        !loadedTrackIsCommitted ||
        !Number.isFinite(audio.duration) ||
        audio.duration <= 0
    ) {
        try {
            navigator.mediaSession.setPositionState();
        } catch (error) {}
        return;
    }

    const bounds = getPlaybackBounds(audio);
    const duration = bounds.end - bounds.start;
    if (!Number.isFinite(duration) || duration <= 0) return;
    const position = Math.min(
        duration,
        Math.max(0, audio.currentTime - bounds.start)
    );
    try {
        navigator.mediaSession.setPositionState({
            duration,
            playbackRate: audio.playbackRate || 1,
            position,
        });
    } catch (error) {
        // Position state is optional and some partial implementations reject
        // valid-looking values. Core playback must remain unaffected.
    }
}

// Trackbar / playback control entry points
function togglePlayPause() {
    const audio = getAudio();
    if (!audio || (!currentTrackKey && !pendingPlayback)) return;
    const isCommittedPlaying =
        !audio.paused &&
        !pendingPlayback &&
        currentTrackKey === audio.dataset.trackKey;
    if (isCommittedPlaying) {
        pauseCurrentPlayback();
        return Promise.resolve(true);
    }
    return resumeLoadedPlayback();
}

function nextTrack() {
    if (surpriseMode) {
        pauseCurrentPlayback();
        playNextSurprise();
        return;
    }
    if (!isPlayingPlaylist || currentPlaylistQueue.length === 0) return;
    const pendingIndex = pendingPlayback?.playlistIndex;
    const fromIndex = Number.isInteger(pendingIndex)
        ? pendingIndex
        : currentPlaylistIndex;
    pauseCurrentPlayback();
    playNextInQueue(fromIndex + 1);
}

function prevTrack() {
    if (surpriseMode) {
        const audio = getAudio();
        const playbackStart = audio ? getPlaybackBounds(audio).start : 0;
        const pendingIndex = pendingPlayback?.surpriseIndex;
        const activeIndex = Number.isInteger(pendingIndex)
            ? pendingIndex
            : surpriseCurrentIndex;
        if (audio && audio.currentTime > playbackStart + 3) {
            audio.currentTime = playbackStart;
        } else if (activeIndex > 0) {
            pauseCurrentPlayback();
            playSurpriseTrack(activeIndex - 1);
        } else if (audio) {
            audio.currentTime = playbackStart;
        }
        return;
    }
    if (isPlayingPlaylist && currentPlaylistQueue.length > 0) {
        const audio = getAudio();
        const playbackStart = audio ? getPlaybackBounds(audio).start : 0;
        const pendingIndex = pendingPlayback?.playlistIndex;
        const activeIndex = Number.isInteger(pendingIndex)
            ? pendingIndex
            : currentPlaylistIndex;
        if (audio && audio.currentTime > playbackStart + 3) {
            audio.currentTime = playbackStart;
        } else if (activeIndex > 0) {
            pauseCurrentPlayback();
            playNextInQueue(activeIndex - 1);
        } else if (audio) {
            audio.currentTime = playbackStart;
        }
    } else {
        if (currentTrackKey) {
            const audio = getAudio();
            if (audio) audio.currentTime = getPlaybackBounds(audio).start;
        }
    }
}

function updateTrackbar(trackRef) {
    const trackbar = document.getElementById('tubio-trackbar');
    const titleEl = document.getElementById('trackbar-title');
    const playlistEl = document.getElementById('trackbar-playlist');
    const thumb = document.getElementById('trackbar-thumb');
    const placeholder = document.getElementById('trackbar-thumb-placeholder');

    if (!trackRef) {
        if (trackbar) trackbar.dataset.active = 'false';
        if (titleEl) titleEl.textContent = 'No track playing';
        if (playlistEl) playlistEl.textContent = '';
        if (thumb) { thumb.hidden = true; thumb.removeAttribute('src'); }
        if (placeholder) placeholder.hidden = false;
        updateTrackbarPlayPauseUI(false);
        updateTrackbarTitleOverflow();
        return;
    }

    const trackItem = resolveTrackItem(trackRef);
    if (!trackItem) return;

    if (trackbar) trackbar.dataset.active = 'true';
    if (titleEl) titleEl.textContent = trackItem.dataset.title || 'Unknown Track';
    if (playlistEl) playlistEl.textContent = trackItem.dataset.playlist || '';
    updateTrackbarTitleOverflow();

    if (trackItem.dataset.thumbnailUrl && thumb) {
        const url = trackItem.dataset.thumbnailUrl;
        if (thumb.src !== url) thumb.src = url;
        thumb.hidden = false;
        if (placeholder) placeholder.hidden = true;
    } else {
        if (thumb) { thumb.hidden = true; thumb.removeAttribute('src'); }
        if (placeholder) placeholder.hidden = false;
    }

    const audio = getAudio();
    const isLoaded = audio?.dataset.trackKey === trackItem.dataset.trackKey;
    updateTrackbarPlayPauseUI(isLoaded && !audio.paused);
}

function updateTrackbarPlayPauseUI(isPlaying) {
    const btn = document.getElementById('trackbar-playpause');
    if (!btn) return;
    btn.innerHTML = isPlaying
        ? '<i class="bi bi-pause-fill"></i>'
        : '<i class="bi bi-play-fill"></i>';
    btn.title = isPlaying ? 'Pause' : 'Play';
}

function updateTrackbarScrubber() {
    const crc = currentTrackCrc;
    const range = document.getElementById('trackbar-scrubber');
    const currEl = document.getElementById('trackbar-time-current');
    const durEl = document.getElementById('trackbar-time-duration');
    if (!range) return;

    if (!crc) {
        range.value = 0;
        range.disabled = true;
        if (currEl) currEl.textContent = '0:00';
        if (durEl) durEl.textContent = '0:00';
        return;
    }

    range.disabled = false;
    const audio = getAudioForCrc(crc);
    if (!audio) return;

    if (audio.duration && isFinite(audio.duration)) {
        const bounds = getPlaybackBounds(audio);
        const playableDuration = bounds.end - bounds.start;
        const playbackTime = Math.max(0, audio.currentTime - bounds.start);
        range.value = playableDuration > 0 ? (playbackTime / playableDuration) * 100 : 0;
        if (currEl) currEl.textContent = formatTime(playbackTime);
        if (durEl) durEl.textContent = formatTime(playableDuration);
    }
}

// Media Session API integration for hardware media keys
function initializeMediaSession() {
    if (!('mediaSession' in navigator)) return;

    function setHandler(action, handler) {
        try {
            navigator.mediaSession.setActionHandler(action, handler);
        } catch (error) {
            // Browsers may expose Media Session while omitting individual
            // actions. Unsupported controls should not disable the player.
        }
    }

    setHandler('play', () => {
        // Always retry the loaded occurrence. In the WebKit failure mode the
        // element can report paused=false without ever reaching `playing`.
        resumeLoadedPlayback();
    });

    setHandler('pause', pauseCurrentPlayback);
    setHandler('nexttrack', nextTrack);
    setHandler('previoustrack', prevTrack);

    setHandler('seekto', details => {
        const audio = getAudio();
        if (!audio || !Number.isFinite(details.seekTime)) return;
        const bounds = getPlaybackBounds(audio);
        const target = Math.min(
            bounds.end,
            Math.max(bounds.start, bounds.start + details.seekTime)
        );
        if (details.fastSeek && typeof audio.fastSeek === 'function') {
            audio.fastSeek(target);
        } else {
            audio.currentTime = target;
        }
        updateTrackbarScrubber();
        updateMediaSessionPositionState();
    });

    updateMediaSessionPlaybackState('none');
}

function getCurrentlyPlayingTrack() {
    return currentTrackCrc;
}

async function resyncTrack(crc, buttonElement) {
    const originalHTML = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Syncing...';

    // Dispose tooltip so it doesn't linger while disabled
    const tooltip = bootstrap.Tooltip.getInstance(buttonElement);
    if (tooltip) tooltip.dispose();

    try {
        const response = await jsonPost(`/tubio/resync/${crc}`);

        const data = await response.json();
        if (response.ok && data.success) {
            showNotification(data.message, 'success');
            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i>Done';
            // Reload the audio element to pick up the new file (only if loaded)
            const audio = getAudioForCrc(crc);
            if (audio) {
                audio._metadataReady = false;
                audio.load();
            }
            setTimeout(() => {
                buttonElement.disabled = false;
                buttonElement.innerHTML = originalHTML;
                new bootstrap.Tooltip(buttonElement);
            }, 2000);
        } else {
            throw new Error(data.error || 'Resync failed');
        }
    } catch (error) {
        console.error('Error resyncing track:', error);
        showNotification(error.message || 'Error resyncing track', 'error');
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalHTML;
        new bootstrap.Tooltip(buttonElement);
    }
}

function openTrimModal(crc, title, trimStart, trimEnd) {
    document.getElementById('trim-audio-crc').value = crc;
    document.getElementById('trim-audio-title').textContent = title;
    document.getElementById('trim-start-seconds').value = String(trimStart);
    document.getElementById('trim-end-seconds').value = String(trimEnd);
    document.getElementById('trim-audio-error').classList.add('d-none');
    bootstrap.Modal.getOrCreateInstance(document.getElementById('trimAudioModal')).show();
}

async function submitAudioTrim(event) {
    event.preventDefault();
    const crc = document.getElementById('trim-audio-crc').value;
    const submitButton = document.getElementById('trim-audio-submit');
    const errorElement = document.getElementById('trim-audio-error');
    const trimStart = Number(document.getElementById('trim-start-seconds').value);
    const trimEnd = Number(document.getElementById('trim-end-seconds').value);
    const audio = getAudioForCrc(crc);
    if (audio && Number.isFinite(audio.duration) && trimStart + trimEnd >= audio.duration) {
        errorElement.textContent = 'The start and end trims must leave some audio to play.';
        errorElement.classList.remove('d-none');
        return;
    }

    const originalHtml = submitButton.innerHTML;
    submitButton.disabled = true;
    submitButton.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Saving...';
    errorElement.classList.add('d-none');

    try {
        const response = await jsonPost(`/tubio/audio/${crc}/trim`, {
            trim_start_s: String(trimStart),
            trim_end_s: String(trimEnd)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not trim audio');

        bootstrap.Modal.getInstance(document.getElementById('trimAudioModal')).hide();
        showNotification(data.message, 'success');
        await updateContent(data);
    } catch (error) {
        errorElement.textContent = error.message;
        errorElement.classList.remove('d-none');
    } finally {
        submitButton.disabled = false;
        submitButton.innerHTML = originalHtml;
    }
}

function initializeTrimForm() {
    const form = document.getElementById('trim-audio-form');
    if (!form || form.dataset.initialized === 'true') return;
    form.addEventListener('submit', submitAudioTrim);
    form.dataset.initialized = 'true';
}

function getPlaybackBounds(audio) {
    const start = Math.max(0, Number(audio.dataset.trimStart) || 0);
    const trimEnd = Math.max(0, Number(audio.dataset.trimEnd) || 0);
    const naturalEnd = Number.isFinite(audio.duration) ? audio.duration : Infinity;
    return { start, end: Math.max(start, naturalEnd - trimEnd) };
}

function applyPlaybackStart(audio, { restart = false } = {}) {
    if (!audio._metadataReady || !Number.isFinite(audio.duration)) {
        audio._restartWhenMetadata = audio._restartWhenMetadata || restart;
        return false;
    }

    const bounds = getPlaybackBounds(audio);
    const shouldRestart = restart || audio._restartWhenMetadata;
    audio._restartWhenMetadata = false;
    if (
        shouldRestart ||
        audio.currentTime < bounds.start ||
        audio.currentTime >= bounds.end
    ) {
        audio.currentTime = bounds.start;
    }
    audio._trimEnded = false;
    return true;
}

function initializeTooltips() {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        if (!bootstrap.Tooltip.getInstance(el)) {
            new bootstrap.Tooltip(el);
        }
    });
}

// Lazy load thumbnails and audio metadata when track accordion is expanded
function initializeLazyThumbnails() {
    document.querySelectorAll('.accordion-collapse').forEach(collapse => {
        collapse.addEventListener('show.bs.collapse', function() {
            const lazyImg = this.querySelector('.lazy-thumbnail[data-src]');
            if (lazyImg && !lazyImg.src) {
                lazyImg.src = lazyImg.dataset.src;
            }
            // Initialize tooltips within this expanded section
            this.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
                if (!bootstrap.Tooltip.getInstance(el)) {
                    new bootstrap.Tooltip(el);
                }
            });
        }, { once: true });
    });
}

document.addEventListener('DOMContentLoaded', function() {
    document.addEventListener('click', function(event) {
        const actionElement = event.target.closest('[data-tubio-action]');
        if (!actionElement) return;

        const action = actionElement.dataset.tubioAction;
        const track = actionElement.closest('.playlist-track');
        const panel = actionElement.closest('.playlist-panel');
        const actions = {
            'switch-tab': () => switchTab(actionElement.dataset.tab),
            'prepare-playlist': () => preparePlaylistModal(),
            'refresh-surprise': () => refreshSurprisePlaylist(actionElement),
            'close-sidebar': () => closeSidebar(),
            'toggle-sidebar': () => toggleSidebar(),
            'select-playlist': () => selectPlaylist(actionElement.dataset.playlistSlug),
            'toggle-surprise-playlist': () => toggleSurprisePlaylistPlayback(),
            'toggle-playlist': () => togglePlaylistPlayback(panel?.dataset.playlistName),
            'save-surprise': () => saveSurprisePlaylist(),
            'toggle-surprise-track': () => toggleSurpriseTrack(track),
            'toggle-track': () => togglePlayTrack(track),
            'suggest-more': () => suggestMoreFromTrack(actionElement),
            'favourite-surprise': () => favouriteSurpriseTrack(track?.dataset.audioCrc, actionElement),
            'resync-track': () => resyncTrack(track?.dataset.audioCrc, actionElement),
            'open-trim': () => openTrimModal(
                track?.dataset.audioCrc,
                track?.dataset.title,
                Number(track?.dataset.trimStart) || 0,
                Number(track?.dataset.trimEnd) || 0
            ),
            'remove-track': () => removeTrack(track?.dataset.audioCrc, actionElement),
            'toggle-shuffle': () => toggleShuffle(),
            'previous-track': () => prevTrack(),
            'toggle-playback': () => togglePlayPause(),
            'next-track': () => nextTrack(),
            'cycle-loop': () => cycleLoopMode(),
        };
        if (!actions[action]) return;
        if (actionElement.matches('a')) event.preventDefault();
        actions[action]();
    });
    document.addEventListener('submit', function(event) {
        const form = event.target.closest('form[data-confirm]');
        if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
    });

    initializeMediaSession();
    initializeAudioEventListeners();
    initializeLazyThumbnails();
    initializeTooltips();
    initializeSidebar();
    initializeTrackbarVolume();
    initializeTrackbarVolumePopover();
    initializeTrimForm();
    updateTrackbar(null);
    updateTrackbarScrubber();

    const trackbarScrubber = document.getElementById('trackbar-scrubber');
    if (trackbarScrubber) {
        trackbarScrubber.addEventListener('input', function() {
            if (currentTrackCrc) seekTrack(currentTrackCrc, this.value);
        });
    }

    const trackbarVolume = document.getElementById('trackbar-volume');
    if (trackbarVolume) {
        trackbarVolume.addEventListener('input', function() {
            setTrackbarVolume(this.value);
        });
    }

    window.addEventListener('resize', updateTrackbarTitleOverflow);
});

// Playlist management functions
function getSelectedSongs() {
    const checkboxes = document.querySelectorAll('.song-checkbox:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

function preparePlaylistModal() {
    const selectedSongs = getSelectedSongs();
    const count = selectedSongs.length;

    const movePlaylistInput = document.getElementById('move_playlist_tracks_crcs');
    if (movePlaylistInput) movePlaylistInput.value = selectedSongs.join(',');

    const movePlaylistBtn = document.querySelector('#move-playlist-form button[type="submit"]');
    if (movePlaylistBtn) movePlaylistBtn.disabled = (count === 0);
}
