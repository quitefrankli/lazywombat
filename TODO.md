# TODO

## Misc

* make repos private
* remove "user documents"
* convert nabicat to use uv instead of mamba+pypi

## Tubio streaming support

- [ ] Add immediate playback for uncached YouTube tracks using their native browser-compatible audio stream.
  - Reserve the audio metadata and add the track to the user's playlist before the full media download completes, reusing the collision-safe reservation approach used by Surprise playlists.
  - Continue serving cached tracks through the existing local M4A byte-range response.
  - For uncached tracks, use yt-dlp to resolve a native M4A/AAC source without downloading it and prototype a direct redirect behind a `ConfigManager.tubio` feature flag.
  - Keep resolved signed media URLs short-lived in Redis; never persist or log them.
  - Re-resolve expired or rejected upstream URLs without interrupting cached playback.

- [ ] Validate the direct-streaming prototype across supported playback scenarios.
  - Test Chrome, Safari, and iOS Safari, including seeking, playback trims, playlist handoff, background playback, screen lock, expired URLs, upstream 403 responses, and tracks without a compatible native M4A source.
  - Measure time to first audio and server resource use against the current download-then-convert flow.
  - Confirm that authentication and authorization are enforced before exposing a temporary stream location.

- [ ] Decouple background caching and conversion from playback.
  - Keep `is_cached` as durable metadata and store transient resolving, caching, and failure state in Redis.
  - Use an idempotent Redis-backed job claim so concurrent requests do not download or convert the same track repeatedly.
  - Run materialization outside synchronous Gunicorn request workers, then atomically publish the completed local file and update metadata through `DataInterface.edit_metadata`.
  - Preserve explicit file downloads and future playback from the local cache even when first playback used the remote stream.

- [ ] Adapt the Tubio player and prefetch behavior for streaming.
  - Allow regular and Surprise tracks to begin playback without waiting for `convertTrackForPlayback`.
  - Replace full-response blob prefetching with source resolution or a small initial range so the browser and server do not download the same track twice.
  - Show unobtrusive streaming/caching state without blocking playback or replacing the persistent audio element.

- [ ] If direct redirects are unreliable, move remote byte proxying to Nginx or a dedicated streaming service.
  - Do not hold one of the four synchronous Gunicorn workers for the duration of playback.
  - Forward and validate byte ranges, content length, content type, and upstream failures while keeping signed upstream URLs behind opaque short-lived identifiers.

- [ ] Treat live FFmpeg-to-fragmented-MP4 streaming as a later fallback, not the initial implementation.
  - Evaluate browser compatibility, seeking and trim behavior, disconnect cleanup, cache finalization, and fragmented MP4 recovery before adopting it.
  - Review YouTube's terms and applicable content rights for the intended deployment before enabling streaming.


## V2 apps

NabiCat's `v2_apps` are strongly coupled, independently packaged dependencies.
A v2 app may live in its own repository, but it is installed and versioned as
part of the NabiCat deployment and runs inside the NabiCat web application.

This model separates source ownership without requiring each subapp to become a
separate web service. V2 apps are trusted application code and do not represent
a security or process-isolation boundary.

### Package relationships

The design has three parts:

- **NabiCat host** discovers installed v2 apps, integrates them into the web
  application, and provides the concrete runtime services they need.
- **V2 app packages** own their routes, domain logic, templates, static assets,
  data models, and app-specific configuration.
- **NabiCat app SDK** owns the shared coupled framework: the canonical user and
  data types, filesystem documents, runtime object, and integration contract.

Both NabiCat and each v2 app depend on the SDK. V2 apps import its public APIs,
including `User`, `UsersFile`, `AppData`, `DataRoot`, `DataInterface`,
`DataSyncer`, `FilesystemDocuments`, and `CoupledRuntime`. Because they are
trusted NabiCat dependencies rather than host-neutral plugins, they may also use
explicit host integration types such as `ConfigManager` when runtime injection
is not available.

### SDK scope

The SDK should remain cohesive and stable. It defines app metadata,
compatibility information, service interfaces, common errors, testing support,
and the framework shared directly with NabiCat. The host still creates the
Flask application and supplies configured production resources.

Reusable functionality belongs in the SDK only when it is part of the subapp
integration contract. Larger domain-specific capabilities should remain in the
host or move into their own focused packages when genuine reuse justifies it.

### Host-provided services

Each packaged app declares the single `NABICAT_RUNTIME` capability. The host
constructs that `CoupledRuntime` with the app and host configuration, canonical
current user, app-scoped data paths and interface, Redis, sync/Git services, and
the operational adapters used by the app.

Those adapters include:

- authentication and access control;
- application logging and audit context;
- rate limiting;
- configuration;
- persistent model storage and distributed read-modify-write locking;
- namespaced ephemeral or Redis-backed state;
- host-integrated scheduling when a concrete app requires it.

This boundary lets NabiCat change its internal implementation without forcing
corresponding changes across every v2 app. It also allows v2 apps to use fake
host services in their own tests.

Persistent user storage follows `data/<app_id>/<User.folder>/...`; usernames do
not form filesystem paths. V2 app `DataInterface` subclasses participate in the
same host registry as built-in apps. Backup and account deletion enumerate
`get_all_data_interfaces()`, so each registered interface owns its normal sync
and deletion behavior without a separate plugin lifecycle or generic document
purge hook.

### Integration and presentation

An installed v2 app advertises enough metadata for NabiCat to register it and,
where applicable, present it in shared navigation or discovery surfaces. Its
templates and static assets remain inside its package while using NabiCat's
shared template hierarchy and design system.

Not every host feature must be part of the first SDK version. Integration points
such as scheduled work, sitemap entries, or special caching behavior should be
added only when concrete v2 apps require them.

### Configuration

Each v2 app owns a typed configuration definition alongside its code. NabiCat
constructs and exposes that configuration through its central configuration
system so deployment-time overrides and runtime policy remain host-controlled.
The rule that constants live in `web_app/config.py` applies to the host and
legacy subapps; v2 app constants live in the v2 app's configuration module and
are made available through `ConfigManager`.

### Compatibility and deployment

The SDK has an explicit compatibility version. NabiCat validates installed v2
apps at startup and should fail clearly when an app requires an incompatible
contract. Production deployments pin both SDK and v2 app versions or immutable
revisions, and deployment diagnostics should identify the installed app
versions.

This keeps v2 app development independent while preserving NabiCat's existing
canary, rollback, and reproducibility guarantees. Updating a v2 app is a normal,
reviewable dependency change in the NabiCat deployment.
