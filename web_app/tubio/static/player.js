(() => {
    'use strict';

    const Tubio = window.Tubio = window.Tubio || {};
    const playerState = {
        current: { crc: null, key: null },
        pending: null,
        attemptSequence: 0,
        queue: { active: false, keys: [], index: 0, name: '' },
        surprise: {
            mode: false,
            payload: null,
            index: -1,
            exhausted: false,
            filling: false,
            initializing: null,
        },
        shuffle: false,
        loop: 'off',
        volume: { percent: null, muted: false },
        conversions: new Map(),
        prefetches: new Map(),
        prefetchedSources: new Set(),
        initialized: false,
    };

    const api = () => Tubio.api;
    const ui = () => Tubio.ui || {};
    const notify = (message, type = 'info') => ui().notify?.(message, type);
    const reportError = (scope, error, context = {}) => {
        api()?.reportError?.(scope, error, context);
    };

    function audioElement() {
        return document.getElementById('tubio-audio');
    }

    function trackItems() {
        return Array.from(
            document.querySelectorAll('.playlist-track[data-track-key]')
        );
    }

    function loadedTrackItem() {
        const audio = audioElement();
        if (!audio?.dataset.trackKey) return null;
        return trackItems().find(
            item => item.dataset.trackKey === audio.dataset.trackKey
        ) || null;
    }

    function resolveTrackItem(reference) {
        if (reference?.matches?.('.playlist-track')) return reference;
        const value = String(reference ?? '');
        const exact = trackItems().find(item => item.dataset.trackKey === value);
        if (exact) return exact;
        const loaded = loadedTrackItem();
        if (loaded?.dataset.audioCrc === value) return loaded;
        return trackItems().find(item => item.dataset.audioCrc === value) || null;
    }

    function audioForCrc(crc) {
        const audio = audioElement();
        return audio?.dataset.crc === String(crc) ? audio : null;
    }

    function loadTrack(reference, { force = false } = {}) {
        const audio = audioElement();
        const item = resolveTrackItem(reference);
        if (!audio || !item) return null;

        const crc = String(item.dataset.audioCrc);
        const sourceChanged = force
            || audio.dataset.crc !== crc
            || audio.getAttribute('src') !== item.dataset.audioSrc;
        Object.assign(audio.dataset, {
            trackKey: item.dataset.trackKey,
            crc,
            playlist: item.dataset.playlist || '',
            playlistKind: item.dataset.playlistKind || '',
            trimStart: item.dataset.trimStart || '0',
            trimEnd: item.dataset.trimEnd || '0',
        });
        audio._trimEnded = false;
        if (sourceChanged) {
            audio._metadataReady = false;
            audio.src = item.dataset.audioSrc;
            applyVolume(audio);
        }
        return audio;
    }

    function playbackBounds(audio) {
        const start = Math.max(0, Number(audio.dataset.trimStart) || 0);
        const trimEnd = Math.max(0, Number(audio.dataset.trimEnd) || 0);
        const naturalEnd = Number.isFinite(audio.duration)
            ? audio.duration
            : Infinity;
        return { start, end: Math.max(start, naturalEnd - trimEnd) };
    }

    function applyPlaybackStart(audio, { restart = false } = {}) {
        if (!audio._metadataReady || !Number.isFinite(audio.duration)) {
            audio._restartWhenMetadata = audio._restartWhenMetadata || restart;
            return false;
        }
        const bounds = playbackBounds(audio);
        const shouldRestart = restart || audio._restartWhenMetadata;
        audio._restartWhenMetadata = false;
        if (
            shouldRestart
            || audio.currentTime < bounds.start
            || audio.currentTime >= bounds.end
        ) {
            audio.currentTime = bounds.start;
        }
        audio._trimEnded = false;
        return true;
    }

    function setTrackButton(button, isPlaying) {
        if (!button) return;
        button.innerHTML = isPlaying
            ? '<i class="bi bi-pause-fill"></i>'
            : '<i class="bi bi-play-fill"></i>';
        button.classList.toggle('btn-success', isPlaying);
        button.classList.toggle('btn-outline-primary', !isPlaying);
    }

    function resetTrackButtons() {
        trackItems().forEach(item => {
            item.classList.remove('track-playing');
            setTrackButton(item.querySelector('.track-play-btn'), false);
        });
    }

    function setPlaylistButton(playlistName, isPlaying) {
        const panel = Array.from(document.querySelectorAll(
            '.playlist-panel[data-playlist-name][data-playlist-kind="regular"]'
        )).find(item => item.dataset.playlistName === playlistName);
        const button = panel?.querySelector('.btn-play-all');
        if (!button) return;
        button.innerHTML = isPlaying
            ? '<i class="bi bi-pause-fill"></i>'
            : '<i class="bi bi-play-fill"></i>';
        button.title = isPlaying ? 'Pause' : 'Play All';
    }

    function resetPlaylistButtons() {
        document.querySelectorAll('.btn-play-all').forEach(button => {
            button.innerHTML = '<i class="bi bi-play-fill"></i>';
            button.title = 'Play All';
        });
    }

    function loadedContext() {
        const audio = audioElement();
        if (
            playerState.pending
            && audio?.dataset.trackKey === playerState.pending.trackKey
        ) {
            return playerState.pending;
        }
        if (playerState.surprise.mode && playerState.surprise.index >= 0) {
            return { playlistIndex: null, surpriseIndex: playerState.surprise.index };
        }
        if (playerState.queue.active) {
            return { playlistIndex: playerState.queue.index, surpriseIndex: null };
        }
        return { playlistIndex: null, surpriseIndex: null };
    }

    function setCommittedUi(isPlaying) {
        const item = resolveTrackItem(playerState.current.key);
        if (item) {
            item.classList.toggle('track-playing', isPlaying);
            setTrackButton(item.querySelector('.track-play-btn'), isPlaying);
        }
        updateTrackbarPlayButton(isPlaying);
        if (playerState.queue.active && playerState.queue.name) {
            setPlaylistButton(playerState.queue.name, isPlaying);
        }
        if (playerState.surprise.mode) renderSurprise();
    }

    function commitPlayback(audio) {
        const item = loadedTrackItem();
        if (!item || audio.paused) return false;
        const context = playerState.pending?.trackKey === item.dataset.trackKey
            ? playerState.pending
            : loadedContext();

        resetTrackButtons();
        playerState.current.key = item.dataset.trackKey;
        playerState.current.crc = item.dataset.audioCrc;
        if (Number.isInteger(context.playlistIndex)) {
            playerState.queue.index = context.playlistIndex;
            playerState.queue.name = item.dataset.playlist || playerState.queue.name;
            playerState.queue.active = true;
            playerState.surprise.mode = false;
        } else if (Number.isInteger(context.surpriseIndex)) {
            playerState.surprise.index = context.surpriseIndex;
            playerState.surprise.mode = true;
            playerState.queue.active = false;
            setSurpriseStatus(
                `Surprise Playlist · now playing “${item.dataset.title || 'track'}”`
            );
            fillSurpriseBuffer();
        }
        playerState.pending = null;
        setCommittedUi(true);
        updateTrackbar(item);
        updateScrubber();
        updateMediaMetadata(item);
        updateMediaPlaybackState('playing');
        updateMediaPosition();
        prefetchNextTrack();
        if (document.visibilityState === 'visible') {
            item.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        return true;
    }

    function handlePlaybackFailure(error, attempt) {
        const audio = audioElement();
        if (!attempt || attempt.attemptId !== playerState.attemptSequence) {
            return false;
        }
        if (!audio || audio.dataset.trackKey !== attempt.trackKey) return false;
        attempt.failed = true;
        playerState.pending = attempt;
        if (!audio.paused) audio.pause();
        setCommittedUi(false);
        updateMediaPlaybackState('paused');
        updateMediaPosition();
        reportError('playback-request', error, {
            crc: attempt.crc,
            trackKey: attempt.trackKey,
            playlistIndex: attempt.playlistIndex,
            surpriseIndex: attempt.surpriseIndex,
            visibility: document.visibilityState,
        });
        if (document.visibilityState === 'visible') {
            notify('Playback could not start. Press play to retry.', 'error');
        }
        return false;
    }

    function requestPlayback(
        reference,
        context = { playlistIndex: null, surpriseIndex: null },
        { restart = false } = {},
    ) {
        const item = resolveTrackItem(reference);
        if (!item) return Promise.resolve(false);
        const attempt = {
            attemptId: ++playerState.attemptSequence,
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
        playerState.pending = attempt;
        if (
            playerState.current.key
            && playerState.current.key !== attempt.trackKey
        ) {
            setCommittedUi(false);
        }

        const audio = loadTrack(item);
        if (!audio) return Promise.resolve(false);
        applyPlaybackStart(audio, { restart });
        updateMediaPlaybackState('paused');

        let playResult;
        try {
            // Keep play() synchronous in action/ended stacks for iOS/WebKit.
            playResult = audio.play();
        } catch (error) {
            return Promise.resolve(handlePlaybackFailure(error, attempt));
        }
        return Promise.resolve(playResult).then(
            () => {
                if (attempt.attemptId !== playerState.attemptSequence) return false;
                if (audio.paused) {
                    return handlePlaybackFailure(
                        new Error('The media element remained paused after play() resolved.'),
                        attempt,
                    );
                }
                return true;
            },
            error => handlePlaybackFailure(error, attempt),
        ).catch(error => {
            console.error('Unexpected error while settling playback:', error);
            setCommittedUi(false);
            updateMediaPlaybackState('paused');
            return false;
        });
    }

    function pausePlayback() {
        playerState.attemptSequence += 1;
        const audio = audioElement();
        if (audio && !audio.paused) audio.pause();
        setCommittedUi(false);
        updateMediaPlaybackState('paused');
        updateMediaPosition();
    }

    function resumePlayback() {
        const item = loadedTrackItem() || resolveTrackItem(playerState.current.key);
        if (!item) return Promise.resolve(false);
        const audio = audioElement();
        if (playerState.pending && audio && !audio.paused) audio.pause();
        return requestPlayback(item, loadedContext());
    }

    function toggleTrack(reference) {
        exitSurpriseMode();
        const item = resolveTrackItem(reference);
        if (!item) return Promise.resolve(false);
        const loaded = audioForCrc(item.dataset.audioCrc);
        if (loaded && !loaded.paused) {
            if (
                playerState.current.key === item.dataset.trackKey
                && !playerState.pending
            ) {
                pausePlayback();
                return Promise.resolve(true);
            }
            pausePlayback();
        }
        playerState.queue.name = item.dataset.playlist || '';
        return requestPlayback(item);
    }

    function shuffle(values) {
        const result = [...values];
        for (let index = result.length - 1; index > 0; index -= 1) {
            const target = Math.floor(Math.random() * (index + 1));
            [result[index], result[target]] = [result[target], result[index]];
        }
        return result;
    }

    function toggleShuffle() {
        playerState.shuffle = !playerState.shuffle;
        const button = document.getElementById('trackbar-shuffle');
        button?.classList.toggle('active', playerState.shuffle);
        if (button) {
            button.title = playerState.shuffle ? 'Shuffle: On' : 'Shuffle: Off';
        }
        if (playerState.queue.active && playerState.queue.keys.length > 1) {
            const played = playerState.queue.keys.slice(0, playerState.queue.index + 1);
            const upcoming = playerState.queue.keys.slice(playerState.queue.index + 1);
            playerState.queue.keys = played.concat(
                playerState.shuffle ? shuffle(upcoming) : upcoming
            );
            prefetchNextTrack();
        }
    }

    function cycleLoop() {
        const modes = ['off', 'playlist', 'single'];
        playerState.loop = modes[(modes.indexOf(playerState.loop) + 1) % modes.length];
        const button = document.getElementById('trackbar-loop');
        if (!button) return;
        button.classList.toggle('active', playerState.loop !== 'off');
        button.innerHTML = playerState.loop === 'single'
            ? '<i class="bi bi-arrow-repeat"></i><small>1</small>'
            : playerState.loop === 'playlist'
                ? '<i class="bi bi-arrow-repeat"></i><small>All</small>'
                : '<i class="bi bi-arrow-repeat"></i>';
        button.title = playerState.loop === 'off'
            ? 'Loop: Off'
            : playerState.loop === 'single'
                ? 'Loop: Single'
                : 'Loop: Playlist';
        prefetchNextTrack();
    }

    function startPlaylist(playlistName) {
        exitSurpriseMode();
        const accordion = Array.from(document.querySelectorAll(
            '.playlist-accordion[data-playlist-name][data-playlist-kind="regular"]'
        )).find(item => item.dataset.playlistName === playlistName);
        const items = accordion
            ? Array.from(accordion.querySelectorAll('[data-track-key]'))
            : [];
        if (!items.length) {
            notify(accordion ? 'No songs in this playlist' : 'Playlist not found', 'info');
            return Promise.resolve(false);
        }
        const audio = audioElement();
        if (audio && !audio.paused) pausePlayback();
        resetTrackButtons();
        playerState.queue.keys = items.map(item => item.dataset.trackKey);
        if (playerState.shuffle) {
            playerState.queue.keys = shuffle(playerState.queue.keys);
        }
        playerState.queue.index = 0;
        playerState.queue.active = true;
        playerState.queue.name = playlistName;
        resetPlaylistButtons();
        notify(`Starting ${items.length} songs in “${playlistName}”`, 'success');
        return playQueueEntry(0);
    }

    function finishPlaylist() {
        playerState.queue.active = false;
        if (Number.isInteger(playerState.pending?.playlistIndex)) {
            playerState.pending = null;
        }
        resetPlaylistButtons();
        updateMediaPlaybackState('none');
        notify('Playlist finished', 'info');
    }

    function playQueueEntry(startIndex = playerState.queue.index) {
        if (!playerState.queue.active || !playerState.queue.keys.length) {
            finishPlaylist();
            return Promise.resolve(false);
        }
        let index = startIndex;
        let checked = 0;
        while (checked < playerState.queue.keys.length) {
            if (index >= playerState.queue.keys.length) {
                if (playerState.loop !== 'playlist') {
                    finishPlaylist();
                    return Promise.resolve(false);
                }
                if (playerState.shuffle) {
                    playerState.queue.keys = shuffle(playerState.queue.keys);
                }
                index = 0;
            }
            const item = resolveTrackItem(playerState.queue.keys[index]);
            if (item) {
                return requestPlayback(item, {
                    playlistIndex: index,
                    surpriseIndex: null,
                });
            }
            index += 1;
            checked += 1;
        }
        finishPlaylist();
        return Promise.resolve(false);
    }

    function togglePlaylist(playlistName) {
        if (
            playerState.queue.active
            && playerState.queue.name === playlistName
        ) {
            const audio = audioElement();
            const playing = audio
                && !audio.paused
                && !playerState.pending
                && playerState.current.key === audio.dataset.trackKey;
            if (playing) {
                pausePlayback();
                return Promise.resolve(true);
            }
            const pendingIndex = playerState.pending?.playlistIndex;
            return playQueueEntry(
                Number.isInteger(pendingIndex)
                    ? pendingIndex
                    : playerState.queue.index
            );
        }
        return startPlaylist(playlistName);
    }

    function sleep(milliseconds) {
        return new Promise(resolve => setTimeout(resolve, milliseconds));
    }

    function markCached(crc) {
        trackItems()
            .filter(item => item.dataset.audioCrc === String(crc))
            .forEach(item => { item.dataset.isCached = 'true'; });
    }

    function convertTrack(item) {
        if (!item) return Promise.reject(new Error('Track is no longer available'));
        if (item.dataset.isCached !== 'false') return Promise.resolve(true);
        const crc = String(item.dataset.audioCrc);
        if (playerState.conversions.has(crc)) {
            return playerState.conversions.get(crc);
        }
        const conversion = (async () => {
            while (true) {
                const payload = await api().post(
                    `/tubio/audio/${encodeURIComponent(crc)}/cache`
                );
                if (payload.status === 'in_progress') {
                    await sleep(surprisePollInterval());
                    continue;
                }
                if (!payload.is_cached) {
                    throw new Error(payload.error || 'Could not convert this track');
                }
                markCached(crc);
                return true;
            }
        })();
        playerState.conversions.set(crc, conversion);
        conversion.finally(() => {
            if (playerState.conversions.get(crc) === conversion) {
                playerState.conversions.delete(crc);
            }
        }).catch(() => {});
        return conversion;
    }

    function prefetchTrack(item) {
        const source = item?.dataset.audioSrc;
        if (!source) return Promise.resolve(false);
        if (playerState.prefetchedSources.has(source)) return Promise.resolve(true);
        if (playerState.prefetches.has(source)) {
            return playerState.prefetches.get(source);
        }
        const prefetch = (async () => {
            await convertTrack(item);
            const response = await fetch(source, {
                credentials: 'same-origin',
                headers: { 'Accept': 'audio/mp4' },
            });
            if (!response.ok) {
                throw new Error(`Could not prefetch track (${response.status})`);
            }
            await response.blob();
            playerState.prefetchedSources.add(source);
            return true;
        })();
        playerState.prefetches.set(source, prefetch);
        prefetch.finally(() => {
            if (playerState.prefetches.get(source) === prefetch) {
                playerState.prefetches.delete(source);
            }
        }).catch(() => {});
        return prefetch;
    }

    function surpriseItems() {
        return trackItems().filter(
            item => item.dataset.playlistKind === 'surprise'
        );
    }

    function ensureSurpriseDomState() {
        if (playerState.surprise.payload) return;
        const items = surpriseItems();
        if (!items.length) return;
        playerState.surprise.payload = {
            audio_crcs: items.map(item => Number(item.dataset.audioCrc)),
            html: null,
        };
        const container = document.getElementById('surprise-playlist');
        playerState.surprise.exhausted = !container
            || container.dataset.exhausted === 'true';
    }

    function surpriseCrcs() {
        ensureSurpriseDomState();
        return playerState.surprise.payload?.audio_crcs || [];
    }

    function nextTrackItem() {
        if (playerState.surprise.mode) {
            return surpriseItems()[playerState.surprise.index + 1] || null;
        }
        if (!playerState.queue.active || !playerState.queue.keys.length) return null;
        let index = playerState.queue.index + 1;
        if (index >= playerState.queue.keys.length) {
            if (playerState.loop !== 'playlist' || playerState.shuffle) return null;
            index = 0;
        }
        return resolveTrackItem(playerState.queue.keys[index]);
    }

    function prefetchNextTrack() {
        const item = nextTrackItem();
        if (!item) return Promise.resolve(false);
        return prefetchTrack(item).catch(error => {
            reportError('track-prefetch', error, {
                crc: item.dataset.audioCrc,
                trackKey: item.dataset.trackKey,
                playlistKind: item.dataset.playlistKind,
            });
            return false;
        });
    }

    function surpriseBufferSize() {
        const value = Number.parseInt(
            document.getElementById('surprise-playlist')?.dataset.bufferSize,
            10,
        );
        return Number.isFinite(value) && value > 0 ? value : 5;
    }

    function surprisePollInterval() {
        const value = Number.parseInt(
            document.getElementById('surprise-playlist')?.dataset.cachePollIntervalMs,
            10,
        );
        return Number.isFinite(value) && value > 0 ? value : 750;
    }

    function normalizeSurprise(payload, source) {
        if (payload === null || payload === undefined) return null;
        if (!Array.isArray(payload.audio_crcs)) {
            const error = new Error('Surprise Playlist data is invalid. Reload Discover to try again.');
            reportError('surprise-payload', error, { source });
            throw error;
        }
        return payload;
    }

    function setSurpriseStatus(message) {
        const status = document.getElementById('surprise-status');
        if (status) status.textContent = message || '';
    }

    function exitSurpriseMode() {
        playerState.surprise.mode = false;
        setSurpriseStatus('');
    }

    function renderSurprise() {
        const container = document.getElementById('surprise-playlist');
        const payload = playerState.surprise.payload;
        if (!container || !payload?.html) return;
        container.innerHTML = payload.html;
        container.hidden = false;
        container.setAttribute('aria-busy', 'false');
        ui().decorate?.(container);
        const item = surpriseItems()[playerState.surprise.index];
        const audio = audioElement();
        if (item && audio?.dataset.trackKey === item.dataset.trackKey) {
            const playing = playerState.current.key === item.dataset.trackKey
                && !audio.paused;
            item.classList.toggle('track-playing', playing);
            setTrackButton(item.querySelector('.track-play-btn'), playing);
        }
    }

    async function fillSurpriseBuffer({ requirePlayback = true } = {}) {
        const surprise = playerState.surprise;
        if (surprise.filling || !surprise.payload || !api()) return;
        surprise.filling = true;
        try {
            while (
                (!requirePlayback || surprise.mode)
                && !surprise.exhausted
                && surpriseCrcs().length - surprise.index - 1 < surpriseBufferSize()
            ) {
                try {
                    const payload = await api().post('/tubio/surprise/grow');
                    if (payload.exhausted) {
                        surprise.exhausted = true;
                        break;
                    }
                    if (!payload.playlist) break;
                    surprise.payload = normalizeSurprise(payload.playlist, 'grow');
                    renderSurprise();
                    prefetchNextTrack();
                } catch (error) {
                    if (error.status === 404 || error.status === 409) {
                        if (!await loadSurprise()) break;
                        continue;
                    }
                    throw error;
                }
            }
        } finally {
            surprise.filling = false;
        }
    }

    async function ensureSurpriseCached(index) {
        const item = surpriseItems()[index];
        if (!item) return false;
        const needsDownload = item.dataset.isCached !== 'true';
        const button = item.querySelector('.track-play-btn');
        const original = button?.innerHTML || '';
        if (button && needsDownload) {
            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
        }
        if (needsDownload) {
            setSurpriseStatus(`Converting “${item.dataset.title || 'track'}”…`);
        }
        try {
            return await convertTrack(item);
        } catch (error) {
            notify(error.message, 'error');
            setSurpriseStatus(error.message);
            return false;
        } finally {
            if (button && needsDownload) {
                button.disabled = false;
                button.innerHTML = original;
            }
        }
    }

    function startSurpriseTrack(index) {
        const item = surpriseItems()[index];
        if (!item) return Promise.resolve(false);
        const audio = audioElement();
        if (
            playerState.surprise.mode
            && index === playerState.surprise.index
            && audio?.dataset.trackKey === item.dataset.trackKey
        ) {
            if (audio.paused) {
                return requestPlayback(item, {
                    playlistIndex: null,
                    surpriseIndex: index,
                });
            }
            pausePlayback();
            renderSurprise();
            return Promise.resolve(true);
        }
        playerState.surprise.mode = true;
        playerState.queue.active = false;
        resetPlaylistButtons();
        setSurpriseStatus(`Starting “${item.dataset.title || 'track'}”…`);
        renderSurprise();
        return requestPlayback(item, {
            playlistIndex: null,
            surpriseIndex: index,
        });
    }

    function playSurpriseTrack(index) {
        ensureSurpriseDomState();
        const item = surpriseItems()[index];
        if (!item) return Promise.resolve(false);
        if (item.dataset.isCached === 'true') return startSurpriseTrack(index);
        return ensureSurpriseCached(index).then(
            cached => cached ? startSurpriseTrack(index) : false
        );
    }

    async function playNextSurprise() {
        const surprise = playerState.surprise;
        if (!surprise.mode || !surprise.payload) return false;
        const pendingIndex = playerState.pending?.surpriseIndex;
        const nextIndex = Number.isInteger(pendingIndex)
            ? pendingIndex + 1
            : surprise.index + 1;
        if (nextIndex >= surpriseCrcs().length && !surprise.exhausted) {
            setSurpriseStatus('Loading next track…');
            await fillSurpriseBuffer();
        }
        if (nextIndex >= surpriseCrcs().length) {
            notify('Surprise playlist finished', 'info');
            exitSurpriseMode();
            return false;
        }
        return playSurpriseTrack(nextIndex);
    }

    async function loadSurprise() {
        const payload = await api().get('/tubio/surprise');
        const surprise = playerState.surprise;
        if (surprise.mode && surprise.payload && payload.playlist) {
            const currentCrc = surpriseCrcs()[surprise.index];
            const restored = normalizeSurprise(payload.playlist, 'restore-playing');
            surprise.payload = restored;
            surprise.index = restored.audio_crcs.findIndex(crc => crc === currentCrc);
            if (surprise.index < 0) {
                if (audioForCrc(currentCrc)) pausePlayback();
                exitSurpriseMode();
            }
        } else {
            surprise.payload = normalizeSurprise(payload.playlist, 'restore');
            surprise.index = -1;
            surprise.exhausted = false;
        }
        renderSurprise();
        return surprise.payload;
    }

    async function createSurprise(seedCrc = null) {
        const fields = seedCrc === null ? {} : { seed_crc: String(seedCrc) };
        const payload = await api().post('/tubio/surprise', fields);
        if (!payload.playlist) {
            throw new Error(
                payload.empty_reason === 'no_library'
                    ? 'Add some songs to your library first to generate a Surprise Playlist.'
                    : 'No fresh tracks found right now.'
            );
        }
        playerState.surprise.payload = normalizeSurprise(
            payload.playlist,
            seedCrc === null ? 'create' : 'seeded-create',
        );
        playerState.surprise.index = -1;
        playerState.surprise.exhausted = false;
        renderSurprise();
        return playerState.surprise.payload;
    }

    function initializeDiscover() {
        const surprise = playerState.surprise;
        if (surprise.initializing) return surprise.initializing;
        surprise.initializing = (async () => {
            if (!surprise.mode) setSurpriseStatus('Building your Surprise Playlist…');
            try {
                const restored = await loadSurprise();
                if (!restored) await createSurprise();
                if (!surprise.mode && surprise.payload) {
                    const count = surpriseCrcs().length;
                    setSurpriseStatus(`${count} track${count === 1 ? '' : 's'} ready to play.`);
                }
            } catch (error) {
                reportError('discover-initialize', error);
                if (!surprise.mode) setSurpriseStatus(error.message);
            } finally {
                surprise.initializing = null;
            }
        })();
        return surprise.initializing;
    }

    async function replaceSurprise(button, options = {}) {
        const surprise = playerState.surprise;
        const previous = surprise.payload;
        const previousCrcs = [...surpriseCrcs()];
        const original = button?.innerHTML || '';
        const seedCrc = options.seedCrc ?? null;
        if (button) {
            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
        }
        setSurpriseStatus(options.statusMessage || 'Refreshing your Surprise Playlist…');
        try {
            await createSurprise(seedCrc);
            const audio = audioElement();
            if (audio && previousCrcs.some(crc => String(crc) === audio.dataset.crc)) {
                pausePlayback();
                audio.removeAttribute('src');
                delete audio.dataset.crc;
                delete audio.dataset.trackKey;
                playerState.current = { crc: null, key: null };
                playerState.pending = null;
                updateTrackbar(null);
                updateScrubber();
            }
            exitSurpriseMode();
            surprise.index = -1;
            renderSurprise();
            const count = surpriseCrcs().length;
            setSurpriseStatus(`${count} track${count === 1 ? '' : 's'} ready to play.`);
            notify(options.successMessage || 'Surprise Playlist refreshed', 'success');
            return true;
        } catch (error) {
            surprise.payload = previous;
            renderSurprise();
            setSurpriseStatus(error.message);
            reportError('surprise-refresh', error);
            notify(error.message, 'error');
            return false;
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = original;
            }
        }
    }

    async function suggestMore(button) {
        const item = button?.closest('.playlist-track');
        const seedCrc = item?.dataset.audioCrc;
        if (!seedCrc) return false;
        ui().switchTab?.('discover', { initializeDiscover: false });
        if (playerState.surprise.initializing) {
            try { await playerState.surprise.initializing; } catch (_error) {}
        }
        const title = item.dataset.title || 'this track';
        return replaceSurprise(button, {
            seedCrc,
            statusMessage: `Finding more tracks like “${title}”…`,
            successMessage: `Built a Surprise Playlist from “${title}”`,
        });
    }

    async function favouriteSurprise(button) {
        const item = button?.closest('.playlist-track');
        if (!item || !playerState.surprise.payload) return false;
        button.disabled = true;
        try {
            const payload = await api().post(
                `/tubio/surprise/tracks/${encodeURIComponent(item.dataset.audioCrc)}/favourite`
            );
            playerState.surprise.payload = normalizeSurprise(
                payload.playlist,
                'favourite',
            );
            renderSurprise();
            if (payload.library_html) ui().replaceLibrary?.(payload.library_html);
            notify('Added to Favourites', 'success');
            return true;
        } catch (error) {
            button.disabled = false;
            notify(error.message, 'error');
            return false;
        }
    }

    async function saveSurprise() {
        if (!playerState.surprise.payload) return false;
        const name = window.prompt('Name this playlist:');
        if (!name?.trim()) return false;
        try {
            const payload = await api().post('/tubio/surprise/save', {
                playlist_name: name.trim(),
            });
            exitSurpriseMode();
            playerState.surprise.payload = null;
            renderSurprise();
            if (payload.library_html) ui().replaceLibrary?.(payload.library_html);
            ui().switchTab?.('playlists');
            ui().selectPlaylist?.(
                payload.playlist_name.replace(/ /g, '-').replace(/'/g, '')
            );
            notify(payload.message, 'success');
            return true;
        } catch (error) {
            notify(error.message, 'error');
            return false;
        }
    }

    function handleEnded() {
        const audio = audioElement();
        setCommittedUi(false);
        updateMediaPlaybackState('paused');
        if (playerState.loop === 'single' && audio) {
            const item = loadedTrackItem();
            if (item) requestPlayback(item, loadedContext(), { restart: true });
            return;
        }
        if (playerState.surprise.mode) {
            playNextSurprise();
        } else if (playerState.queue.active) {
            playQueueEntry(playerState.queue.index + 1);
        }
    }

    function togglePlayPause() {
        const audio = audioElement();
        if (!audio || (!playerState.current.key && !playerState.pending)) {
            return Promise.resolve(false);
        }
        const playing = !audio.paused
            && !playerState.pending
            && playerState.current.key === audio.dataset.trackKey;
        if (playing) {
            pausePlayback();
            return Promise.resolve(true);
        }
        return resumePlayback();
    }

    function nextTrack() {
        if (playerState.surprise.mode) {
            pausePlayback();
            return playNextSurprise();
        }
        if (!playerState.queue.active) return false;
        const pendingIndex = playerState.pending?.playlistIndex;
        const index = Number.isInteger(pendingIndex)
            ? pendingIndex
            : playerState.queue.index;
        pausePlayback();
        return playQueueEntry(index + 1);
    }

    function previousTrack() {
        const audio = audioElement();
        const start = audio ? playbackBounds(audio).start : 0;
        if (playerState.surprise.mode) {
            const pending = playerState.pending?.surpriseIndex;
            const index = Number.isInteger(pending)
                ? pending
                : playerState.surprise.index;
            if (audio && audio.currentTime > start + 3) audio.currentTime = start;
            else if (index > 0) {
                pausePlayback();
                playSurpriseTrack(index - 1);
            } else if (audio) audio.currentTime = start;
            return true;
        }
        if (playerState.queue.active) {
            const pending = playerState.pending?.playlistIndex;
            const index = Number.isInteger(pending) ? pending : playerState.queue.index;
            if (audio && audio.currentTime > start + 3) audio.currentTime = start;
            else if (index > 0) {
                pausePlayback();
                playQueueEntry(index - 1);
            } else if (audio) audio.currentTime = start;
        } else if (audio) {
            audio.currentTime = start;
        }
        return true;
    }

    function seek(value) {
        const audio = audioForCrc(playerState.current.crc);
        if (!audio) return;
        const apply = () => {
            const bounds = playbackBounds(audio);
            audio.currentTime = bounds.start
                + ((Number(value) / 100) * (bounds.end - bounds.start));
            updateScrubber();
            updateMediaPosition();
        };
        if (audio.readyState === 0) {
            audio.addEventListener('loadedmetadata', apply, { once: true });
            audio.load();
        } else if (Number.isFinite(audio.duration)) {
            apply();
        }
    }

    function formatTime(seconds) {
        if (!Number.isFinite(seconds)) return '0:00';
        const minutes = Math.floor(seconds / 60);
        const remainder = Math.floor(seconds % 60);
        return `${minutes}:${String(remainder).padStart(2, '0')}`;
    }

    function updateTrackbar(reference) {
        const trackbar = document.getElementById('tubio-trackbar');
        const title = document.getElementById('trackbar-title');
        const playlist = document.getElementById('trackbar-playlist');
        const thumbnail = document.getElementById('trackbar-thumb');
        const placeholder = document.getElementById('trackbar-thumb-placeholder');
        const item = resolveTrackItem(reference);
        if (!item) {
            if (trackbar) trackbar.dataset.active = 'false';
            if (title) title.textContent = 'No track playing';
            if (playlist) playlist.textContent = '';
            if (thumbnail) {
                thumbnail.hidden = true;
                thumbnail.removeAttribute('src');
            }
            if (placeholder) placeholder.hidden = false;
            updateTrackbarPlayButton(false);
            updateTitleOverflow();
            return;
        }
        if (trackbar) trackbar.dataset.active = 'true';
        if (title) title.textContent = item.dataset.title || 'Unknown Track';
        if (playlist) playlist.textContent = item.dataset.playlist || '';
        if (thumbnail && item.dataset.thumbnailUrl) {
            thumbnail.src = item.dataset.thumbnailUrl;
            thumbnail.hidden = false;
            if (placeholder) placeholder.hidden = true;
        } else {
            if (thumbnail) thumbnail.hidden = true;
            if (placeholder) placeholder.hidden = false;
        }
        const audio = audioElement();
        updateTrackbarPlayButton(
            audio?.dataset.trackKey === item.dataset.trackKey && !audio.paused
        );
        updateTitleOverflow();
    }

    function updateTrackbarPlayButton(isPlaying) {
        const button = document.getElementById('trackbar-playpause');
        if (!button) return;
        button.innerHTML = isPlaying
            ? '<i class="bi bi-pause-fill"></i>'
            : '<i class="bi bi-play-fill"></i>';
        button.title = isPlaying ? 'Pause' : 'Play';
    }

    function updateScrubber() {
        const range = document.getElementById('trackbar-scrubber');
        const current = document.getElementById('trackbar-time-current');
        const duration = document.getElementById('trackbar-time-duration');
        if (!range) return;
        if (!playerState.current.crc) {
            range.value = 0;
            range.disabled = true;
            if (current) current.textContent = '0:00';
            if (duration) duration.textContent = '0:00';
            return;
        }
        range.disabled = false;
        const audio = audioForCrc(playerState.current.crc);
        if (!audio || !Number.isFinite(audio.duration)) return;
        const bounds = playbackBounds(audio);
        const playable = bounds.end - bounds.start;
        const elapsed = Math.max(0, audio.currentTime - bounds.start);
        range.value = playable > 0 ? (elapsed / playable) * 100 : 0;
        if (current) current.textContent = formatTime(elapsed);
        if (duration) duration.textContent = formatTime(playable);
    }

    function updateTitleOverflow() {
        const title = document.getElementById('trackbar-title');
        const wrapper = document.querySelector('.trackbar-title-wrap');
        if (!title || !wrapper) return;
        title.classList.remove('is-overflowing');
        title.style.removeProperty('--trackbar-title-shift');
        const overflow = title.scrollWidth - wrapper.clientWidth;
        if (overflow <= 4) return;
        title.style.setProperty('--trackbar-title-shift', `${overflow}px`);
        title.classList.add('is-overflowing');
    }

    function volumeRange() {
        return document.getElementById('trackbar-volume');
    }

    function clampVolume(value) {
        const range = volumeRange();
        if (!range) return Number(value) || 0;
        const min = Number(range.min);
        const max = Number(range.max);
        const number = Number(value);
        return Math.min(max, Math.max(min, Number.isFinite(number) ? number : min));
    }

    function volumeConfig() {
        const trackbar = document.getElementById('tubio-trackbar');
        const range = volumeRange();
        return {
            defaultVolume: clampVolume(trackbar?.dataset.defaultVolume || range?.value),
            volumeKey: trackbar?.dataset.volumeStorageKey || '',
            mutedKey: trackbar?.dataset.mutedStorageKey || '',
        };
    }

    function normalizedVolume() {
        const range = volumeRange();
        if (!range) return 1;
        const min = Number(range.min);
        const max = Number(range.max);
        return max > min
            ? (clampVolume(playerState.volume.percent) - min) / (max - min)
            : 0;
    }

    function applyVolume(audio) {
        if (!audio || playerState.volume.percent === null) return;
        audio.volume = normalizedVolume();
        audio.muted = playerState.volume.muted;
    }

    function applyVolumeToAll() {
        document.querySelectorAll('audio').forEach(applyVolume);
    }

    function updateVolumeUi() {
        const range = volumeRange();
        const button = document.getElementById('trackbar-mute');
        if (!range || !button || playerState.volume.percent === null) return;
        const min = Number(range.min);
        const midpoint = min + ((Number(range.max) - min) / 2);
        range.value = String(Math.round(playerState.volume.percent));
        const icon = button.querySelector('i');
        button.title = playerState.volume.muted ? 'Unmute' : 'Mute';
        button.setAttribute('aria-label', button.title);
        if (icon) {
            icon.className = playerState.volume.muted || playerState.volume.percent <= min
                ? 'bi bi-volume-mute-fill'
                : playerState.volume.percent <= midpoint
                    ? 'bi bi-volume-down-fill'
                    : 'bi bi-volume-up-fill';
        }
    }

    function persistVolume() {
        const config = volumeConfig();
        try {
            localStorage.setItem(config.volumeKey, String(Math.round(playerState.volume.percent)));
            localStorage.setItem(config.mutedKey, playerState.volume.muted ? '1' : '0');
        } catch (_error) {}
    }

    function setVolume(value) {
        const range = volumeRange();
        playerState.volume.percent = clampVolume(value);
        if (playerState.volume.percent > Number(range?.min || 0)) {
            playerState.volume.muted = false;
        }
        persistVolume();
        updateVolumeUi();
        applyVolumeToAll();
    }

    function toggleMute() {
        playerState.volume.muted = !playerState.volume.muted;
        persistVolume();
        updateVolumeUi();
        applyVolumeToAll();
    }

    function initializeVolume() {
        const range = volumeRange();
        if (!range) return;
        const config = volumeConfig();
        if (playerState.volume.percent === null) {
            try {
                const stored = localStorage.getItem(config.volumeKey);
                playerState.volume.percent = stored === null
                    ? config.defaultVolume
                    : clampVolume(stored);
                playerState.volume.muted = localStorage.getItem(config.mutedKey) === '1';
            } catch (_error) {
                playerState.volume.percent = config.defaultVolume;
            }
        }
        updateVolumeUi();
        applyVolumeToAll();

        const button = document.getElementById('trackbar-mute');
        const control = document.querySelector('.trackbar-volume');
        if (button && !button._tubioBound) {
            button._tubioBound = true;
            button.addEventListener('click', event => {
                const touchLayout = window.matchMedia(
                    '(hover: none), (pointer: coarse)'
                ).matches;
                if (touchLayout && control && !control.classList.contains('is-open')) {
                    event.preventDefault();
                    control.classList.add('is-open');
                    button.setAttribute('aria-expanded', 'true');
                    return;
                }
                toggleMute();
            });
        }
        if (control && !control._tubioBound) {
            control._tubioBound = true;
            control.addEventListener('mouseenter', () => {
                button?.setAttribute('aria-expanded', 'true');
            });
            control.addEventListener('mouseleave', () => {
                if (
                    !control.classList.contains('is-open')
                    && !control.contains(document.activeElement)
                ) {
                    button?.setAttribute('aria-expanded', 'false');
                }
            });
            control.addEventListener('focusin', () => {
                button?.setAttribute('aria-expanded', 'true');
            });
            control.addEventListener('focusout', event => {
                if (
                    !control.classList.contains('is-open')
                    && !control.contains(event.relatedTarget)
                ) {
                    button?.setAttribute('aria-expanded', 'false');
                }
            });
            document.addEventListener('click', event => {
                if (!control.contains(event.target)) {
                    control.classList.remove('is-open');
                    button?.setAttribute('aria-expanded', 'false');
                }
            });
        }
    }

    function updateMediaMetadata(item) {
        if (!('mediaSession' in navigator) || typeof MediaMetadata !== 'function') return;
        try {
            navigator.mediaSession.metadata = new MediaMetadata({
                title: item.dataset.title || 'Unknown Track',
                album: item.dataset.playlist || '',
                artwork: item.dataset.thumbnailUrl
                    ? [{ src: item.dataset.thumbnailUrl, sizes: '512x512', type: 'image/jpeg' }]
                    : [],
            });
        } catch (error) {
            reportError('media-session-metadata', error, {
                crc: item.dataset.audioCrc,
                trackKey: item.dataset.trackKey,
            });
        }
    }

    function updateMediaPlaybackState(value) {
        if (!('mediaSession' in navigator)) return;
        try { navigator.mediaSession.playbackState = value; } catch (_error) {}
    }

    function updateMediaPosition() {
        if (typeof navigator.mediaSession?.setPositionState !== 'function') return;
        const audio = audioElement();
        if (
            !audio
            || audio.dataset.trackKey !== playerState.current.key
            || !Number.isFinite(audio.duration)
            || audio.duration <= 0
        ) {
            try { navigator.mediaSession.setPositionState(); } catch (_error) {}
            return;
        }
        const bounds = playbackBounds(audio);
        const duration = bounds.end - bounds.start;
        if (duration <= 0) return;
        try {
            navigator.mediaSession.setPositionState({
                duration,
                playbackRate: audio.playbackRate || 1,
                position: Math.min(duration, Math.max(0, audio.currentTime - bounds.start)),
            });
        } catch (_error) {}
    }

    function bindMediaSession() {
        if (!('mediaSession' in navigator)) return;
        const setHandler = (action, handler) => {
            try { navigator.mediaSession.setActionHandler(action, handler); } catch (_error) {}
        };
        setHandler('play', resumePlayback);
        setHandler('pause', pausePlayback);
        setHandler('nexttrack', nextTrack);
        setHandler('previoustrack', previousTrack);
        setHandler('seekto', details => {
            const audio = audioElement();
            if (!audio || !Number.isFinite(details.seekTime)) return;
            const bounds = playbackBounds(audio);
            const target = Math.min(
                bounds.end,
                Math.max(bounds.start, bounds.start + details.seekTime),
            );
            if (details.fastSeek && typeof audio.fastSeek === 'function') {
                audio.fastSeek(target);
            } else {
                audio.currentTime = target;
            }
            updateScrubber();
            updateMediaPosition();
        });
        updateMediaPlaybackState(playerState.current.key ? 'paused' : 'none');
    }

    function bindAudio() {
        const audio = audioElement();
        if (!audio || audio._tubioPlayerBound) {
            if (audio) applyVolume(audio);
            return;
        }
        audio._tubioPlayerBound = true;
        audio.addEventListener('play', () => applyPlaybackStart(audio));
        audio.addEventListener('playing', () => commitPlayback(audio));
        audio.addEventListener('pause', () => {
            setCommittedUi(false);
            updateMediaPlaybackState('paused');
            updateMediaPosition();
        });
        audio.addEventListener('timeupdate', () => {
            const bounds = playbackBounds(audio);
            if (!audio.paused && audio.currentTime >= bounds.end && !audio._trimEnded) {
                audio._trimEnded = true;
                audio.pause();
                audio.dispatchEvent(new Event('ended'));
                return;
            }
            updateScrubber();
            updateMediaPosition();
        });
        audio.addEventListener('loadedmetadata', () => {
            audio._metadataReady = true;
            applyPlaybackStart(audio);
            updateScrubber();
            updateMediaPosition();
        });
        audio.addEventListener('ratechange', updateMediaPosition);
        audio.addEventListener('ended', handleEnded);
        audio.addEventListener('error', () => {
            const error = new Error(
                audio.error ? `Media error ${audio.error.code}` : 'Unknown media error'
            );
            if (playerState.pending) handlePlaybackFailure(error, playerState.pending);
            else reportError('media-element', error, {
                crc: audio.dataset.crc,
                trackKey: audio.dataset.trackKey,
            });
        });
        applyVolume(audio);
    }

    function bindControls() {
        const scrubber = document.getElementById('trackbar-scrubber');
        if (scrubber && !scrubber._tubioBound) {
            scrubber._tubioBound = true;
            scrubber.addEventListener('input', () => seek(scrubber.value));
        }
        const volume = volumeRange();
        if (volume && !volume._tubioBound) {
            volume._tubioBound = true;
            volume.addEventListener('input', () => setVolume(volume.value));
        }
    }

    function reconcileDom() {
        bindAudio();
        bindControls();
        initializeVolume();
        bindMediaSession();
        if (playerState.current.key) {
            const item = resolveTrackItem(playerState.current.key);
            updateTrackbar(item);
            if (item) {
                const audio = audioElement();
                if (audio?.dataset.trackKey === item.dataset.trackKey) {
                    audio.dataset.trimStart = item.dataset.trimStart || '0';
                    audio.dataset.trimEnd = item.dataset.trimEnd || '0';
                }
                const playing = audio?.dataset.trackKey === item.dataset.trackKey
                    && !audio.paused
                    && !playerState.pending;
                item.classList.toggle('track-playing', playing);
                setTrackButton(item.querySelector('.track-play-btn'), playing);
            }
        } else {
            updateTrackbar(null);
        }
        updateScrubber();
        updateTitleOverflow();
    }

    function forgetTrack(track) {
        if (!track || track.dataset.audioCrc !== playerState.current.crc) return true;
        pausePlayback();
        const audio = audioElement();
        if (audio) {
            audio.removeAttribute('src');
            delete audio.dataset.crc;
            delete audio.dataset.trackKey;
        }
        playerState.current = { crc: null, key: null };
        playerState.pending = null;
        updateTrackbar(null);
        updateScrubber();
        return true;
    }

    function init() {
        reconcileDom();
        if (!playerState.initialized) {
            playerState.initialized = true;
            window.addEventListener('resize', updateTitleOverflow);
        }
    }

    function handleAction(action, element) {
        const track = element?.matches?.('.playlist-track')
            ? element
            : element?.closest?.('.playlist-track');
        const panel = element?.matches?.('.playlist-panel')
            ? element
            : element?.closest?.('.playlist-panel');
        switch (action) {
            case 'toggle-track':
                playerState.queue.active = false;
                resetPlaylistButtons();
                return toggleTrack(track);
            case 'toggle-playlist':
                return togglePlaylist(panel?.dataset.playlistName);
            case 'toggle-surprise-track': {
                const index = surpriseItems().indexOf(track);
                return index >= 0 ? playSurpriseTrack(index) : false;
            }
            case 'toggle-surprise-playlist':
                return playerState.surprise.mode ? togglePlayPause() : playSurpriseTrack(0);
            case 'toggle-shuffle':
                toggleShuffle(); return true;
            case 'cycle-loop':
                cycleLoop(); return true;
            case 'toggle-playback':
                return togglePlayPause();
            case 'next-track':
                return nextTrack();
            case 'previous-track':
                return previousTrack();
            case 'initialize-discover':
                return initializeDiscover();
            case 'refresh-surprise':
                return replaceSurprise(element);
            case 'suggest-more':
                return suggestMore(element);
            case 'favourite-surprise':
                return favouriteSurprise(element);
            case 'save-surprise':
                return saveSurprise();
            case 'forget-track':
                return forgetTrack(track);
            default:
                return false;
        }
    }

    function state() {
        return {
            currentCrc: playerState.current.crc,
            currentTrackKey: playerState.current.key,
            pendingTrackKey: playerState.pending?.trackKey || null,
            playlistIndex: playerState.queue.index,
            playlistName: playerState.queue.name,
            isPlayingPlaylist: playerState.queue.active,
            surpriseIndex: playerState.surprise.index,
            surpriseMode: playerState.surprise.mode,
            shuffle: playerState.shuffle,
            loop: playerState.loop,
        };
    }

    Tubio.player = { handleAction, init, reconcileDom, state };
})();
