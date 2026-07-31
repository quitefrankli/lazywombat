document.addEventListener("DOMContentLoaded", function() {
    document.querySelectorAll("form[data-confirm]").forEach(form => {
        form.addEventListener("submit", event => {
            if (!window.confirm(form.dataset.confirm)) event.preventDefault();
        });
    });

    const openParam = new URLSearchParams(window.location.search).get("open");
    if (openParam) {
        history.replaceState(null, "", window.location.pathname);
    }

    const treeProjects = document.querySelectorAll(".tree-project");

    treeProjects.forEach(function(project) {
        const treeItem = project.closest(".tree-item");
        const posts = treeItem.querySelector(".tree-posts");

        const shouldOpen = project.classList.contains("active")
            || posts.querySelector(".tree-post.active")
            || project.dataset.project === openParam;

        if (shouldOpen) {
            treeItem.classList.add("expanded");
            posts.classList.add("show");
        }

        project.addEventListener("click", function(e) {
            e.preventDefault();
            treeItem.classList.toggle("expanded");
            posts.classList.toggle("show");
        });
    });

    const galleryForms = document.querySelectorAll("[data-gallery-upload-form]");
    galleryForms.forEach(function(galleryForm) {
        const progressWrap = galleryForm.querySelector("[data-gallery-upload-progress]")
            || document.querySelector("[data-gallery-upload-progress]");
        if (!progressWrap) return;
        const progressBar = progressWrap.querySelector("[data-gallery-upload-bar]");
        const progressTrack = progressWrap.querySelector(".loft-upload-progress-track");
        const progressStatus = progressWrap.querySelector("[data-gallery-upload-status]");
        const submitButton = galleryForm.querySelector('[type="submit"]')
            || (galleryForm.id ? document.querySelector(`[type="submit"][form="${galleryForm.id}"]`) : null);
        const setProgress = value => {
            progressBar.style.width = value + "%";
            progressTrack.setAttribute("aria-valuenow", String(value));
        };

        galleryForm.addEventListener("submit", function(e) {
            e.preventDefault();
            progressWrap.hidden = false;
            setProgress(0);
            progressStatus.textContent = "Uploading...";
            if (submitButton) {
                submitButton.disabled = true;
                submitButton.dataset.originalText = submitButton.textContent;
                submitButton.textContent = "Uploading...";
            }

            const xhr = new XMLHttpRequest();
            xhr.open("POST", galleryForm.action);
            xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
            xhr.upload.addEventListener("progress", function(event) {
                if (!event.lengthComputable) return;
                setProgress(Math.round((event.loaded / event.total) * 100));
                if (event.loaded === event.total) {
                    progressStatus.textContent = "Processing media...";
                    if (submitButton) submitButton.textContent = "Processing...";
                }
            });
            xhr.addEventListener("load", function() {
                let data = {};
                try {
                    data = JSON.parse(xhr.responseText);
                } catch (err) {
                    data = {};
                }
                if (xhr.status >= 200 && xhr.status < 300 && data.redirect_url) {
                    setProgress(100);
                    progressStatus.textContent = "Saved.";
                    window.location.assign(data.redirect_url);
                    return;
                }
                progressStatus.textContent = data.error || "Upload failed.";
                if (submitButton) {
                    submitButton.disabled = false;
                    submitButton.textContent = submitButton.dataset.originalText || "Update post";
                }
            });
            xhr.addEventListener("error", function() {
                progressStatus.textContent = "Upload failed.";
                if (submitButton) {
                    submitButton.disabled = false;
                    submitButton.textContent = submitButton.dataset.originalText || "Update post";
                }
            });
            xhr.send(new FormData(galleryForm));
        });
    });

    const setupGalleryImages = function() {
        const gallery = document.querySelector(".loft-gallery");
        const galleryImages = Array.from(document.querySelectorAll("img[data-gallery-src]"));
        if (!gallery || galleryImages.length === 0) return;

        const staggerMs = Number(gallery.dataset.galleryStaggerMs);
        const maxRetries = Number(gallery.dataset.galleryMaxRetries);
        const retryDelayMs = Number(gallery.dataset.galleryRetryDelayMs);
        if ([staggerMs, maxRetries, retryDelayMs].some(Number.isNaN)) return;
        const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

        const loadImageAttempt = (img, url) => new Promise((resolve, reject) => {
            const onLoad = () => {
                img.removeEventListener("load", onLoad);
                img.removeEventListener("error", onError);
                resolve();
            };
            const onError = () => {
                img.removeEventListener("load", onLoad);
                img.removeEventListener("error", onError);
                reject(new Error("gallery image load failed"));
            };

            img.addEventListener("load", onLoad);
            img.addEventListener("error", onError);
            img.src = url;
        });

        const loadWithRetries = async (img, gallerySrc) => {
            for (let attempt = 0; attempt <= maxRetries; attempt++) {
                const suffix = attempt > 0
                    ? `${gallerySrc.includes("?") ? "&" : "?"}retry=${attempt}&_ts=${Date.now()}`
                    : "";
                try {
                    await loadImageAttempt(img, `${gallerySrc}${suffix}`);
                    return;
                } catch (_) {
                    if (attempt >= maxRetries) return;
                    await sleep(retryDelayMs * (attempt + 1));
                }
            }
        };

        const queue = [];
        const pending = new Set(galleryImages);
        let isProcessing = false;

        const processQueue = async () => {
            if (isProcessing) return;
            isProcessing = true;
            while (queue.length > 0) {
                const img = queue.shift();
                if (img && img.dataset.gallerySrc) {
                    await loadWithRetries(img, img.dataset.gallerySrc);
                    await sleep(staggerMs);
                }
            }
            isProcessing = false;
        };

        const enqueueImage = img => {
            if (!pending.has(img)) return;
            pending.delete(img);
            queue.push(img);
            processQueue();
        };

        if ("IntersectionObserver" in window) {
            const observer = new IntersectionObserver(entries => {
                for (const entry of entries) {
                    if (!entry.isIntersecting) continue;
                    observer.unobserve(entry.target);
                    enqueueImage(entry.target);
                }
            }, { root: null, rootMargin: "200px 0px", threshold: 0.01 });
            galleryImages.forEach(img => observer.observe(img));
        } else {
            galleryImages.forEach(img => enqueueImage(img));
        }
    };
    setupGalleryImages();

    const videos = Array.from(document.querySelectorAll("[data-loft-video]"));
    const syncSoundButton = video => {
        const container = video.closest(".loft-gallery-video, .loft-edit-tile");
        const button = container && container.querySelector("[data-video-sound]");
        if (!button) return;
        const icon = button.querySelector("i");
        button.setAttribute("aria-pressed", String(!video.muted));
        button.setAttribute("aria-label", video.muted ? "Unmute video" : "Mute video");
        icon.classList.toggle("bi-volume-mute-fill", video.muted);
        icon.classList.toggle("bi-volume-up-fill", !video.muted);
    };

    videos.forEach(video => {
        video.muted = true;
        syncSoundButton(video);
        const container = video.closest(".loft-gallery-video, .loft-edit-tile");
        const button = container && container.querySelector("[data-video-sound]");
        if (button) {
            button.addEventListener("click", () => {
                const shouldUnmute = video.muted;
                videos.forEach(other => {
                    other.muted = true;
                    syncSoundButton(other);
                });
                if (shouldUnmute) {
                    video.muted = false;
                    video.play().catch(() => {});
                }
                syncSoundButton(video);
            });
        }
        if (video.hasAttribute("data-video-expand")) {
            video.addEventListener("click", () => {
                const expanded = container.classList.toggle("is-expanded");
                document.body.classList.toggle("loft-video-expanded", expanded);
            });
        }
    });
    document.addEventListener("keydown", event => {
        if (event.key !== "Escape") return;
        const expanded = document.querySelector(".loft-gallery-video.is-expanded");
        if (!expanded) return;
        expanded.classList.remove("is-expanded");
        document.body.classList.remove("loft-video-expanded");
    });

    const reorderGrid = document.querySelector("[data-reorder-grid]");
    const mediaOrderInput = document.querySelector("[data-media-order]");
    if (reorderGrid && mediaOrderInput) {
        const tiles = () => Array.from(reorderGrid.querySelectorAll("[data-media-item]"));
        const syncOrder = () => {
            const current = tiles();
            mediaOrderInput.value = JSON.stringify(current.map(tile => tile.dataset.mediaItem));
            current.forEach((tile, index) => {
                tile.querySelector("[data-reorder-previous]").disabled = index === 0;
                tile.querySelector("[data-reorder-next]").disabled = index === current.length - 1;
            });
        };

        reorderGrid.addEventListener("click", event => {
            const previous = event.target.closest("[data-reorder-previous]");
            const next = event.target.closest("[data-reorder-next]");
            const button = previous || next;
            if (!button) return;
            const tile = button.closest("[data-media-item]");
            if (previous && tile.previousElementSibling) {
                reorderGrid.insertBefore(tile, tile.previousElementSibling);
            } else if (next && tile.nextElementSibling) {
                reorderGrid.insertBefore(tile.nextElementSibling, tile);
            }
            syncOrder();
            button.focus();
        });

        syncOrder();
    }

    // Gallery lightbox
    const galleryButtons = document.querySelectorAll(".loft-gallery-photo-btn");
    if (galleryButtons.length > 0) {
        const lb = document.createElement("div");
        lb.className = "loft-lightbox";
        lb.innerHTML = '<span class="loft-lightbox-close" aria-label="Close">&times;</span><img alt="">';
        document.body.appendChild(lb);
        const lbImg = lb.querySelector("img");
        const close = () => { lb.classList.remove("open"); lbImg.src = ""; };

        galleryButtons.forEach(btn => {
            btn.addEventListener("click", () => {
                lbImg.src = btn.dataset.full;
                lb.classList.add("open");
            });
        });
        lb.querySelector(".loft-lightbox-close").addEventListener("click", close);
        lb.addEventListener("click", e => { if (e.target === lb) close(); });
        document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
    }

    const templateRadios = document.querySelectorAll('input[name="template"]');
    const templateFields = document.querySelectorAll("[data-template-field]");
    const syncTemplateFields = () => {
        const selected = document.querySelector('input[name="template"]:checked');
        if (!selected) return;
        templateFields.forEach(field => {
            field.hidden = field.dataset.templateField !== selected.value;
        });
    };
    templateRadios.forEach(radio => radio.addEventListener("change", syncTemplateFields));
    syncTemplateFields();

    const existingProject = document.getElementById("project-existing");
    const newProject = document.getElementById("project-new");
    if (existingProject && newProject) {
        existingProject.addEventListener("change", () => {
            if (existingProject.value) newProject.value = "";
        });
        newProject.addEventListener("input", () => {
            if (newProject.value) existingProject.value = "";
        });
    }
});
