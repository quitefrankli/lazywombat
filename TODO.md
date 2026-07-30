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
