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

// Point the single audio element at a track (by crc), reading src + trim bounds
// from the track's accordion item. No-op reload if it already holds this crc.
// Returns the audio element, or null if the track isn't in the DOM.
function loadTrack(crc, { force = false } = {}) {
    const audio = getAudio();
    if (!audio) return null;
    const item = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
    if (!item) return null;
    if (force || audio.dataset.crc !== String(crc)) {
        audio.dataset.crc = String(crc);
        audio.dataset.trimStart = item.dataset.trimStart || '0';
        audio.dataset.trimEnd = item.dataset.trimEnd || '0';
        audio._trimEnded = false;
        audio.src = item.dataset.audioSrc;
        audio.load();
        applyTrackbarVolume(audio);
    }
    return audio;
}

// Unified 'ended' handler for the single audio element. Behavior depends on the
// global loop mode and whether a playlist is driving playback.
function handleTrackEnded() {
    const crc = currentTrackCrc;
    const audio = getAudio();

    if (globalLoopMode === 'single' && audio) {
        audio.currentTime = getPlaybackBounds(audio).start;
        audio._trimEnded = false;
        audio.play().catch(err => console.error('Error replaying audio:', err));
        return;
    }

    if (surpriseMode) {
        setTimeout(() => playNextSurprise(), 500);
        return;
    }

    if (crc) resetTrackPlayingUI(crc);

    if (isPlayingPlaylist) {
        currentPlaylistIndex++;
        setTimeout(() => playNextInQueue(), 500);
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

function switchTab(tabName) {
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

    if (tabName === 'discover') {
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
            const statusText = status === 'downloading' ? 'Downloading...' :
                               status === 'processing' ? 'Processing audio...' :
                               status === 'complete' ? 'Complete!' : status;
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

            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i>Downloaded';
            buttonElement.style.backgroundColor = '#adb5bd';
            buttonElement.style.borderColor = '#adb5bd';
            setTimeout(hideProgress, 1500);
        } else {
            throw new Error(data.error || 'Download failed');
        }

    } catch (error) {
        if (eventSource) eventSource.close();
        console.error('Error downloading video:', error);
        showNotification(error.message || 'Error downloading video', 'error');
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
                const loadedItem = audio && audio.dataset.crc
                    ? document.querySelector(`.accordion-item[data-audio-crc="${audio.dataset.crc}"]`)
                    : null;
                if (loadedItem) {
                    const crc = audio.dataset.crc;
                    // Trim bounds may have changed in the re-rendered DOM.
                    audio.dataset.trimStart = loadedItem.dataset.trimStart || '0';
                    audio.dataset.trimEnd = loadedItem.dataset.trimEnd || '0';
                    syncAudioButtonUI(crc);
                    if (!audio.paused) {
                        const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
                        if (trackItem) trackItem.classList.add('track-playing');
                    }
                    updateTrackbar(crc);
                    updateTrackbarScrubber();
                } else {
                    updateTrackbar(currentTrackCrc);
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
        if (audio && !audio.paused) audio.pause();

        const response = await jsonPost(`/tubio/delete_audio/${crc}`);

        if (!response.ok) throw new Error('Failed to remove track');

        document.querySelectorAll(`.accordion-item[data-audio-crc="${crc}"]`).forEach(el => el.remove());
        if (String(currentTrackCrc) === String(crc)) {
            const player = getAudio();
            if (player) {
                player.pause();
                player.removeAttribute('src');
                delete player.dataset.crc;
            }
            currentTrackCrc = null;
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
function togglePlayTrack(crc) {
    if (surpriseMode) exitSurpriseMode();
    const playButton = document.getElementById(`play-btn-${crc}`);
    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
    const loaded = getAudioForCrc(crc);

    // Currently playing this exact track → pause it.
    if (loaded && !loaded.paused) {
        loaded.pause();
        setPlayButtonState(playButton, false);
        return;
    }

    // Switching to a different track clears the previous track's row UI.
    if (currentTrackCrc && String(currentTrackCrc) !== String(crc)) {
        resetTrackPlayingUI(currentTrackCrc);
    }

    if (trackItem && trackItem.dataset.playlist) {
        currentPlaylistName = trackItem.dataset.playlist;
    }

    const audio = loadTrack(crc);
    if (!audio) {
        console.error(`Track not found for crc: ${crc}`);
        return;
    }
    currentTrackCrc = crc;
    applyPlaybackStart(audio);
    audio.play().catch(err => {
        console.error('Error playing audio:', err);
        showNotification('Error playing audio. Please try again.', 'error');
    });
    setPlayButtonState(playButton, true);
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
        }, { once: true });
        audio.load();
    } else if (audio.duration && isFinite(audio.duration)) {
        const bounds = getPlaybackBounds(audio);
        audio.currentTime = bounds.start + ((value / 100) * (bounds.end - bounds.start));
    }
}

// Currently active track CRC (the last track the user interacted with)
let currentTrackCrc = null;

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
        }
    } finally {
        surpriseFilling = false;
    }
}

async function playNextSurprise() {
    if (!surpriseMode || !surprisePlaylist) return;
    const nextIndex = surpriseCurrentIndex + 1;
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

async function ensureSurpriseTrackCached(crc) {
    const item = document.querySelector(`.playlist-track[data-playlist-kind="surprise"][data-audio-crc="${crc}"]`);
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
        setSurpriseStatus(`Downloading “${item.dataset.title || 'track'}”…`);
    }
    try {
        while (true) {
            const request = jsonPost(`/tubio/audio/${encodeURIComponent(crc)}/cache`);
            const videoId = item.dataset.videoId;
            const progress = needsDownload && videoId
                ? new EventSource(`/tubio/download_progress/${encodeURIComponent(videoId)}`)
                : null;
            if (progress) {
                progress.onmessage = event => {
                    const data = JSON.parse(event.data);
                    if (typeof data.percent === 'number') {
                        setSurpriseStatus(`Downloading “${item.dataset.title || 'track'}”… ${Math.round(data.percent)}%`);
                    }
                };
            }
            const response = await request;
            const data = await response.json();
            if (progress) progress.close();
            if (response.status === 202) {
                await sleep(surpriseCachePollIntervalMs());
                continue;
            }
            if (!response.ok || !data.is_cached) {
                throw new Error(data.error || 'Could not download this track');
            }
            item.dataset.isCached = 'true';
            item.querySelector('.track-cache-badge')?.remove();
            return true;
        }
    } catch (error) {
        showNotification(error.message, 'error');
        setSurpriseStatus(error.message);
        return false;
    } finally {
        if (button && needsDownload) {
            button.disabled = false;
            button.innerHTML = original;
        }
    }
}

async function playSurpriseTrack(index) {
    const crc = surpriseCrcs()[index];
    if (crc === undefined) return;
    if (!await ensureSurpriseTrackCached(crc)) return;
    const existingAudio = getAudio();
    if (surpriseMode && index === surpriseCurrentIndex && existingAudio) {
        if (existingAudio.paused) {
            existingAudio.play().catch(err => console.error('Error playing audio:', err));
        } else {
            existingAudio.pause();
        }
        renderSurprisePlaylist();
        return;
    }
    surpriseMode = true;
    isPlayingPlaylist = false;
    surpriseCurrentIndex = index;
    if (currentTrackCrc && String(currentTrackCrc) !== String(crc)) {
        resetTrackPlayingUI(currentTrackCrc);
    }
    const audio = loadTrack(crc);
    if (!audio) return;
    currentTrackCrc = String(crc);
    audio.play().catch(err => {
        console.error('Error playing surprise track:', err);
        showNotification('Could not play this track', 'error');
    });
    const item = document.querySelector(`.playlist-track[data-playlist-kind="surprise"][data-audio-crc="${crc}"]`);
    setSurpriseStatus(`Surprise Playlist · now playing “${item?.dataset.title || 'track'}”`);
    renderSurprisePlaylist();
    fillSurpriseBuffer();
}

function toggleSurpriseTrack(crc) {
    const index = surpriseCrcs().findIndex(value => String(value) === String(crc));
    if (index >= 0) playSurpriseTrack(index);
}

function toggleSurprisePlaylistPlayback() {
    const audio = getAudio();
    if (surpriseMode && surpriseCurrentIndex >= 0 && audio) {
        playSurpriseTrack(surpriseCurrentIndex);
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
    const crc = surpriseCrcs()[surpriseCurrentIndex];
    const audio = getAudioForCrc(crc);
    if (crc !== undefined && audio) {
        const item = document.querySelector(`.playlist-track[data-playlist-kind="surprise"][data-audio-crc="${crc}"]`);
        if (item && !audio.paused) item.classList.add('track-playing');
        setPlayButtonState(item?.querySelector('.track-play-btn'), !audio.paused);
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
            if (audio) audio.pause();
            exitSurpriseMode();
        }
        renderSurprisePlaylist();
        return surprisePlaylist;
    }
    if (surpriseMode && surprisePlaylist && !data.playlist) {
        const audio = getAudio();
        if (audio && surpriseCrcs().some(crc => String(crc) === audio.dataset.crc)) {
            audio.pause();
        }
        exitSurpriseMode();
    }
    surprisePlaylist = normalizeSurprisePlaylist(data.playlist, 'restore');
    surpriseCurrentIndex = -1;
    surpriseExhausted = false;
    renderSurprisePlaylist();
    return surprisePlaylist;
}

async function createSurprisePlaylist() {
    const response = await jsonPost('/tubio/surprise');
    const data = await response.json();
    if (!response.ok || !data.playlist) {
        throw new Error(data.error || (data.empty_reason === 'no_library'
            ? 'Add some songs to your library first to generate a Surprise Playlist.'
            : 'No fresh tracks found right now.'));
    }
    surprisePlaylist = normalizeSurprisePlaylist(data.playlist, 'create');
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

async function refreshSurprisePlaylist(button) {
    const previousPlaylist = surprisePlaylist;
    const previousCrcs = [...surpriseCrcs()];
    const original = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Refreshing…</span>';
    }
    setSurpriseStatus('Refreshing your Surprise Playlist…');
    try {
        await createSurprisePlaylist();
        const audio = getAudio();
        if (audio && previousCrcs.some(crc => String(crc) === audio.dataset.crc)) {
            audio.pause();
            audio.removeAttribute('src');
            delete audio.dataset.crc;
            currentTrackCrc = null;
            updateTrackbar(null);
            updateTrackbarScrubber();
        }
        exitSurpriseMode();
        surpriseCurrentIndex = -1;
        renderSurprisePlaylist();
        const count = surpriseCrcs().length;
        setSurpriseStatus(`${count} track${count === 1 ? '' : 's'} ready to play.`);
        showNotification('Surprise Playlist refreshed', 'success');
    } catch (error) {
        surprisePlaylist = previousPlaylist;
        renderSurprisePlaylist();
        setSurpriseStatus(error.message || 'Could not refresh the Surprise Playlist.');
        reportTubioClientError('surprise-refresh', error);
        showNotification(error.message || 'Could not refresh the Surprise Playlist', 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.innerHTML = original;
        }
    }
}

function togglePlaylistPlayback(playlistName) {
    // Check if this playlist is currently playing
    if (isPlayingPlaylist && currentPlaylistName === playlistName) {
        // Find the currently playing audio
        const crc = currentPlaylistQueue[currentPlaylistIndex];
        const audioElement = getAudioForCrc(crc);

        if (audioElement && !audioElement.paused) {
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
    const crc = currentPlaylistQueue[currentPlaylistIndex];
    const audioElement = getAudioForCrc(crc);
    const playButton = document.getElementById(`play-btn-${crc}`);

    if (audioElement) {
        audioElement.pause();
    }

    if (playButton) {
        setPlayButtonState(playButton, false);
    }

    // Update playlist play button to show play icon
    updatePlaylistPlayButton(currentPlaylistName, false);
}

function resumePlaylist() {
    const crc = currentPlaylistQueue[currentPlaylistIndex];
    const audioElement = loadTrack(crc);
    const playButton = document.getElementById(`play-btn-${crc}`);

    if (audioElement) {
        currentTrackCrc = crc;
        applyPlaybackStart(audioElement);
        audioElement.play().catch(err => {
            console.error('Error resuming audio:', err);
            showNotification('Error resuming playback', 'error');
        });
    }

    if (playButton) {
        setPlayButtonState(playButton, true);
    }

    // Update playlist play button to show pause icon
    updatePlaylistPlayButton(currentPlaylistName, true);
}

function updatePlaylistPlayButton(playlistName, isPlaying) {
    const buttonId = `play-all-btn-${playlistName.replace(/ /g, '-').replace(/'/g, '')}`;
    const button = document.getElementById(buttonId);

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
    // Find all audio items in this playlist
    const accordionId = `audioAccordion-${playlistName.replace(/ /g, '-')}`;
    const accordion = document.getElementById(accordionId);
    
    if (!accordion) {
        console.error(`Playlist accordion not found: ${accordionId}`);
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
        playingAudio.pause();
    }
    if (currentTrackCrc) {
        resetTrackPlayingUI(currentTrackCrc);
    }

    // Build queue of audio CRCs
    currentPlaylistQueue = Array.from(audioItems).map(item => item.dataset.audioCrc);

    if (globalShuffle) {
        currentPlaylistQueue = shuffleArray(currentPlaylistQueue);
    }

    currentPlaylistIndex = 0;
    isPlayingPlaylist = true;
    currentPlaylistName = playlistName;

    // Reset all playlist play buttons, then set this one to pause
    resetAllPlaylistPlayButtons();
    updatePlaylistPlayButton(playlistName, true);

    const shuffleText = globalShuffle ? ' (shuffled)' : '';
    showNotification(`Playing all ${currentPlaylistQueue.length} songs in "${playlistName}"${shuffleText}`, 'success');

    // Start playing first song
    playNextInQueue();
}

function playNextInQueue() {
    if (!isPlayingPlaylist || currentPlaylistIndex >= currentPlaylistQueue.length) {
        // Check if we should loop the playlist
        if (globalLoopMode === 'playlist' && currentPlaylistQueue.length > 0) {
            if (globalShuffle) {
                currentPlaylistQueue = shuffleArray(currentPlaylistQueue);
            }
            currentPlaylistIndex = 0;
            showNotification('Looping playlist from beginning', 'info');
        } else {
            // Playlist finished
            isPlayingPlaylist = false;
            resetAllPlaylistPlayButtons();
            showNotification('Playlist finished', 'info');
            return;
        }
    }
    
    const crc = currentPlaylistQueue[currentPlaylistIndex];
    const playButton = document.getElementById(`play-btn-${crc}`);
    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);

    const audioElement = loadTrack(crc);
    if (!audioElement || !trackItem) {
        console.error(`Track not found for crc: ${crc}`);
        currentPlaylistIndex++;
        playNextInQueue();
        return;
    }
    currentTrackCrc = crc;

    // Scroll to the song
    trackItem.scrollIntoView({ behavior: 'smooth', block: 'center' });

    applyPlaybackStart(audioElement);
    audioElement.play().catch(err => {
        console.error('Error playing audio:', err);
        currentPlaylistIndex++;
        playNextInQueue();
    });

    // Update button to show pause icon (same as togglePlayTrack)
    if (playButton) {
        setPlayButtonState(playButton, true);
    }

    // Highlight this track (same as togglePlayTrack)
    trackItem.classList.add('track-playing');
    // Advancement to the next track is handled by the single element's
    // persistent 'ended' listener (handleTrackEnded).
}

// Stop playlist playback if user manually interacts with play button
document.addEventListener('click', function(e) {
    // Check if a play button was clicked
    if (e.target.closest('.track-play-btn')) {
        // User manually interacted with a track, stop playlist mode
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
function syncAudioButtonUI(crc) {
    const audioElement = getAudioForCrc(crc);
    const playButton = document.getElementById(`play-btn-${crc}`);

    if (!audioElement || !playButton) {
        return;
    }

    setPlayButtonState(playButton, !audioElement.paused);
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
        const crc = audio.dataset.crc;
        applyPlaybackStart(audio);
        currentTrackCrc = crc;
        syncAudioButtonUI(crc);
        updateMediaSessionMetadata(crc);
        updateMediaSessionPlaybackState('playing');
        updateTrackbar(crc);
        if (surpriseMode) renderSurprisePlaylist();
    });
    audio.addEventListener('pause', () => {
        const crc = audio.dataset.crc;
        syncAudioButtonUI(crc);
        updateMediaSessionPlaybackState('paused');
        if (crc === currentTrackCrc) updateTrackbarPlayPauseUI(false);
        if (surpriseMode) renderSurprisePlaylist();
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
    });
    audio.addEventListener('loadedmetadata', () => updateTrackbarScrubber());
    audio.addEventListener('ended', handleTrackEnded);
    applyTrackbarVolume(audio);
}

function updateMediaSessionMetadata(crc) {
    if (!('mediaSession' in navigator)) return;
    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
    if (!trackItem) return;

    const title = trackItem.dataset.title || 'Unknown Track';
    const artwork = [];
    if (trackItem.dataset.thumbnailUrl) {
        const thumbnailUrl = trackItem.dataset.thumbnailUrl;
        artwork.push({ src: thumbnailUrl, sizes: '512x512', type: 'image/jpeg' });
    }
    navigator.mediaSession.metadata = new MediaMetadata({ title, artwork });
}

function updateMediaSessionPlaybackState(state) {
    if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = state;
    }
}

// Trackbar / playback control entry points
function togglePlayPause() {
    if (surpriseMode && surpriseCurrentIndex >= 0) {
        playSurpriseTrack(surpriseCurrentIndex);
        return;
    }
    const crc = currentTrackCrc;
    if (!crc) return;
    const audio = loadTrack(crc);
    if (!audio) return;
    if (audio.paused) {
        applyPlaybackStart(audio);
        audio.play().catch(err => console.error('Error playing audio:', err));
    } else {
        audio.pause();
    }
}

function resetTrackPlayingUI(crc) {
    const button = document.getElementById(`play-btn-${crc}`);
    const item = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
    setPlayButtonState(button, false);
    if (item) item.classList.remove('track-playing');
}

function nextTrack() {
    if (surpriseMode) {
        const audio = getAudio();
        if (audio) audio.pause();
        playNextSurprise();
        return;
    }
    if (!isPlayingPlaylist || currentPlaylistQueue.length === 0) return;
    const oldCrc = currentPlaylistQueue[currentPlaylistIndex];
    const oldAudio = getAudioForCrc(oldCrc);
    if (oldAudio) oldAudio.pause();
    resetTrackPlayingUI(oldCrc);
    currentPlaylistIndex++;
    playNextInQueue();
}

function prevTrack() {
    if (surpriseMode) {
        const audio = getAudio();
        if (audio && audio.currentTime > 3) {
            audio.currentTime = 0;
        } else if (surpriseCurrentIndex > 0) {
            if (audio) audio.pause();
            playSurpriseTrack(surpriseCurrentIndex - 1);
        } else if (audio) {
            audio.currentTime = 0;
        }
        return;
    }
    if (isPlayingPlaylist && currentPlaylistQueue.length > 0) {
        const crc = currentPlaylistQueue[currentPlaylistIndex];
        const audio = getAudioForCrc(crc);
        const playbackStart = audio ? getPlaybackBounds(audio).start : 0;
        if (audio && audio.currentTime > playbackStart + 3) {
            audio.currentTime = playbackStart;
        } else if (currentPlaylistIndex > 0) {
            if (audio) audio.pause();
            resetTrackPlayingUI(crc);
            currentPlaylistIndex--;
            playNextInQueue();
        } else if (audio) {
            audio.currentTime = playbackStart;
        }
    } else {
        const crc = currentTrackCrc;
        if (crc) {
            const audio = getAudioForCrc(crc);
            if (audio) audio.currentTime = getPlaybackBounds(audio).start;
        }
    }
}

function updateTrackbar(crc) {
    const trackbar = document.getElementById('tubio-trackbar');
    const titleEl = document.getElementById('trackbar-title');
    const playlistEl = document.getElementById('trackbar-playlist');
    const thumb = document.getElementById('trackbar-thumb');
    const placeholder = document.getElementById('trackbar-thumb-placeholder');

    if (!crc) {
        if (trackbar) trackbar.dataset.active = 'false';
        if (titleEl) titleEl.textContent = 'No track playing';
        if (playlistEl) playlistEl.textContent = '';
        if (thumb) { thumb.hidden = true; thumb.removeAttribute('src'); }
        if (placeholder) placeholder.hidden = false;
        updateTrackbarPlayPauseUI(false);
        updateTrackbarTitleOverflow();
        return;
    }

    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
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

    const audio = getAudioForCrc(crc);
    updateTrackbarPlayPauseUI(audio ? !audio.paused : false);
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

    navigator.mediaSession.setActionHandler('play', () => {
        const crc = getCurrentlyPlayingTrack();
        if (crc) {
            const audio = loadTrack(crc);
            if (audio && audio.paused) {
                applyPlaybackStart(audio);
                audio.play();
                updateMediaSessionPlaybackState('playing');
            }
        }
    });

    navigator.mediaSession.setActionHandler('pause', () => {
        const crc = getCurrentlyPlayingTrack();
        if (crc) {
            const audio = getAudioForCrc(crc);
            if (audio && !audio.paused) {
                audio.pause();
                updateMediaSessionPlaybackState('paused');
            }
        }
    });

    navigator.mediaSession.setActionHandler('nexttrack', () => nextTrack());

    navigator.mediaSession.setActionHandler('previoustrack', () => {
        if (isPlayingPlaylist && currentPlaylistQueue.length > 0) {
            prevTrack();
        } else {
            const crc = getCurrentlyPlayingTrack();
            if (crc) {
                const audio = getAudioForCrc(crc);
                if (audio) audio.currentTime = getPlaybackBounds(audio).start;
            }
        }
    });
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

function applyPlaybackStart(audio) {
    if (!Number.isFinite(audio.duration)) {
        if (!audio._trimWaitingForMetadata) {
            audio._trimWaitingForMetadata = true;
            audio.addEventListener('loadedmetadata', () => {
                audio._trimWaitingForMetadata = false;
                applyPlaybackStart(audio);
                audio.play().catch(err => console.error('Error starting trimmed audio:', err));
            }, { once: true });
        }
        audio.pause();
        return false;
    }

    const bounds = getPlaybackBounds(audio);
    if (audio.currentTime < bounds.start || audio.currentTime >= bounds.end) {
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
