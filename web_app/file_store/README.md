# File Store

Authenticated file and folder management under `/file_store`, including uploads, downloads, thumbnails, moves, and bulk deletion.

- Metadata writes use `DataInterface.edit_metadata`; load methods are read-only.
- Do slow upload/archive/image work before entering the metadata edit lock.
- Image grids use placeholder sources and the lazy-loading, stagger, retry, and cache-busting behavior in `static/script.js`.
