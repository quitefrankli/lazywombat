# Loft

Filesystem-backed publishing for raw, Markdown, and gallery posts under `/loft`.

## Storage

Posts live at:

`~/.nabicat/data/loft/projects/<project>/<post>/`

Every post has `meta.json` with:

- `template`: `markdown`, `gallery`, or `raw`
- `owner`: username
- `title`
- `date`

Templated posts keep their source of truth beside rendered `index.html`:

- Markdown: `source.md`
- Gallery: `gallery.json` containing `title`, `description`, and `images`
- Gallery originals: post directory
- Gallery WebP thumbnails: `thumbs/<filename>.webp`

`DataInterface.get_post_content` re-renders templated posts on every view. Renderer and style changes therefore propagate to existing posts without resaving them.

Pydantic field aliases preserve the on-disk format; for example, `PostMeta.template_data` serializes as `template-data`.

## Authorization and quotas

- A post owner or an admin may edit and delete it.
- Legacy posts without an `owner` are admin-only.
- Per-user quotas are configured by `loft_non_admin_quota_bytes` and `loft_admin_quota_bytes`.
- Gallery thumbnail size is configured by `loft_gallery_thumb_max_px`.

## Concurrent metadata writes

Gunicorn workers are separate processes. All read-modify-write operations on Loft metadata must use `DataInterface.edit_meta`, which wraps the shared `edit_model` path lock.

- Use load/get methods only for read-only operations.
- Do not use bare save methods for read-modify-write.
- Do not nest `edit_meta` calls for the same file. A nested edit reloads from disk and cannot see the outer block's uncommitted changes; mutate the yielded model directly.
- Complete uploads, image processing, and thumbnail generation before entering the edit block. Never hold the distributed lock across slow I/O.
- A clean edit saves only when serialized data changed. Exceptions discard the mutation.

## Gallery loading

Do not render every real image URL directly into gallery HTML. Follow the established lazy-loading pattern:

- Render a tiny placeholder in `src`.
- Put the real URL in a data attribute.
- Load visible images with `IntersectionObserver`.
- Serialize requests with a small stagger.
- Retry failed loads with a cache-busting query parameter.
- Define stagger and retry constants on `ConfigManager`.
