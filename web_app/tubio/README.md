# Tubio

Audio search, download, upload, trimming, playback, discovery, and playlist management under `/tubio`.

## Responsibilities

`audio_downloader.py` handles YouTube search, video metadata, downloads, conversion, and progress reporting. The blueprint also supports direct uploads, trimming, playback, discovery, cached audio, thumbnails, and playlists.

Search runs the ordered duration fallback tiers configured by `TubioConfig.search_length_filter_sps`. Search limits, download-progress settings, retry behavior, media limits, and model names also belong in configuration rather than at call sites.

## Multi-worker state

Gunicorn workers are separate processes. Tubio download progress is stored in Redis rather than an in-process dictionary so a polling request can read progress written by any worker.

- Progress records use the configured TTL and expire if a failed download cannot clean them up.
- Treat Redis as a hard runtime dependency; do not add module-level shared request state.

## Concurrent metadata writes

All read-modify-write operations on Tubio metadata must use `DataInterface.edit_metadata`, which wraps the shared `edit_model` path lock.

- Use load/get methods only for read-only operations.
- Do not use bare save methods for read-modify-write.
- Do not nest `edit_metadata` calls for the same file. A nested edit reloads from disk and cannot see the outer block's uncommitted changes; mutate the yielded model directly.
- Perform downloads, uploads, ffmpeg transcoding, trimming, and other slow media work before entering the edit block. Never hold the distributed lock across slow I/O.
- A clean edit saves only when serialized metadata changed. Exceptions discard the mutation.

The lock is path-keyed and shared through Redis, so it protects the complete load-mutate-save span across gunicorn workers.
