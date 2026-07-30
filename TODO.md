# TODO

## Codebase refactor and improvement roadmap

Current baseline: 384 non-FFmpeg unit tests pass, with 432 default tests and
49 separately collected UI tests.

### P0 — security-sensitive storage

- [ ] Replace plaintext user passwords with password hashes.
  - Centralize password creation and verification on the user model.
  - Transparently migrate legacy plaintext passwords after successful login.
  - Cover registration, login, account deletion, API authentication, migration,
    and incorrect-password behavior with tests before implementation.

- [ ] Protect sensitive files and Sentinel secrets.
  - Stop applying `0644` permissions to files containing credentials or other
    private data; use an appropriate private mode such as `0600`.
  - Never retain card CVVs.
  - Encrypt remembered Sentinel account and permitted payment data with a
    server-managed key, or move it to a dedicated secret store.
  - Define retention and deletion behavior for remembered secrets.

### P0 — browser caching and outbound-request security

- [ ] Restrict the service worker to an explicit cache allowlist.
  - Cache only versioned static assets and intentionally public media.
  - Never cache responses marked `private` or `no-store`, responses carrying
    `Set-Cookie`, authenticated pages, or authenticated JSON endpoints.
  - Clear user-scoped caches on logout and cache-version changes.
  - Add a browser test proving one user cannot receive another user's cached
    response.

- [ ] Harden the Proxy subapp against SSRF and hostile remote content.
  - Apply the public-target policy to the initial URL and every redirect.
  - Block private, loopback, link-local, reserved, and cloud metadata targets.
  - Remove the iframe's `allow-same-origin` plus `allow-scripts` combination;
    disable scripts or sanitize remote documents.
  - Add response-size and content-type limits.
  - Add focused SSRF, redirect, and hostile-script tests.

### P1 — production operations and application lifecycle

- [ ] Move automatic yt-dlp dependency updates out of the web process.
  - Do not let a gunicorn worker edit `requirements.txt`, commit, push, and
    trigger deployment.
  - Use a scheduled GitHub workflow or dependency-update bot.
  - Let production report an outdated dependency without mutating the checkout.

- [ ] Introduce a Flask application factory.
  - Add `create_app(settings)` to configure Flask, initialize extensions,
    register root routes and subapp blueprints, and install hooks and error
    handlers.
  - Make extension declarations unbound and initialize them with `init_app`.
  - Separate web startup, scheduler startup, CLI startup, and production entry.
  - Give each test an isolated application instead of mutating the global app.
  - Compute the static asset version once at startup rather than spawning Git
    from request handling.

### P2 — module boundaries and data access

- [ ] Split `hammock/data_interface.py` by responsibility.
  - Extract the repository, gallery upload transaction, media probe,
    normalization/transcoding, and rendering responsibilities.

- [ ] Split Sentinel's runner and routes by responsibility.
  - Extract run orchestration, browser observation, action execution, report
    building, and persistence.
  - Split run, batch, report, and screenshot routes into focused modules.

- [ ] Split Tubio's blueprint by feature.
  - Separate search, playlists, media routes, Surprise behavior, downloads, and
    caching into route and service modules.

- [ ] Break up shared `helpers.py`.
  - Separate authentication, encryption, LLM transports, extension declarations,
    and blueprint registration.

- [ ] Rename subapp `DataInterface` classes to domain-specific repositories.
  - Prefer names such as `HammockRepository` and `TubioRepository`.
  - Preserve the Redis-backed `edit_model()` read-modify-write pattern.
  - Make bare `save_*` methods private where possible so callers cannot bypass
    transactional edits.

### P2 — configuration, dependencies, and quality gates

- [ ] Make application configuration injectable and test-isolated.
  - Keep every named constant under `ConfigManager`, grouped by subapp.
  - Construct one settings object per application.
  - Prefer immutable production settings and modified copies for tests.
  - Move remaining hardcoded timeouts, scheduler values, rate limits, file
    modes, chunk sizes, and cache values into configuration.
  - Generate service-worker configuration instead of duplicating values between
    Python and JavaScript.

- [ ] Clean up dependency management.
  - Separate runtime dependencies from development and UI-test tooling.
  - Distinguish direct dependencies from transitively installed packages.
  - Adopt a reproducible lock or constraints strategy.

- [ ] Add incremental CI quality gates.
  - Add Ruff formatting and linting.
  - Add Pyright or mypy for newly touched modules first.
  - Add coverage reporting with a ratcheting threshold.
  - Add dependency vulnerability scanning.
  - Add a small Playwright smoke job for login, navigation, and a critical flow.
  - Add a check that rejects named constants introduced outside configuration.

### P3 — errors, logging, and UI consistency

- [ ] Replace broad exception handling as modules are touched.
  - Catch expected exceptions, log with module-level loggers, and let unexpected
    failures reach centralized Flask error handling.
  - Add typed domain exceptions and consistent HTTP error mapping.
  - Redact nested secrets and authorization data from request logs.

- [ ] Remove wildcard typing imports and improve type coverage incrementally.

- [ ] Move inline CSS and JavaScript out of templates.
  - Keep assets in each subapp's `static/` directory.
  - Replace hardcoded colors, radii, spacing, and transitions with Honeydew
    design tokens.

## Hammock gallery media support

### High priority

- [ ] Support Android Motion Photos.
  - Detect Motion Photo XMP/container metadata in JPEG, HEIC, and AVIF files.
  - Extract the appended MP4 or MOV without trusting filename extensions.
  - Preserve the still and motion components as one logical gallery item.
  - Retain the current metadata stripping, quota checks, and normalized-output validation.
  - Reference: <https://developer.android.com/media/platform/motion-photo-format>

- [ ] Normalize Ultra HDR and other HDR still images predictably.
  - Detect Android Ultra HDR JPEG gain maps.
  - Detect HDR/NCLX color information in HEIC and AVIF images.
  - Tone-map HDR sources to a consistent browser-safe SDR output.
  - Add fixtures covering valid, malformed, and missing gain-map metadata.
  - Reference: <https://developer.android.com/media/platform/hdr-image-format>

- [ ] Preserve Apple Live Photo relationships.
  - Recognize associated HEIC/JPEG and MOV components from an exported Live Photo.
  - Publish them as one logical gallery item rather than unrelated media.
  - Support batches containing multiple Live Photos without weakening worker resource limits.
  - Handle missing or mismatched components with a clear fallback or rejection.
  - Reference: <https://support.apple.com/en-us/104966>

### Medium priority

- [ ] Add Apple ProRAW and common camera RAW support.
  - Start with DNG/ProRAW, then evaluate CR3, NEF, and ARW.
  - Use a maintained RAW decoder and generate a metadata-free display rendition.
  - Apply stricter encoded-byte, decoded-pixel, memory, and processing-time limits.
  - Reject unsupported RAW variants with a clear error.
  - Reference: <https://support.apple.com/en-ie/119916>

- [ ] Define an explicit Apple ProRes upload policy.
  - Detect ProRes and ProRes RAW before transcoding.
  - Return a format-specific size error instead of a generic rejection.
  - Evaluate whether elevated users should receive a separate source-size limit.
  - Do not raise general upload limits without preserving worker and storage safety.
  - Reference: <https://support.apple.com/en-us/109041>

- [ ] Handle Apple spatial photos and videos intentionally.
  - Detect stereo HEIC and MV-HEVC with spatial metadata.
  - Decide whether Hammock should create a deterministic 2D rendition or reject spatial media.
  - Never silently publish an arbitrary eye or layer.
  - Add real spatial-media fixtures before enabling support.
  - Reference: <https://developer.apple.com/documentation/imageio/creating-spatial-photos-and-videos-with-spatial-metadata>

- [ ] Evaluate MTS, M2TS, and MPEG-TS input support.
  - Require content-based demuxer detection and standalone local media.
  - Continue rejecting playlists, remote references, and unsafe protocols.
  - Normalize accepted inputs to the existing browser-safe MP4 contract.

### Lower priority or product decisions

- [ ] Decide whether animated GIF, WebP, and AVIF should retain animation.
  - Current behavior intentionally publishes the first frame.
  - If enabled, define duration, frame-count, decoded-pixel, and output-size limits.

- [ ] Evaluate standalone JPEG XL support.
  - Confirm decoder maturity and resource-limit behavior before adding it.

- [ ] Keep SVG and PDF unsupported unless a sandboxed rasterization path is introduced.
  - Do not render active or externally referenced content directly in the gallery.
