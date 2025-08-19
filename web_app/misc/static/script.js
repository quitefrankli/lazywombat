document.addEventListener('DOMContentLoaded', function() {
    function setupFileModal({modalId, listId, searchId, actionType}) {
        const modal = document.getElementById(modalId);
        if (!modal) return;
        const fileListDiv = modal.querySelector('#' + listId);
        const fileSearch = modal.querySelector('#' + searchId);
        let files = [];
        function renderFileList(filter = "") {
            fileListDiv.innerHTML = '';
            files.filter(f => f.toLowerCase().includes(filter.toLowerCase())).forEach(file => {
                const item = document.createElement('div');
                item.className = 'd-flex justify-content-between align-items-center mb-1';
                if (actionType === 'download') {
                    item.innerHTML = `<span>${file}</span> <a href="/misc/download/${encodeURIComponent(file)}" class="btn btn-success btn-sm">Download</a>`;
                } else if (actionType === 'delete') {
                    item.innerHTML = `<span>${file}</span> <form method="post" action="/misc/delete/${encodeURIComponent(file)}" style="display:inline;"><button type="submit" class="btn btn-danger btn-sm" onclick="return confirm('Delete this file?');">Delete</button></form>`;
                }
                fileListDiv.appendChild(item);
            });
            if (fileListDiv.innerHTML === '') {
                fileListDiv.innerHTML = '<div class="text-muted">No files found.</div>';
            }
        }
        modal.addEventListener('show.bs.modal', function () {
            fetch('/misc/files_list').then(r => r.json()).then(data => {
                files = data.files || [];
                renderFileList();
            });
        });
        if (fileSearch) {
            fileSearch.addEventListener('input', function() {
                renderFileList(this.value);
            });
        }
    }
    setupFileModal({
        modalId: 'downloadModal',
        listId: 'fileList',
        searchId: 'fileSearch',
        actionType: 'download'
    });
    setupFileModal({
        modalId: 'deleteModal',
        listId: 'deleteFileList',
        searchId: 'deleteFileSearch',
        actionType: 'delete'
    });
});
