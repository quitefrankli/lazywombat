document.addEventListener('DOMContentLoaded', function() {
    const fileListDiv = document.getElementById('fileList');
    const fileSearch = document.getElementById('fileSearch');
    let files = [];
    function renderFileList(filter = "") {
        fileListDiv.innerHTML = '';
        files.filter(f => f.toLowerCase().includes(filter.toLowerCase())).forEach(file => {
            const item = document.createElement('div');
            item.className = 'd-flex justify-content-between align-items-center mb-1';
            item.innerHTML = `<span>${file}</span> <a href="/cheapify/download/${encodeURIComponent(file)}" class="btn btn-success btn-sm">Download</a>`;
            fileListDiv.appendChild(item);
        });
        if (fileListDiv.innerHTML === '') {
            fileListDiv.innerHTML = '<div class="text-muted">No files found.</div>';
        }
    }
    const downloadModal = document.getElementById('downloadModal');
    if (downloadModal) {
        downloadModal.addEventListener('show.bs.modal', function () {
            fetch('/cheapify/files_list').then(r => r.json()).then(data => {
                files = data.files || [];
                renderFileList();
            });
        });
    }
    if (fileSearch) {
        fileSearch.addEventListener('input', function() {
            renderFileList(this.value);
        });
    }
});
