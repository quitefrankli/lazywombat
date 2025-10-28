function switchTab(tabName) {
    // Remove active class from all navbar tabs
    document.querySelectorAll('#search-nav-tab, #playlists-nav-tab').forEach(tab => {
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
    
    // Update URL hash
    window.location.hash = '#' + tabName;
}

function displaySearchResults(results) {
    const resultsDiv = document.getElementById('search-results');
    if (!resultsDiv) return;
    
    if (!results || results.length === 0) {
        resultsDiv.innerHTML = '<div class="text-center py-5"><h5 class="text-muted">No results found</h5></div>';
        return;
    }
    
    let html = '<div class="accordion" id="searchResultsAccordion">';
    
    results.forEach((video, index) => {
        const isDisabled = video.cached ? 'disabled style="background-color: #adb5bd; border-color: #adb5bd;"' : '';
        const truncatedTitle = video.title.length > 60 ? video.title.substring(0, 60) + '...' : video.title;
        
        html += `
            <div class="accordion-item mb-3 border-0 shadow-sm">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed bg-gradient text-primary fw-semibold" 
                            type="button" 
                            data-bs-toggle="collapse" 
                            data-bs-target="#collapse-search-${index}" 
                            aria-expanded="false" 
                            aria-controls="collapse-search-${index}"
                            style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);">
                        <i class="bi bi-youtube me-2"></i>
                        <div class="d-flex justify-content-between align-items-center w-100 me-3">
                            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">${truncatedTitle}</span>
                            <small class="badge bg-secondary ms-2">${video.length}</small>
                        </div>
                    </button>
                </h2>
                <div id="collapse-search-${index}" 
                     class="accordion-collapse collapse" 
                     data-bs-parent="#searchResultsAccordion">
                    <div class="accordion-body bg-light">
                        <div class="mb-3">
                            <h6 class="text-primary mb-2">Full Title:</h6>
                            <p class="text-dark fw-medium">${video.title}</p>
                        </div>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <h6 class="text-primary mb-2">Description:</h6>
                                <p class="text-muted small" style="max-height: 100px; overflow-y: auto;">${video.description}</p>
                            </div>
                            <div class="col-md-6 mb-3">
                                <div class="row">
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Views:</h6>
                                        <p class="text-dark">${video.view_count}</p>
                                    </div>
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Published:</h6>
                                        <p class="text-dark">${video.published}</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="text-center">
                            <button onclick="downloadVideo('${video.video_id}', '${video.title.replace(/'/g, "\\'")}', this)" 
                                    class="btn btn-primary" ${isDisabled} id="download-btn-${index}">
                                <i class="bi bi-download me-1"></i>Add To Favourites
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    });
    
    html += '</div>';
    resultsDiv.innerHTML = html;
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
            const query = document.getElementById('youtube-query').value;
            
            try {
                const formData = new FormData();
                formData.append('youtube_query', query);
                
                const response = await fetch('/tubio/search', {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                if (response.ok) {
                    const data = await response.json();
                    displaySearchResults(data.results);
                } else {
                    console.error('Search failed');
                    const resultsDiv = document.getElementById('search-results');
                    if (resultsDiv) {
                        resultsDiv.innerHTML = '<div class="text-center py-5"><h5 class="text-danger">Search failed. Please try again.</h5></div>';
                    }
                }
            } catch (error) {
                console.error('Error during search:', error);
                const resultsDiv = document.getElementById('search-results');
                if (resultsDiv) {
                    resultsDiv.innerHTML = '<div class="text-center py-5"><h5 class="text-danger">Error occurred while searching.</h5></div>';
                }
            }
        });
    }
});

async function downloadVideo(videoId, title, buttonElement) {
    const originalText = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Downloading...';
    
    try {
        const formData = new FormData();
        formData.append('video_id', videoId);
        formData.append('title', title);
        
        const response = await fetch('/tubio/youtube_download', {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            // Show success message
            showNotification(data.message, 'success');
            
            // Update playlists content
            await updateContent(data);
            
            // Disable the button and show it's cached
            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i>Downloaded';
            buttonElement.style.backgroundColor = '#adb5bd';
            buttonElement.style.borderColor = '#adb5bd';
            
        } else {
            throw new Error(data.error || 'Download failed');
        }
        
    } catch (error) {
        console.error('Error downloading video:', error);
        showNotification(error.message || 'Error downloading video', 'error');
        
        // Reset button
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
            
            // Extract the playlists tab content from the response
            const newPlaylistsContent = doc.getElementById('playlists');
            const currentPlaylistsTab = document.getElementById('playlists');
            
            if (newPlaylistsContent && currentPlaylistsTab) {
                currentPlaylistsTab.innerHTML = newPlaylistsContent.innerHTML;
                
                // Re-attach collapse event listeners for chevron animation
                currentPlaylistsTab.querySelectorAll('[data-bs-toggle="collapse"]').forEach(button => {
                    const targetId = button.getAttribute('data-bs-target');
                    if (targetId) {
                        const target = document.querySelector(targetId);
                        if (target) {
                            target.addEventListener('shown.bs.collapse', function() {
                                button.setAttribute('aria-expanded', 'true');
                            });
                            target.addEventListener('hidden.bs.collapse', function() {
                                button.setAttribute('aria-expanded', 'false');
                            });
                        }
                    }
                });
            }
        }
    } catch (error) {
        console.error('Error updating content:', error);
        // Fallback: just reload the page
        window.location.reload();
    }
}

// Individual track playback controls
function togglePlayTrack(crc) {
    const audioElement = document.getElementById(`audio-${crc}`);
    const playButton = document.getElementById(`play-btn-${crc}`);
    
    if (!audioElement || !playButton) {
        console.error(`Audio element or button not found for crc: ${crc}`);
        return;
    }
    
    if (audioElement.paused) {
        // Pause all other audio elements and reset their buttons
        document.querySelectorAll('audio').forEach(audio => {
            if (audio.id !== `audio-${crc}` && !audio.paused) {
                audio.pause();
                // Reset the other play button
                const otherCrc = audio.id.replace('audio-', '');
                const otherButton = document.getElementById(`play-btn-${otherCrc}`);
                if (otherButton) {
                    otherButton.innerHTML = '<i class="bi bi-play-fill"></i>';
                    otherButton.classList.remove('btn-success');
                    otherButton.classList.add('btn-outline-primary');
                }
            }
        });
        
        // Play this track
        audioElement.play().catch(err => {
            console.error('Error playing audio:', err);
            showNotification('Error playing audio. Please try again.', 'error');
        });
        
        // Update button to show pause icon
        playButton.innerHTML = '<i class="bi bi-pause-fill"></i>';
        playButton.classList.remove('btn-outline-primary');
        playButton.classList.add('btn-success');
    } else {
        // Pause this track
        audioElement.pause();
        
        // Update button to show play icon
        playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
        playButton.classList.remove('btn-success');
        playButton.classList.add('btn-outline-primary');
    }
    
    // Add event listener to reset button when track ends naturally
    audioElement.onended = function() {
        if (!audioElement.loop) {
            playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
            playButton.classList.remove('btn-success');
            playButton.classList.add('btn-outline-primary');
        }
    };
}

function toggleLoopTrack(crc) {
    const audioElement = document.getElementById(`audio-${crc}`);
    const loopButton = document.getElementById(`loop-btn-${crc}`);
    
    if (!audioElement || !loopButton) {
        console.error(`Audio element or loop button not found for crc: ${crc}`);
        return;
    }
    
    // Toggle loop state
    audioElement.loop = !audioElement.loop;
    
    // Update button visual state
    if (audioElement.loop) {
        loopButton.classList.remove('btn-outline-secondary');
        loopButton.classList.add('btn-warning');
        loopButton.innerHTML = '<i class="bi bi-arrow-repeat" style="font-weight: bold;"></i>';
        loopButton.title = 'Loop enabled - Click to disable';
    } else {
        loopButton.classList.remove('btn-warning');
        loopButton.classList.add('btn-outline-secondary');
        loopButton.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
        loopButton.title = 'Loop disabled - Click to enable';
    }
}

// Playlist playback functionality
let currentPlaylistQueue = [];
let currentPlaylistIndex = 0;
let isPlayingPlaylist = false;

function playAllInPlaylist(playlistName) {
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
    
    // Build queue of audio CRCs
    currentPlaylistQueue = Array.from(audioItems).map(item => item.dataset.audioCrc);
    currentPlaylistIndex = 0;
    isPlayingPlaylist = true;
    
    // Show notification
    showNotification(`Playing all ${currentPlaylistQueue.length} songs in "${playlistName}"`, 'success');
    
    // Start playing first song
    playNextInQueue();
}

function playNextInQueue() {
    if (!isPlayingPlaylist || currentPlaylistIndex >= currentPlaylistQueue.length) {
        // Playlist finished
        isPlayingPlaylist = false;
        showNotification('Playlist finished', 'info');
        return;
    }
    
    const crc = currentPlaylistQueue[currentPlaylistIndex];
    const audioElement = document.getElementById(`audio-${crc}`);
    
    if (!audioElement) {
        console.error(`Audio element not found: audio-${crc}`);
        currentPlaylistIndex++;
        playNextInQueue();
        return;
    }
    
    // Expand the accordion for this song
    const collapseElement = document.getElementById(`collapse-${crc}`);
    if (collapseElement && !collapseElement.classList.contains('show')) {
        const bsCollapse = new bootstrap.Collapse(collapseElement, {
            toggle: true
        });
    }
    
    // Scroll to the song
    audioElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
    
    // Remove previous ended event listeners to avoid duplicates
    const oldAudio = document.querySelector('audio[data-playlist-playing="true"]');
    if (oldAudio) {
        oldAudio.removeAttribute('data-playlist-playing');
    }
    
    // Mark this audio as playing from playlist
    audioElement.setAttribute('data-playlist-playing', 'true');
    
    // Play the audio
    audioElement.currentTime = 0;
    audioElement.play().catch(err => {
        console.error('Error playing audio:', err);
        currentPlaylistIndex++;
        playNextInQueue();
    });
    
    // Set up event listener for when song ends
    audioElement.addEventListener('ended', function onEnded() {
        // Remove this event listener
        audioElement.removeEventListener('ended', onEnded);
        audioElement.removeAttribute('data-playlist-playing');
        
        // Move to next song
        if (isPlayingPlaylist) {
            currentPlaylistIndex++;
            setTimeout(() => playNextInQueue(), 500); // Small delay between songs
        }
    }, { once: true });
}

// Stop playlist playback if user manually interacts with audio controls
document.addEventListener('play', function(e) {
    if (e.target.tagName === 'AUDIO' && !e.target.hasAttribute('data-playlist-playing')) {
        // User manually started playing a different song
        isPlayingPlaylist = false;
    }
}, true);

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `alert alert-${type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info'} alert-dismissible fade show position-fixed`;
    notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
    notification.innerHTML = `
        ${message}
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

// Handle playlist collapse chevron rotation
document.addEventListener('DOMContentLoaded', function() {
    // Update chevron rotation when collapse events occur
    document.querySelectorAll('[data-bs-toggle="collapse"]').forEach(button => {
        const targetId = button.getAttribute('data-bs-target');
        if (targetId) {
            const target = document.querySelector(targetId);
            if (target) {
                target.addEventListener('shown.bs.collapse', function() {
                    button.setAttribute('aria-expanded', 'true');
                });
                target.addEventListener('hidden.bs.collapse', function() {
                    button.setAttribute('aria-expanded', 'false');
                });
            }
        }
    });
});

// Playlist management functions
function getSelectedSongs() {
    const checkboxes = document.querySelectorAll('.song-checkbox:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

function preparePlaylistModal() {
    const selectedSongs = getSelectedSongs();
    const count = selectedSongs.length;
    
    // Update count displays
    document.getElementById('add-selected-count').textContent = count;
    document.getElementById('remove-selected-count').textContent = count;
    document.getElementById('delete-selected-count').textContent = count;
    
    // Set hidden inputs
    document.getElementById('add_song_crcs').value = selectedSongs.join(',');
    document.getElementById('remove_song_crcs').value = selectedSongs.join(',');
    document.getElementById('delete_song_crcs').value = selectedSongs.join(',');
    
    // Disable submit if no songs selected
    const addBtn = document.querySelector('#add-to-playlist-form button[type="submit"]');
    const removeBtn = document.querySelector('#remove-from-playlist-form button[type="submit"]');
    const deleteBtn = document.querySelector('#delete-selected-songs-form button[type="submit"]');
    
    if (addBtn) addBtn.disabled = (count === 0);
    if (removeBtn) removeBtn.disabled = (count === 0);
    if (deleteBtn) deleteBtn.disabled = (count === 0);
}

// Select/deselect all checkboxes in a playlist
function togglePlaylistSelection(playlistName, checked) {
    const accordionId = `audioAccordion-${playlistName.replace(/ /g, '-')}`;
    const accordion = document.getElementById(accordionId);
    if (accordion) {
        const checkboxes = accordion.querySelectorAll('.song-checkbox');
        checkboxes.forEach(cb => cb.checked = checked);
    }
}

// Clear all selections
function clearAllSelections() {
    document.querySelectorAll('.song-checkbox').forEach(cb => cb.checked = false);
}