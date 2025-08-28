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
                            <form method="post" action="/tubio/youtube_download" style="display:inline;">
                                <input type="hidden" name="video_id" value="${video.video_id}">
                                <input type="hidden" name="title" value="${video.title}">
                                <button type="submit" class="btn btn-primary" ${isDisabled}>
                                    <i class="bi bi-download me-1"></i>Download
                                </button>
                            </form>
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
    const hash = window.location.hash.replace('#', '');
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