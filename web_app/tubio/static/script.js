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
    // Update playlists tab if data is provided
    if (data.playlists) {
        const playlistsTab = document.getElementById('playlists');
        if (playlistsTab) {
            playlistsTab.innerHTML = renderPlaylists(data.playlists);
        }
    }
}

function renderPlaylists(playlists) {
    let html = '';
    
    playlists.forEach(([playlistName, playlistData]) => {
        html += `
            <div class="text-center mb-4">
                <h2 class="fw-bold text-primary mb-3">${playlistName}</h2>
            </div>
            <div class="accordion" id="audioAccordion-${playlistName.replace(/\s+/g, '-')}">
        `;
        
        playlistData.forEach(([crc, title]) => {
            html += `
                <div class="accordion-item mb-3 border-0 shadow-sm">
                    <h2 class="accordion-header">
                        <button class="accordion-button collapsed bg-gradient text-primary fw-semibold" 
                                type="button" 
                                data-bs-toggle="collapse" 
                                data-bs-target="#collapse-playlist-${crc}" 
                                aria-expanded="false" 
                                aria-controls="collapse-playlist-${crc}"
                                style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);">
                            <i class="bi bi-musical-note me-2"></i>
                            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${title}</span>
                        </button>
                    </h2>
                    <div id="collapse-playlist-${crc}" 
                         class="accordion-collapse collapse" 
                         data-bs-parent="#audioAccordion-${playlistName.replace(/\s+/g, '-')}">
                        <div class="accordion-body bg-light">
                            <div class="mb-3">
                                <h6 class="text-primary mb-2">Full Title:</h6>
                                <p class="text-dark fw-medium">${title}</p>
                            </div>
                            <div class="d-flex flex-column align-items-center">
                                <audio controls preload="metadata" id="audio-playlist-${crc}" style="max-width: 300px;">
                                    <source src="/tubio/audio/${crc}" type="audio/mp4">
                                    Your browser does not support the audio element.
                                </audio>
                                <div class="d-flex gap-3 mt-3 align-items-center">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" id="loop-playlist-${crc}" onchange="document.getElementById('audio-playlist-${crc}').loop = this.checked">
                                        <label class="form-check-label" for="loop-playlist-${crc}">Loop</label>
                                    </div>
                                    <form method="post" action="/tubio/delete_audio/${crc}" style="display:inline;" 
                                          onsubmit="return confirm('Are you sure you want to delete this audio track?');">
                                        <button type="submit" class="btn btn-outline-danger btn-sm">
                                            <i class="bi bi-trash me-1"></i>Delete
                                        </button>
                                    </form>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
    });
    
    return html;
}

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