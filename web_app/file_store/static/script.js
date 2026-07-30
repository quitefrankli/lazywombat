function joinPath(parent, name) {
    return parent ? `${parent}/${name}` : name;
}

function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function setupFolderUpload() {
    const form = document.getElementById('uploadForm');
    if (!form) return;
    const fileInput = document.getElementById('fileInput');
    const folderInput = document.getElementById('folderInput');
    const archiveInput = document.getElementById('archiveInput');
    const currentPath = document.querySelector('.file-store-shell').dataset.currentPath;
    const supportsFolderPicker = 'webkitdirectory' in folderInput;
    if (!supportsFolderPicker) {
        folderInput.disabled = true;
    }

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const selectedFiles = Array.from(fileInput.files || []);
        const selectedFolder = Array.from(folderInput.files || []);
        const archive = archiveInput.files?.[0];
        if (!selectedFiles.length && !selectedFolder.length && !archive) return;
        if (archive && (selectedFiles.length || selectedFolder.length)) {
            alert('Upload either files, a folder, or a ZIP archive.');
            return;
        }

        const data = new FormData();
        data.append('csrf_token', csrfToken());
        data.append('base_path', currentPath);
        if (archive) {
            data.append('folder_archive', archive);
        } else {
            const files = selectedFolder.length ? selectedFolder : selectedFiles;
            files.forEach((file) => {
                const relativePath = selectedFolder.length
                    ? joinPath(currentPath, file.webkitRelativePath)
                    : joinPath(currentPath, file.name);
                data.append('file', file, relativePath);
            });
        }

        const progress = document.getElementById('uploadProgress');
        const progressBar = document.getElementById('uploadProgressBar');
        const status = document.getElementById('uploadStatus');
        const button = document.getElementById('uploadBtn');
        progress.classList.remove('d-none');
        button.disabled = true;
        const xhr = new XMLHttpRequest();
        xhr.upload.addEventListener('progress', (update) => {
            if (update.lengthComputable) {
                const percent = Math.round((update.loaded / update.total) * 100);
                progressBar.style.width = `${percent}%`;
                progressBar.textContent = `${percent}%`;
            }
        });
        xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) {
                window.location.reload();
                return;
            }
            status.textContent = xhr.responseJSON?.error || xhr.responseText || 'Upload failed.';
            button.disabled = false;
        });
        xhr.addEventListener('error', () => {
            status.textContent = 'Upload failed. Check your connection and try again.';
            button.disabled = false;
        });
        xhr.open('POST', form.action);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.send(data);
    });
}

function setupBulkActions() {
    const selectAll = document.getElementById('selectAllFiles');
    const actions = document.getElementById('fileBulkActions');
    const checkboxes = Array.from(document.querySelectorAll('.file-selection-checkbox'));
    if (!selectAll || !actions || !checkboxes.length) return;

    const count = document.getElementById('selectedFileCount');
    const move = document.getElementById('moveSelectedFiles');
    const remove = document.getElementById('deleteSelectedFiles');
    const selectedPaths = () => checkboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
    const update = () => {
        const selected = selectedPaths();
        actions.hidden = selected.length === 0;
        count.textContent = `${selected.length} selected`;
        selectAll.checked = selected.length === checkboxes.length;
        selectAll.indeterminate = selected.length > 0 && selected.length < checkboxes.length;
    };
    const setPaths = (container, paths) => {
        container.replaceChildren(...paths.map((path) => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'paths';
            input.value = path;
            return input;
        }));
    };

    selectAll.addEventListener('change', () => {
        checkboxes.forEach((checkbox) => { checkbox.checked = selectAll.checked; });
        update();
    });
    checkboxes.forEach((checkbox) => checkbox.addEventListener('change', update));
    move.addEventListener('click', () => {
        setPaths(document.getElementById('bulkMovePaths'), selectedPaths());
        bootstrap.Modal.getOrCreateInstance(document.getElementById('bulkMoveModal')).show();
    });
    remove.addEventListener('click', () => {
        const paths = selectedPaths();
        setPaths(document.getElementById('bulkDeletePaths'), paths);
        document.getElementById('bulkDeleteMessage').textContent = `Permanently delete ${paths.length} selected item(s)? This cannot be undone.`;
        bootstrap.Modal.getOrCreateInstance(document.getElementById('bulkDeleteModal')).show();
    });
    update();
}

function setupImageModal() {
    const modal = document.getElementById('imageModal');
    const shell = document.querySelector('.file-store-shell');
    if (!modal || !shell) return;
    const image = document.getElementById('modalImage');
    const previews = Array.from(document.querySelectorAll('.file-grid-item [data-bs-toggle="modal"]'));
    let activeIndex = -1;

    const showImage = (index) => {
        if (index < 0 || index >= previews.length) return;
        activeIndex = index;
        const preview = previews[activeIndex];
        image.src = preview.dataset.imageUrl;
        image.alt = preview.dataset.imageName;
    };
    const moveImage = (offset) => showImage(activeIndex + offset);

    previews.forEach((preview, index) => {
        preview.addEventListener('click', () => showImage(index));
    });
    image.addEventListener('click', (event) => {
        const imageBounds = image.getBoundingClientRect();
        moveImage(event.clientX < imageBounds.left + imageBounds.width / 2 ? -1 : 1);
    });
    modal.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowLeft') {
            event.preventDefault();
            moveImage(-1);
        } else if (event.key === 'ArrowRight') {
            event.preventDefault();
            moveImage(1);
        }
    });
    modal.addEventListener('hidden.bs.modal', () => {
        activeIndex = -1;
        image.src = '';
    });
}

function setupStaggeredThumbnails() {
    const shell = document.querySelector('.file-store-shell');
    const images = Array.from(document.querySelectorAll('img[data-thumbnail-src]'));
    if (!shell || !images.length) return;

    const staggerMs = Number(shell.dataset.thumbnailStaggerMs);
    const maxRetries = Number(shell.dataset.thumbnailMaxRetries);
    const retryDelayMs = Number(shell.dataset.thumbnailRetryDelayMs);
    const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const load = (image, url) => new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = reject;
        image.src = url;
    });
    const loadWithRetries = async (image) => {
        const source = image.dataset.thumbnailSrc;
        for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
            try {
                const suffix = attempt ? `${source.includes('?') ? '&' : '?'}retry=${attempt}&_ts=${Date.now()}` : '';
                await load(image, `${source}${suffix}`);
                return;
            } catch (_) {
                if (attempt < maxRetries) await delay(retryDelayMs);
            }
        }
    };
    const queue = [];
    let processing = false;
    const processQueue = async () => {
        if (processing) return;
        processing = true;
        while (queue.length) {
            await loadWithRetries(queue.shift());
            await delay(staggerMs);
        }
        processing = false;
    };
    const enqueue = (image) => { queue.push(image); processQueue(); };
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                observer.unobserve(entry.target);
                enqueue(entry.target);
            });
        }, { rootMargin: '200px 0px', threshold: 0.01 });
        images.forEach((image) => observer.observe(image));
    } else {
        images.forEach(enqueue);
    }
}

function setupGalleryDensity() {
    const shell = document.querySelector('.file-store-shell');
    const gallery = document.querySelector('.file-directory.file-grid');
    const slider = document.getElementById('galleryColumns');
    const value = document.getElementById('galleryColumnsValue');
    if (!shell || !gallery || !slider || !value) return;

    const minColumns = Number(slider.min);
    const maxColumns = Number(slider.max);
    const minTilePx = Number(shell.dataset.galleryMinTilePx);
    let preferredColumns = Number(slider.value);

    const applyColumns = () => {
        const gapPx = Number.parseFloat(getComputedStyle(gallery).gap) || 0;
        const viewportMaximum = Math.floor((gallery.clientWidth + gapPx) / (minTilePx + gapPx));
        const availableMaximum = Math.max(minColumns, Math.min(maxColumns, viewportMaximum));
        const columns = Math.min(preferredColumns, availableMaximum);

        gallery.style.setProperty('--file-grid-columns', columns);
        slider.max = availableMaximum;
        slider.value = columns;
        value.value = `${columns} columns`;
        value.textContent = `${columns} columns`;
        slider.setAttribute('aria-valuetext', `${columns} columns`);
    };

    slider.addEventListener('input', () => {
        preferredColumns = Number(slider.value);
        applyColumns();
    });
    window.addEventListener('resize', applyColumns);
    applyColumns();
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.storage-fill[data-usage-percent]').forEach((fill) => {
        fill.style.width = `${Number(fill.dataset.usagePercent) || 0}%`;
    });
    setupFolderUpload();
    setupBulkActions();
    setupImageModal();
    setupStaggeredThumbnails();
    setupGalleryDensity();
});
