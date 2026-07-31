# TODO

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
