# TODO

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

NabiCat may be extended with independently maintained subapps, referred to as
`v2_apps`. A v2 app lives in its own repository and is distributed as an
installable Python package. It runs within the NabiCat web application so it can
participate in the existing authentication, UI, storage, logging, caching, and
operational environment.

This model separates source ownership without requiring each subapp to become a
separate web service. V2 apps are trusted application code and do not represent
a security or process-isolation boundary.

### Package relationships

The design has three parts:

- **NabiCat host** discovers installed v2 apps, integrates them into the web
  application, and provides the concrete runtime services they need.
- **V2 app packages** own their routes, domain logic, templates, static assets,
  data models, and app-specific configuration.
- **NabiCat subapp SDK** is a small shared Python package that defines the stable
  contract between the host and v2 apps.

Both NabiCat and each v2 app depend on the SDK. V2 apps should depend on the
SDK's public contract rather than importing arbitrary modules from the NabiCat
repository.

### SDK scope

The SDK should remain narrow and stable. It may define plugin metadata,
compatibility information, service interfaces, common errors, and testing
support. It should not own the Flask application, connect to production
infrastructure, select filesystem locations, or become a general collection of
unrelated helpers.

Reusable functionality belongs in the SDK only when it is part of the subapp
integration contract. Larger domain-specific capabilities should remain in the
host or move into their own focused packages when genuine reuse justifies it.

### Host-provided services

The host supplies v2 apps with supported capabilities rather than exposing its
internal modules directly. These capabilities can include:

- authentication and access control;
- application logging and audit context;
- rate limiting;
- configuration;
- persistent model storage and distributed read-modify-write locking;
- namespaced ephemeral or Redis-backed state;
- optional lifecycle facilities such as scheduling.

This boundary lets NabiCat change its internal implementation without forcing
corresponding changes across every v2 app. It also allows v2 apps to use fake
host services in their own tests.

Persistent storage should follow a host-managed, app-namespaced convention.
The host remains responsible for safe paths, locking, atomic writes, backups,
and user-data deletion. V2 apps own their data models and mutations but should
not need to reproduce those operational mechanisms.

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
