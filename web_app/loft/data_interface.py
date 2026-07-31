import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Optional

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.errors import APIError
from web_app.loft.image_processing import (
    identify_image_format,
    image_decoded_pixels,
    normalize_image_to_webp,
)
from web_app.redis_client import rmw_lock
from web_app.users import User
from web_app.logging_utils import log_event


_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PostType(str, Enum):
    RAW = "raw"
    MARKDOWN = "markdown"
    GALLERY = "gallery"


class PostVisibility(str, Enum):
    PUBLIC = "public"
    UNLISTED = "unlisted"
    RESTRICTED = "restricted"


class Project(BaseModel):
    name: str
    posts: list[str]


class GalleryItem(BaseModel):
    type: str = "image"
    filename: str
    has_audio: bool | None = None


class GalleryTemplateData(BaseModel):
    description: str = ""
    items: list[GalleryItem] = Field(default_factory=list)


class Gallery(BaseModel):
    """View object returned by get_gallery — flattens title in alongside the
    gallery's description and items for templates/renderers."""

    title: str = ""
    description: str = ""
    items: list[GalleryItem] = Field(default_factory=list)


class PostMeta(BaseModel):
    """The persisted shape of a single post's metadata in meta.json.

    `template_data` round-trips to/from the on-disk "template-data" key via its
    alias; gallery posts populate it, other types leave it None.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore", serialize_by_alias=True)

    type: PostType
    title: str = ""
    date: str = ""
    owner: str = ""
    visibility: PostVisibility = PostVisibility.PUBLIC
    template_data: Optional[GalleryTemplateData] = Field(default=None, alias="template-data")

    def to_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)


class PreparedGalleryUpload(BaseModel):
    media_type: str
    stem: str
    staged_path: Path
    has_audio: bool | None = None


class VideoInfo(BaseModel):
    duration: float | None = None
    video_index: int
    audio_index: int | None = None
    format_name: str = ""
    video_codec: str = ""
    video_codec_tag: str | None = None
    audio_codec: str | None = None
    audio_codec_tag: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    width: int = 0
    height: int = 0
    fps: float | None = None
    pixel_format: str | None = None
    sample_aspect_ratio: str | None = None
    rotation: float | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    is_hdr: bool = False
    video_stream_count: int = 1
    audio_stream_count: int = 0
    other_stream_count: int = 0
    chapter_count: int = 0
    private_metadata_keys: list[str] = Field(default_factory=list)


class StagedGallerySource(BaseModel):
    media_type: str
    stem: str
    display_name: str
    source_path: Path
    video_info: VideoInfo | None = None


class ProjectStore(BaseModel):
    posts: dict[str, PostMeta] = Field(default_factory=dict)


class MetaStore(BaseModel):
    """The persisted shape of the single global meta.json.

    `{"projects": {<project>: {"posts": {<post>: <PostMeta>}}}}`. Modeled so it
    can round-trip through edit_meta()/edit_model rather than raw-dict handling.
    """
    projects: dict[str, ProjectStore] = Field(default_factory=dict)


def make_raw_post(title: str, date: str, owner: str) -> PostMeta:
    return PostMeta(type=PostType.RAW, title=title, date=date, owner=owner)


def make_markdown_post(title: str, date: str, owner: str) -> PostMeta:
    return PostMeta(type=PostType.MARKDOWN, title=title, date=date, owner=owner)


def make_gallery_post(title: str, date: str, owner: str, description: str = "") -> PostMeta:
    return PostMeta(
        type=PostType.GALLERY,
        title=title,
        date=date,
        owner=owner,
        template_data=GalleryTemplateData(description=description),
    )


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s or "untitled"


class DataInterface(BaseDataInterface):
    def __init__(self):
        super().__init__()
        self._content_dir = ConfigManager().save_data_path / "loft"
        self.projects_dir = self._content_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})

    # ---------- listing / reading ----------

    def _post_sort_key(self, post_dir: Path) -> tuple:
        meta = self.get_post_meta(post_dir.parent.name, post_dir.name)
        return (meta.date, post_dir.name)

    @staticmethod
    def _can_see_nonpublic_posts(user: Optional[User]) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return bool(user.has_elevated_access())

    def _post_visible_in_listing(self, project: str, post: str, user: Optional[User]) -> bool:
        meta = self.get_post_meta(project, post)
        return meta.visibility == PostVisibility.PUBLIC or self._can_see_nonpublic_posts(user)

    def user_can_view(self, user: Optional[User], project: str, post: str) -> bool:
        meta = self.get_post_meta(project, post)
        return meta.visibility != PostVisibility.RESTRICTED or self._can_see_nonpublic_posts(user)

    def get_posts_by_project(self, user: Optional[User] = None) -> list[Project]:
        projects: list[Project] = []
        for project_dir in sorted(self.projects_dir.iterdir(), key=lambda p: p.name):
            if not project_dir.is_dir():
                continue
            post_dirs = [d for d in project_dir.iterdir() if d.is_dir()]
            posts = [
                d.name for d in sorted(post_dirs, key=self._post_sort_key, reverse=True)
                if self._post_visible_in_listing(project_dir.name, d.name, user)
            ]
            if posts:
                projects.append(Project(name=project_dir.name, posts=posts))
        return projects

    def get_post_content(self, project: str, post: str) -> str:
        post_dir = self._post_dir(project, post)
        meta = self.get_post_meta(project, post)
        if meta.type == PostType.MARKDOWN:
            src = post_dir / "source.md"
            if src.exists():
                return self._render_markdown_index(meta, src.read_text(encoding="utf-8"))
        elif meta.type == PostType.GALLERY:
            return self._render_gallery_index(meta, self.get_gallery(project, post))
        content_file = post_dir / "index.html"
        if not content_file.exists():
            raise FileNotFoundError(f"Content file not found for post {project}/{post}")
        return content_file.read_text(encoding="utf-8")

    def get_asset_path(self, project: str, post: str, filename: str) -> Path | None:
        asset_path = self._post_dir(project, post) / filename
        if not asset_path.resolve().is_relative_to(self.projects_dir.resolve()):
            return None
        return asset_path

    # ---------- meta ----------

    def _post_dir(self, project: str, post: str) -> Path:
        # Trust callers to pass slugs that match existing directories. Path traversal
        # guard: resolved path must stay inside projects_dir.
        path = self.projects_dir / project / post
        if not path.resolve().is_relative_to(self.projects_dir.resolve()):
            raise APIError("Invalid path")
        return path

    @property
    def meta_file(self) -> Path:
        return self._content_dir / "meta.json"

    def _read_meta_store(self) -> MetaStore:
        """Read-only load. For mutations use edit_meta() so the write is locked."""
        return self.load_model(self.meta_file, MetaStore, sync=False) or MetaStore()

    def edit_meta(self):
        """Transactional edit of the shared meta.json.

        `with di.edit_meta() as store: store.projects[...]...` — locks the file,
        loads fresh, saves on clean exit (only if changed). The blob is global
        (all users/posts), so this is a global lock; keep slow work (transcodes)
        outside the block.
        """
        return self.edit_model(self.meta_file, MetaStore, exclude_none=True)

    def _post_entry(self, project: str, post: str) -> Optional[PostMeta]:
        project_store = self._read_meta_store().projects.get(project)
        if project_store is None:
            return None
        return project_store.posts.get(post)

    def get_post_meta(self, project: str, post: str) -> PostMeta:
        # Missing entries (e.g. a post dir with only index.html and no meta
        # record) fall back to a raw post so the index.html renderer is used.
        return self._post_entry(project, post) or PostMeta(type=PostType.RAW)

    def write_post_meta(self, project: str, post: str, meta: PostMeta) -> None:
        with self.edit_meta() as store:
            store.projects.setdefault(project, ProjectStore()).posts[post] = meta

    @staticmethod
    def _post_in_store(store: MetaStore, project: str, post: str) -> Optional[PostMeta]:
        project_store = store.projects.get(project)
        return project_store.posts.get(post) if project_store else None

    def register_raw_post(self, project: str, post: str, title: str, owner: str, date: str) -> None:
        self.write_post_meta(project, post, make_raw_post(title, date, owner))

    def user_can_edit(self, user: Optional[User], project: str, post: str) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        owner = self.get_post_meta(project, post).owner
        if user.is_admin:
            return True
        return bool(owner) and owner == user.id

    # ---------- input validation ----------

    @staticmethod
    def _validate_text(value: str, field: str, max_chars: int) -> str:
        """Length-cap a user-supplied string and raise APIError if over budget."""
        if value is None:
            return ""
        if len(value) > max_chars:
            raise APIError(f"{field} is too long (max {max_chars} characters)")
        return value

    # ---------- slug helpers ----------

    def reserve_post_slug(self, project_slug: str, title: str) -> str:
        """Return the slug for `title` under `project_slug`, or raise APIError
        if a post with the same slug already exists in that project."""
        slug = slugify(title)
        if (self.projects_dir / project_slug / slug).exists():
            raise APIError(
                f'A post titled "{title}" already exists in project '
                f'"{project_slug}". Pick a different title.'
            )
        return slug

    # ---------- quota ----------

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def user_storage_bytes(self, username: str) -> int:
        total = 0
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for post_dir in project_dir.iterdir():
                if not post_dir.is_dir():
                    continue
                meta = self.get_post_meta(project_dir.name, post_dir.name)
                if meta.owner == username:
                    total += self._dir_size(post_dir)
        return total

    def quota_bytes(self, user: User) -> int:
        cfg = ConfigManager()
        return cfg.loft.admin_quota_bytes if user.has_elevated_access() else cfg.loft.non_admin_quota_bytes

    def check_quota(self, user: User, additional_bytes: int) -> None:
        used = self.user_storage_bytes(user.id)
        limit = self.quota_bytes(user)
        if used + additional_bytes > limit:
            raise APIError(
                f"Storage quota exceeded ({used + additional_bytes} > {limit} bytes). "
                f"Free up space by deleting existing posts."
            )

    # ---------- markdown ----------

    def create_markdown_post(self, user: User, project_input: str, title: str, source_md: str) -> tuple[str, str]:
        cfg = ConfigManager()
        self._validate_text(project_input, "Topic name", cfg.loft.project_slug_max_chars)
        title = self._validate_text(title, "Title", cfg.loft.title_max_chars)
        source_md = self._validate_text(source_md, "Markdown", cfg.loft.markdown_max_chars)
        project_slug = slugify(project_input)
        if not title.strip():
            raise APIError("Title is required")
        post_slug = self.reserve_post_slug(project_slug, title)
        post_dir = self.projects_dir / project_slug / post_slug

        body_bytes = len(source_md.encode("utf-8"))
        self.check_quota(user, body_bytes)

        post_dir.mkdir(parents=True, exist_ok=True)
        meta = make_markdown_post(
            title.strip(),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            user.id,
        )
        self.write_post_meta(project_slug, post_slug, meta)
        self.atomic_write(post_dir / "source.md", data=source_md, mode="w", encoding="utf-8")
        return project_slug, post_slug

    def update_markdown_post(self, project: str, post: str, title: str, source_md: str) -> None:
        cfg = ConfigManager()
        title = self._validate_text(title, "Title", cfg.loft.title_max_chars)
        source_md = self._validate_text(source_md, "Markdown", cfg.loft.markdown_max_chars)
        post_dir = self._post_dir(project, post)
        with self.edit_meta() as store:
            meta = self._post_in_store(store, project, post)
            if meta is None or meta.type != PostType.MARKDOWN:
                raise APIError("Post is not a markdown post")
            if not title.strip():
                raise APIError("Title is required")
            meta.title = title.strip()
            self.atomic_write(post_dir / "source.md", data=source_md, mode="w", encoding="utf-8")

    def get_markdown_source(self, project: str, post: str) -> str:
        src = self._post_dir(project, post) / "source.md"
        return src.read_text(encoding="utf-8") if src.exists() else ""

    # ---------- gallery ----------

    def create_gallery_post(self, user: User, project_input: str, title: str, description: str) -> tuple[str, str]:
        cfg = ConfigManager()
        self._validate_text(project_input, "Topic name", cfg.loft.project_slug_max_chars)
        title = self._validate_text(title, "Title", cfg.loft.title_max_chars)
        description = self._validate_text(description, "Description", cfg.loft.description_max_chars)
        project_slug = slugify(project_input)
        if not title.strip():
            raise APIError("Title is required")
        post_slug = self.reserve_post_slug(project_slug, title)
        post_dir = self.projects_dir / project_slug / post_slug
        post_dir.mkdir(parents=True, exist_ok=True)
        post_meta = make_gallery_post(
            title.strip(),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            user.id,
            description,
        )
        self.write_post_meta(project_slug, post_slug, post_meta)
        return project_slug, post_slug

    def get_gallery(self, project: str, post: str) -> Gallery:
        meta = self.get_post_meta(project, post)
        if meta.type != PostType.GALLERY:
            return Gallery()
        td = meta.template_data or GalleryTemplateData()
        return Gallery(
            title=meta.title,
            description=td.description,
            items=[item for item in td.items if item.filename],
        )

    def update_gallery_meta(
        self,
        project: str,
        post: str,
        title: str,
        description: str,
        media_order: list[str] | None = None,
    ) -> None:
        cfg = ConfigManager()
        title = self._validate_text(title, "Title", cfg.loft.title_max_chars)
        description = self._validate_text(description, "Description", cfg.loft.description_max_chars)
        with self.edit_meta() as store:
            meta = self._post_in_store(store, project, post)
            if meta is None or meta.type != PostType.GALLERY:
                raise APIError("Post is not a gallery post")
            if not title.strip():
                raise APIError("Title is required")
            meta.title = title.strip()
            td = meta.template_data or GalleryTemplateData()
            td.description = description
            if media_order is not None:
                current = [item for item in td.items if item.filename]
                by_filename = {item.filename: item for item in current}
                if (
                    len(by_filename) != len(current)
                    or len(media_order) != len(current)
                    or len(set(media_order)) != len(media_order)
                    or set(media_order) != set(by_filename)
                ):
                    raise APIError("Media order does not match the current gallery")
                td.items = [by_filename[filename] for filename in media_order]
            meta.template_data = td

    def add_gallery_images(self, user: User, project: str, post: str, files: list[FileStorage]) -> int:
        return self.add_gallery_media(user, project, post, files)

    def add_gallery_media(self, user: User, project: str, post: str, files: list[FileStorage]) -> int:
        post_dir = self._post_dir(project, post)
        meta = self._post_entry(project, post)
        if meta is None or meta.type != PostType.GALLERY:
            raise APIError("Post is not a gallery post")

        cfg = ConfigManager().loft
        uploads = [file for file in files if file and file.filename]
        if len(uploads) > cfg.gallery_max_files_per_upload:
            raise APIError(
                f"Too many media files "
                f"(max {cfg.gallery_max_files_per_upload} per upload)"
            )
        if not uploads:
            return 0

        staging_root = (
            cfg.gallery_staging_root
            or ConfigManager().temp_dir / cfg.gallery_staging_dirname
        )
        try:
            staging_root.mkdir(
                mode=cfg.gallery_staging_dir_mode,
                parents=True,
                exist_ok=True,
            )
            if staging_root.is_symlink() or not staging_root.is_dir():
                raise APIError("Gallery upload staging path is unsafe")
        except OSError as error:
            raise APIError(
                "Could not prepare gallery upload staging"
            ) from error
        self._cleanup_stale_gallery_staging(staging_root)
        with tempfile.TemporaryDirectory(dir=staging_root) as staging_dir_name:
            staging_dir = Path(staging_dir_name)
            prepared = self._prepare_gallery_uploads(uploads, staging_dir)
            if not prepared:
                return 0

            total_final_bytes = sum(
                item.staged_path.stat().st_size for item in prepared
            )
            storage_owner_id = meta.owner or user.id
            quota_user = self._quota_user_for_storage_owner(
                user,
                storage_owner_id,
            )
            # Serialize the short quota-check/finalize span per storage owner. Slow
            # decoding and transcoding has already completed outside this lock.
            try:
                with rmw_lock(
                    f"loft-quota:{storage_owner_id}",
                    timeout_s=cfg.gallery_quota_lock_timeout_s,
                    blocking_timeout_s=(
                        cfg.gallery_quota_lock_blocking_timeout_s
                    ),
                ):
                    self._recover_gallery_publish_journals(storage_owner_id)
                    self.check_quota(quota_user, total_final_bytes)
                    return self._publish_gallery_uploads(
                        project,
                        post,
                        post_dir,
                        prepared,
                        storage_owner_id,
                    )
            except TimeoutError as error:
                raise APIError(
                    "Gallery upload finalization is busy; try again"
                ) from error

    @staticmethod
    def _quota_user_for_storage_owner(
        acting_user: User,
        storage_owner_id: str,
    ) -> User:
        if storage_owner_id == acting_user.id:
            return acting_user
        try:
            owner = BaseDataInterface().load_users().get(storage_owner_id)
        except (OSError, ValueError) as error:
            log_event(
                "loft", "loft.gallery_owner_load_failed",
                level=logging.WARNING, user=storage_owner_id,
                exc_info=error, error_type=type(error).__name__,
            )
            owner = None
        # Falling back to non-elevated is conservative when an old post refers
        # to a user that no longer exists.
        return owner or User(storage_owner_id)

    @staticmethod
    def _cleanup_stale_gallery_staging(staging_root: Path) -> None:
        cutoff = time.time() - ConfigManager().loft.gallery_staging_max_age_s
        for entry in staging_root.iterdir():
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except FileNotFoundError:
                # Another worker may clean the same abandoned entry.
                continue
            except OSError as error:
                log_event(
                    "loft", "loft.gallery_staging_cleanup_failed",
                    level=logging.WARNING, path=entry.name,
                    exc_info=error, error_type=type(error).__name__,
                )

    def _prepare_gallery_uploads(
        self,
        files: list[FileStorage],
        staging_dir: Path,
    ) -> list[PreparedGalleryUpload]:
        cfg = ConfigManager().loft
        staged_sources: list[StagedGallerySource] = []
        total_source_bytes = 0
        total_image_pixels = 0
        video_count = 0
        max_file_bytes = max(
            cfg.gallery_image_max_upload_bytes,
            cfg.gallery_video_max_upload_bytes,
        )

        for index, upload in enumerate(files):
            safe_name = secure_filename(upload.filename or "")
            display_name = safe_name or "upload"
            stem = (Path(display_name).stem or "upload")[
                :cfg.gallery_media_filename_max_chars
            ].rstrip(". ")
            stem = stem or "upload"
            source_path = staging_dir / f"{index}.source"
            source_bytes = self._spool_gallery_upload(
                upload,
                source_path,
                max_file_bytes=max_file_bytes,
                total_so_far=total_source_bytes,
            )
            total_source_bytes += source_bytes
            if source_bytes == 0:
                continue

            image_format = identify_image_format(source_path)
            if image_format is not None:
                if image_format not in cfg.gallery_image_allowed_formats:
                    raise APIError(
                        f"Unsupported image format for {display_name}: "
                        f"{image_format}"
                    )
                if source_bytes > cfg.gallery_image_max_upload_bytes:
                    raise APIError(f"Image {display_name} is too large")
                total_image_pixels += image_decoded_pixels(source_path)
                if (
                    total_image_pixels
                    > cfg.gallery_image_max_batch_pixels
                ):
                    raise APIError(
                        "Selected images exceed the decoded pixel budget"
                    )
                staged_sources.append(
                    StagedGallerySource(
                        media_type="image",
                        stem=stem,
                        display_name=display_name,
                        source_path=source_path,
                    )
                )
                continue

            if source_bytes > cfg.gallery_video_max_upload_bytes:
                raise APIError(f"Video {display_name} is too large")
            try:
                source_info = self._validate_video(source_path, display_name)
            except APIError as error:
                if str(error) in {
                    f"Could not process {display_name} as a video",
                    f"Unsupported video container for {display_name}",
                }:
                    raise APIError(
                        f"Unsupported or invalid media: {display_name}"
                    ) from error
                raise
            video_count += 1
            if video_count > cfg.gallery_max_videos_per_upload:
                raise APIError(
                    "Too many videos "
                    f"(max {cfg.gallery_max_videos_per_upload} per upload)"
                )
            staged_sources.append(
                StagedGallerySource(
                    media_type="video",
                    stem=stem,
                    display_name=display_name,
                    source_path=source_path,
                    video_info=source_info,
                )
            )

        prepared: list[PreparedGalleryUpload] = []
        for index, source in enumerate(staged_sources):
            if source.media_type == "image":
                image_data = self._normalize_gallery_image(
                    source.source_path,
                    source.display_name,
                )
                output_path = staging_dir / f"normalized-{index}.webp"
                self.atomic_write(output_path, data=image_data, mode="wb")
                prepared.append(
                    PreparedGalleryUpload(
                        media_type="image",
                        stem=source.stem,
                        staged_path=output_path,
                    )
                )
                continue

            source_info = source.video_info
            if source_info is None:
                raise APIError(
                    f"Could not process {source.display_name} as a video"
                )
            output_path = staging_dir / f"normalized-{index}.mp4"
            self._transcode_video(
                source.source_path,
                output_path,
                source.display_name,
                source_info,
            )
            output_info = self._validate_normalized_video(
                output_path,
                source.display_name,
            )
            if (
                output_info.duration is None
                or source_info.duration is None
                or output_info.duration
                + cfg.gallery_video_duration_tolerance_s
                < source_info.duration
            ):
                raise APIError(
                    f"Normalized video {source.display_name} is incomplete"
                )
            prepared.append(
                PreparedGalleryUpload(
                    media_type="video",
                    stem=source.stem,
                    staged_path=output_path,
                    has_audio=output_info.audio_index is not None,
                )
            )

        return prepared

    @staticmethod
    def _spool_gallery_upload(
        upload: FileStorage,
        destination: Path,
        *,
        max_file_bytes: int,
        total_so_far: int,
    ) -> int:
        cfg = ConfigManager().loft
        written = 0
        with destination.open("wb") as output:
            while True:
                chunk = upload.read(cfg.gallery_upload_stream_chunk_bytes)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_file_bytes:
                    raise APIError(f"Media file {upload.filename} is too large")
                if total_so_far + written > cfg.gallery_upload_max_total_bytes:
                    raise APIError(
                        "Selected media exceeds the total upload size limit"
                    )
                output.write(chunk)
        return written

    def _publish_gallery_uploads(
        self,
        project: str,
        post: str,
        post_dir: Path,
        prepared: list[PreparedGalleryUpload],
        storage_owner_id: str,
    ) -> int:
        moved_paths: list[Path] = []
        journal_path: Path | None = None
        try:
            with self.edit_meta() as store:
                meta = self._post_in_store(store, project, post)
                if (
                    meta is None
                    or meta.type != PostType.GALLERY
                    or (meta.owner or storage_owner_id) != storage_owner_id
                    or not post_dir.is_dir()
                ):
                    raise APIError("Post is not a gallery post")

                td = meta.template_data or GalleryTemplateData()
                existing_names = {
                    item.filename for item in td.items if item.filename
                }
                added: list[GalleryItem] = []
                destinations: list[tuple[PreparedGalleryUpload, str, Path]] = []
                for item in prepared:
                    extension = ".webp" if item.media_type == "image" else ".mp4"
                    filename = f"{item.stem}{extension}"
                    suffix = 2
                    while (
                        filename in existing_names
                        or (post_dir / filename).exists()
                    ):
                        filename = f"{item.stem}-{suffix}{extension}"
                        suffix += 1

                    destination = post_dir / filename
                    destinations.append((item, filename, destination))
                    existing_names.add(filename)

                cfg = ConfigManager().loft
                journal_path = post_dir / (
                    f"{cfg.gallery_publish_journal_prefix}"
                    f"{uuid.uuid4().hex}"
                    f"{cfg.gallery_publish_journal_suffix}"
                )
                self.atomic_write(
                    journal_path,
                    data=json.dumps(
                        {
                            "filenames": [
                                filename
                                for _, filename, _ in destinations
                            ]
                        }
                    ),
                    mode="w",
                    encoding="utf-8",
                )

                for item, filename, destination in destinations:
                    os.replace(item.staged_path, destination)
                    moved_paths.append(destination)
                    destination.chmod(0o644)
                    added.append(
                        GalleryItem(
                            type=item.media_type,
                            filename=filename,
                            has_audio=(
                                item.has_audio
                                if item.media_type == "video"
                                else None
                            ),
                        )
                    )

                td.items = [
                    item for item in td.items if item.filename
                ] + added
                meta.template_data = td
        except Exception as error:
            rollback_failed = False
            for moved_path in moved_paths:
                try:
                    self.atomic_delete(moved_path)
                except OSError as rollback_error:
                    rollback_failed = True
                    log_event(
                        "loft", "loft.gallery_rollback_failed",
                        level=logging.ERROR, path=moved_path.name,
                        exc_info=rollback_error,
                        error_type=type(rollback_error).__name__,
                    )
            if journal_path is not None and not rollback_failed:
                try:
                    self.atomic_delete(journal_path)
                except OSError as cleanup_error:
                    log_event(
                        "loft", "loft.gallery_journal_cleanup_failed",
                        level=logging.WARNING, path=journal_path.name,
                        exc_info=cleanup_error,
                        error_type=type(cleanup_error).__name__,
                    )
            if isinstance(error, OSError):
                raise APIError(
                    "Could not finalize gallery upload"
                ) from error
            raise

        if journal_path is not None:
            try:
                self.atomic_delete(journal_path)
            except OSError as error:
                # The committed metadata is authoritative. A later upload will
                # see that the journal's filenames are referenced and remove it.
                log_event(
                    "loft", "loft.gallery_journal_cleanup_failed",
                    level=logging.WARNING, path=journal_path.name,
                    committed=True, exc_info=error,
                    error_type=type(error).__name__,
                )

        return len(prepared)

    def _recover_gallery_publish_journals(
        self,
        storage_owner_id: str,
    ) -> None:
        cfg = ConfigManager().loft
        journal_pattern = (
            f"{cfg.gallery_publish_journal_prefix}"
            f"*{cfg.gallery_publish_journal_suffix}"
        )
        with self.edit_meta() as store:
            for project, project_store in store.projects.items():
                for post, meta in project_store.posts.items():
                    if (
                        meta.type != PostType.GALLERY
                        or (meta.owner or storage_owner_id)
                        != storage_owner_id
                    ):
                        continue
                    post_dir = self._post_dir(project, post)
                    if not post_dir.is_dir():
                        continue
                    referenced = {
                        item.filename
                        for item in (
                            meta.template_data or GalleryTemplateData()
                        ).items
                        if item.filename
                    }
                    for journal_path in post_dir.glob(journal_pattern):
                        try:
                            payload = json.loads(
                                journal_path.read_text(encoding="utf-8")
                            )
                            filenames = payload.get("filenames", [])
                            if not isinstance(filenames, list):
                                filenames = []
                            for filename in filenames:
                                if (
                                    not isinstance(filename, str)
                                    or Path(filename).name != filename
                                    or filename in referenced
                                ):
                                    continue
                                self.atomic_delete(post_dir / filename)
                            self.atomic_delete(journal_path)
                        except (OSError, ValueError, TypeError) as error:
                            log_event(
                                "loft", "loft.gallery_journal_recovery_failed",
                                level=logging.WARNING, path=journal_path.name,
                                exc_info=error, error_type=type(error).__name__,
                            )

    def delete_gallery_image(self, project: str, post: str, filename: str) -> None:
        self.delete_gallery_media(project, post, filename)

    def delete_gallery_media(self, project: str, post: str, filename: str) -> None:
        post_dir = self._post_dir(project, post)
        with self.edit_meta() as store:
            meta = self._post_in_store(store, project, post)
            if meta is None or meta.type != PostType.GALLERY:
                raise APIError("Post is not a gallery post")
            td = meta.template_data or GalleryTemplateData()
            if not any(item.filename == filename for item in td.items):
                raise APIError("Media not found in gallery")
            self.atomic_delete(post_dir / filename)
            td.items = [item for item in td.items if item.filename != filename]
            meta.template_data = td

    # ---------- thumbnails / video processing ----------

    def _normalize_gallery_image(
        self,
        source: Path,
        display_name: str,
    ) -> bytes:
        try:
            return normalize_image_to_webp(source)
        except APIError as error:
            log_event(
                "loft", "loft.image_normalization_failed",
                level=logging.WARNING, filename=display_name,
                reason="normalization_error",
                error_type=type(error).__name__,
            )
            if "pixel limit" in str(error):
                raise APIError(
                    f"Image {display_name} is too large to process"
                ) from error
            raise APIError(
                f"Could not process {display_name} as an image"
            ) from error

    def _make_thumbnail(self, src: Path, dst: Path) -> None:
        image_data = self._normalize_gallery_image(src, src.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.atomic_write(dst, data=image_data, mode="wb")

    @staticmethod
    def _run_media_command(cmd: list[str], timeout_s: int, error_message: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=True)
        except FileNotFoundError as e:
            raise APIError("Video processing requires ffmpeg") from e
        except subprocess.TimeoutExpired as e:
            raise APIError(error_message) from e
        except subprocess.CalledProcessError as e:
            log_event(
                "loft", "loft.media_command_failed",
                level=logging.WARNING, executable=Path(cmd[0]).name,
                returncode=e.returncode,
            )
            raise APIError(error_message) from e

    def _probe_video_info(self, src: Path, display_name: str) -> VideoInfo:
        cfg = ConfigManager()
        result = self._run_media_command(
            [
                "ffprobe",
                "-hide_banner",
                "-v", "error",
                "-max_alloc", str(cfg.loft.gallery_video_ffmpeg_max_alloc_bytes),
                "-protocol_whitelist", cfg.loft.gallery_video_protocol_whitelist,
                "-format_whitelist", ",".join(cfg.loft.gallery_video_allowed_demuxers),
                "-probesize", str(cfg.loft.gallery_video_probe_size_bytes),
                "-analyzeduration", str(cfg.loft.gallery_video_analyze_duration_us),
                "-max_pixels", str(cfg.loft.gallery_video_max_input_pixels),
                "-threads", str(cfg.loft.gallery_video_ffmpeg_threads),
                "-show_entries",
                (
                    "format=format_name,duration:"
                    "stream=index,codec_type,codec_name,codec_tag_string,duration,"
                    "duration_ts,time_base,width,height,avg_frame_rate,r_frame_rate,"
                    "pix_fmt,sample_aspect_ratio,sample_rate,channels,color_space,"
                    "color_transfer,color_primaries:"
                    "stream_disposition=default,attached_pic,timed_thumbnails,"
                    "metadata,dependent,still_image:"
                    "stream_tags:"
                    "stream_side_data=side_data_type,rotation:"
                    "format_tags:"
                    "chapter=id"
                ),
                "-of", "json",
                str(src),
            ],
            cfg.loft.gallery_video_probe_timeout_s,
            f"Could not process {display_name} as a video",
        )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as e:
            raise APIError(f"Could not process {display_name} as a video") from e
        streams = payload.get("streams", [])
        format_name = str(payload.get("format", {}).get("format_name") or "")
        detected_demuxers = set(format_name.split(","))
        if not detected_demuxers.intersection(
            cfg.loft.gallery_video_allowed_demuxers
        ):
            raise APIError(
                f"Unsupported video container for {display_name}"
            )

        invalid_codecs = {None, "", "none", "unknown", "bin_data"}

        def disposition_enabled(stream: dict, name: str) -> bool:
            try:
                return bool(int(stream.get("disposition", {}).get(name, 0)))
            except (TypeError, ValueError):
                return False

        video_candidates = [
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and stream.get("codec_name") not in invalid_codecs
            and self._positive_int(stream.get("width")) is not None
            and self._positive_int(stream.get("height")) is not None
            and not any(
                disposition_enabled(stream, flag)
                for flag in (
                    "attached_pic",
                    "timed_thumbnails",
                    "metadata",
                    "dependent",
                    "still_image",
                )
            )
        ]
        video = max(
            video_candidates,
            key=lambda stream: (
                disposition_enabled(stream, "default"),
                int(stream["width"]) * int(stream["height"]),
                -int(stream["index"]),
            ),
            default=None,
        )
        if video is None:
            raise APIError(f"Video {display_name} has no valid video stream")

        audio_candidates = [
            stream
            for stream in streams
            if stream.get("codec_type") == "audio"
            and stream.get("codec_name") not in invalid_codecs
            and str(stream.get("codec_tag_string") or "").lower() != "mebx"
            and self._positive_int(stream.get("sample_rate")) is not None
            and self._positive_int(stream.get("channels")) is not None
            and not any(
                disposition_enabled(stream, flag)
                for flag in ("metadata", "dependent")
            )
        ]
        audio = max(
            audio_candidates,
            key=lambda stream: (
                disposition_enabled(stream, "default"),
                -int(stream["index"]),
            ),
            default=None,
        )

        duration_candidates = [payload.get("format", {}).get("duration")]
        for selected_stream in (video, audio):
            if selected_stream is None:
                continue
            duration_candidates.extend(
                [
                    selected_stream.get("duration"),
                    selected_stream.get("tags", {}).get("DURATION"),
                    self._duration_from_time_base(selected_stream),
                ]
            )
        parsed_durations = [
            duration
            for duration in (
                self._parse_duration(candidate)
                for candidate in duration_candidates
            )
            if duration is not None
        ]
        duration = max(parsed_durations, default=None)

        frame_rates = [
            rate
            for rate in (
                self._frame_rate(video.get("avg_frame_rate")),
                self._frame_rate(video.get("r_frame_rate")),
            )
            if rate is not None
        ]
        fps = max(frame_rates, default=None)
        width = int(video["width"])
        height = int(video["height"])
        transfer = str(video.get("color_transfer") or "") or None
        side_data = video.get("side_data_list", [])
        rotation = None
        for rotation_candidate in [
            *(item.get("rotation") for item in side_data),
            video.get("tags", {}).get("rotate"),
        ]:
            try:
                parsed_rotation = float(rotation_candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed_rotation):
                rotation = parsed_rotation
                break
        has_dolby_vision = any(
            "dovi" in str(item.get("side_data_type") or "").lower()
            or "dolby vision" in str(item.get("side_data_type") or "").lower()
            for item in side_data
        )
        is_hdr = (
            transfer in cfg.loft.gallery_video_hdr_transfers
            or has_dolby_vision
        )

        video_stream_count = sum(
            stream.get("codec_type") == "video" for stream in streams
        )
        audio_stream_count = sum(
            stream.get("codec_type") == "audio" for stream in streams
        )
        metadata_keys = {
            str(key).lower()
            for tags in [
                payload.get("format", {}).get("tags", {}),
                video.get("tags", {}),
                audio.get("tags", {}) if audio is not None else {},
            ]
            for key in tags
        }
        private_metadata_keys = sorted(
            key
            for key in metadata_keys
            if any(
                fragment in key
                for fragment in cfg.loft.gallery_video_private_metadata_fragments
            )
        )
        return VideoInfo(
            duration=duration,
            video_index=int(video["index"]),
            audio_index=int(audio["index"]) if audio is not None else None,
            format_name=format_name,
            video_codec=str(video.get("codec_name") or ""),
            video_codec_tag=(
                str(video.get("codec_tag_string") or "") or None
            ),
            audio_codec=(
                str(audio.get("codec_name") or "") if audio is not None else None
            ),
            audio_codec_tag=(
                str(audio.get("codec_tag_string") or "")
                if audio is not None
                else None
            ),
            audio_sample_rate=(
                int(audio["sample_rate"]) if audio is not None else None
            ),
            audio_channels=(
                int(audio["channels"]) if audio is not None else None
            ),
            width=width,
            height=height,
            fps=fps,
            pixel_format=str(video.get("pix_fmt") or "") or None,
            sample_aspect_ratio=(
                str(video.get("sample_aspect_ratio") or "") or None
            ),
            rotation=rotation,
            color_transfer=transfer,
            color_primaries=(
                str(video.get("color_primaries") or "") or None
            ),
            color_space=str(video.get("color_space") or "") or None,
            is_hdr=is_hdr,
            video_stream_count=video_stream_count,
            audio_stream_count=audio_stream_count,
            other_stream_count=(
                len(streams) - video_stream_count - audio_stream_count
            ),
            chapter_count=len(payload.get("chapters", [])),
            private_metadata_keys=private_metadata_keys,
        )

    @staticmethod
    def _positive_int(value) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _positive_float(value) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    @classmethod
    def _parse_duration(cls, value) -> float | None:
        parsed = cls._positive_float(value)
        if parsed is not None or not isinstance(value, str):
            return parsed
        parts = value.split(":")
        if len(parts) != 3:
            return None
        hours = cls._positive_float(parts[0]) or 0
        minutes = cls._positive_float(parts[1]) or 0
        seconds = cls._positive_float(parts[2]) or 0
        total = hours * 3600 + minutes * 60 + seconds
        return total if total > 0 else None

    @classmethod
    def _duration_from_time_base(cls, stream: dict) -> float | None:
        duration_ts = cls._positive_float(stream.get("duration_ts"))
        time_base = cls._frame_rate(stream.get("time_base"))
        if duration_ts is None or time_base is None:
            return None
        return duration_ts * time_base

    @staticmethod
    def _frame_rate(value) -> float | None:
        if value in (None, "", "0/0", "N/A"):
            return None
        try:
            parsed = float(Fraction(str(value)))
        except (ValueError, ZeroDivisionError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    def _probe_video_duration(self, src: Path, display_name: str) -> float | None:
        return self._probe_video_info(src, display_name).duration

    def _validate_video(self, src: Path, display_name: str) -> VideoInfo:
        cfg = ConfigManager()
        info = self._probe_video_info(src, display_name)
        if info.duration is None:
            raise APIError(
                f"Video {display_name} has no reliable duration metadata"
            )
        if info.duration > cfg.loft.gallery_video_max_duration_s:
            raise APIError(
                f"Video {display_name} is too long "
                f"(max {cfg.loft.gallery_video_max_duration_s} seconds)"
            )
        if (
            info.width > cfg.loft.gallery_video_max_input_width_px
            or info.height > cfg.loft.gallery_video_max_input_height_px
            or info.width * info.height
            > cfg.loft.gallery_video_max_input_pixels
        ):
            raise APIError(f"Video {display_name} dimensions are too large")
        if (
            info.fps is not None
            and info.fps > cfg.loft.gallery_video_max_input_fps
        ):
            raise APIError(f"Video {display_name} frame rate is too high")
        return info

    def _validate_normalized_video(
        self,
        src: Path,
        display_name: str,
    ) -> VideoInfo:
        cfg = ConfigManager().loft
        try:
            output_bytes = src.stat().st_size
        except OSError as error:
            raise APIError(
                f"Could not inspect normalized video {display_name}"
            ) from error
        if output_bytes > cfg.gallery_video_max_output_bytes:
            raise APIError(
                f"Normalized video {display_name} exceeds the output size limit"
            )
        info = self._probe_video_info(src, display_name)
        if info.duration is None:
            raise APIError(
                f"Normalized video {display_name} has no reliable duration"
            )
        if (
            info.duration
            > cfg.gallery_video_max_duration_s
            + cfg.gallery_video_duration_tolerance_s
        ):
            raise APIError(
                f"Normalized video {display_name} is too long"
            )
        if cfg.gallery_video_output_demuxer not in set(
            info.format_name.split(",")
        ):
            raise APIError(f"Normalized video {display_name} is not MP4")
        if (
            info.video_codec != cfg.gallery_video_output_codec
            or info.video_codec_tag != cfg.gallery_video_output_codec_tag
            or info.pixel_format != cfg.gallery_video_output_pixel_format
        ):
            raise APIError(
                f"Normalized video {display_name} is not browser-safe H.264"
            )
        if info.width % 2 or info.height % 2:
            raise APIError(
                f"Normalized video {display_name} has invalid dimensions"
            )
        if info.sample_aspect_ratio not in (None, "0:1", "1:1"):
            raise APIError(
                f"Normalized video {display_name} has invalid aspect ratio"
            )
        if info.rotation not in (None, 0):
            raise APIError(
                f"Normalized video {display_name} retains rotation metadata"
            )
        if (
            info.width > cfg.gallery_video_max_width_px
            or info.height > cfg.gallery_video_max_height_px
            or info.width <= 0
            or info.height <= 0
        ):
            raise APIError(
                f"Normalized video {display_name} exceeds the output size limit"
            )
        if (
            info.fps is not None
            and info.fps > cfg.gallery_video_max_output_fps
        ):
            raise APIError(
                f"Normalized video {display_name} exceeds the frame rate limit"
            )
        if any(
            value != cfg.gallery_video_output_color_space
            for value in (
                info.color_space,
                info.color_transfer,
                info.color_primaries,
            )
        ):
            raise APIError(
                f"Normalized video {display_name} is not tagged as BT.709"
            )
        if (
            info.audio_index is not None
            and (
                info.audio_codec != cfg.gallery_video_output_audio_codec
                or info.audio_codec_tag
                != cfg.gallery_video_output_audio_codec_tag
            )
        ):
            raise APIError(
                f"Normalized video {display_name} does not use AAC audio"
            )
        if info.audio_index is not None and (
            info.audio_sample_rate
            != cfg.gallery_video_audio_sample_rate_hz
            or info.audio_channels > cfg.gallery_video_audio_channels
        ):
            raise APIError(
                f"Normalized video {display_name} has incompatible audio"
            )
        if (
            info.video_stream_count != 1
            or info.audio_stream_count > 1
            or info.other_stream_count
            or info.chapter_count
        ):
            raise APIError(
                f"Normalized video {display_name} contains unexpected streams"
            )
        if info.private_metadata_keys:
            raise APIError(
                f"Normalized video {display_name} retains private metadata"
            )
        if not self._mp4_has_faststart(src):
            raise APIError(
                f"Normalized video {display_name} is not optimized for streaming"
            )
        return info

    @staticmethod
    def _mp4_has_faststart(path: Path) -> bool:
        moov_offset = None
        mdat_offset = None
        try:
            with path.open("rb") as media:
                offset = 0
                while True:
                    header = media.read(8)
                    if len(header) != 8:
                        break
                    size = int.from_bytes(header[:4], "big")
                    atom_type = header[4:8]
                    header_size = 8
                    if size == 1:
                        extended_size = media.read(8)
                        if len(extended_size) != 8:
                            return False
                        size = int.from_bytes(extended_size, "big")
                        header_size = 16
                    elif size == 0:
                        media.seek(0, os.SEEK_END)
                        size = media.tell() - offset
                    if size < header_size:
                        return False
                    if atom_type == b"moov":
                        moov_offset = offset
                    elif atom_type == b"mdat":
                        mdat_offset = offset
                    if moov_offset is not None and mdat_offset is not None:
                        return moov_offset < mdat_offset
                    media.seek(offset + size)
                    offset += size
        except OSError:
            return False
        return False

    def _transcode_video(
        self,
        src: Path,
        dst: Path,
        display_name: str,
        info: VideoInfo,
    ) -> None:
        cfg = ConfigManager()
        max_width = cfg.loft.gallery_video_max_width_px
        max_height = cfg.loft.gallery_video_max_height_px
        filters = [
            (
                f"scale='max(2,trunc(iw*min(1,min({max_width}/iw,"
                f"{max_height}/ih))/2)*2)':"
                f"'max(2,trunc(ih*min(1,min({max_width}/iw,"
                f"{max_height}/ih))/2)*2)'"
            )
        ]
        if info.is_hdr:
            filters = [
                (
                    f"zscale=t=linear:npl="
                    f"{cfg.loft.gallery_video_hdr_peak_nits}"
                ),
                "format=gbrpf32le",
                (
                    f"tonemap={cfg.loft.gallery_video_hdr_tonemap_algorithm}:"
                    f"desat="
                    f"{cfg.loft.gallery_video_hdr_desaturation}"
                ),
                (
                    f"zscale=p={cfg.loft.gallery_video_output_color_space}:"
                    f"t={cfg.loft.gallery_video_output_color_space}:"
                    f"m={cfg.loft.gallery_video_output_color_space}:"
                    f"r={cfg.loft.gallery_video_output_color_range}"
                ),
            ] + filters
        elif any(
            value != cfg.loft.gallery_video_output_color_space
            for value in (
                info.color_space,
                info.color_transfer,
                info.color_primaries,
            )
        ):
            color_options = [
                f"all={cfg.loft.gallery_video_output_color_space}",
                f"format={cfg.loft.gallery_video_output_pixel_format}",
            ]
            if not all(
                (
                    info.color_space,
                    info.color_transfer,
                    info.color_primaries,
                )
            ):
                fallback = (
                    cfg.loft.gallery_video_sd_input_color_space
                    if info.height
                    <= cfg.loft.gallery_video_sd_max_height_px
                    else cfg.loft.gallery_video_hd_input_color_space
                )
                color_options.append(f"iall={fallback}")
            filters = [
                f"colorspace={':'.join(color_options)}"
            ] + filters
        filters.extend(
            [
                "setsar=1",
                f"format={cfg.loft.gallery_video_output_pixel_format}",
            ]
        )
        vf = ",".join(filters)
        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-max_alloc", str(cfg.loft.gallery_video_ffmpeg_max_alloc_bytes),
            "-protocol_whitelist", cfg.loft.gallery_video_protocol_whitelist,
            "-format_whitelist", ",".join(cfg.loft.gallery_video_allowed_demuxers),
            "-probesize", str(cfg.loft.gallery_video_probe_size_bytes),
            "-analyzeduration", str(cfg.loft.gallery_video_analyze_duration_us),
            "-max_pixels", str(cfg.loft.gallery_video_max_input_pixels),
            "-threads", str(cfg.loft.gallery_video_ffmpeg_threads),
            "-autorotate", "1",
            "-i", str(src),
            "-map", f"0:{info.video_index}",
        ]
        if info.audio_index is None:
            cmd.append("-an")
        else:
            cmd.extend(["-map", f"0:{info.audio_index}"])
        cmd.extend(
            [
                "-dn", "-sn",
                "-map_metadata", "-1",
                "-map_chapters", "-1",
                "-t", str(cfg.loft.gallery_video_max_duration_s),
                "-filter_threads", str(cfg.loft.gallery_video_ffmpeg_threads),
                "-vf", vf,
                "-c:v", cfg.loft.gallery_video_output_encoder,
                "-preset", cfg.loft.gallery_video_h264_preset,
                "-crf", str(cfg.loft.gallery_video_h264_crf),
                "-profile:v", cfg.loft.gallery_video_h264_profile,
                "-level:v", cfg.loft.gallery_video_h264_level,
                "-tag:v", cfg.loft.gallery_video_output_codec_tag,
                "-pix_fmt", cfg.loft.gallery_video_output_pixel_format,
                "-fpsmax", str(cfg.loft.gallery_video_max_output_fps),
                "-metadata:s:v:0", "rotate=0",
                "-color_primaries", cfg.loft.gallery_video_output_color_space,
                "-color_trc", cfg.loft.gallery_video_output_color_space,
                "-colorspace", cfg.loft.gallery_video_output_color_space,
                "-color_range", cfg.loft.gallery_video_output_color_range,
                "-c:a", cfg.loft.gallery_video_output_audio_encoder,
                "-profile:a", cfg.loft.gallery_video_output_audio_profile,
                "-ac", str(cfg.loft.gallery_video_audio_channels),
                "-ar", str(cfg.loft.gallery_video_audio_sample_rate_hz),
                "-b:a", cfg.loft.gallery_video_audio_bitrate,
                "-threads", str(cfg.loft.gallery_video_ffmpeg_threads),
                "-max_muxing_queue_size",
                str(cfg.loft.gallery_video_max_muxing_queue_packets),
                "-abort_on", "empty_output+empty_output_stream",
                "-movflags", "+faststart",
                "-fs", str(cfg.loft.gallery_video_max_output_bytes),
                "-f", cfg.loft.gallery_video_output_format,
                str(dst),
            ]
        )
        self._run_media_command(
            cmd,
            cfg.loft.gallery_video_transcode_timeout_s,
            f"Could not process {display_name} as a video",
        )

    # ---------- delete post ----------

    def delete_post(self, project: str, post: str) -> None:
        post_dir = self._post_dir(project, post)
        if not post_dir.exists():
            return
        shutil.rmtree(post_dir)
        with self.edit_meta() as store:
            project_store = store.projects.get(project)
            if project_store is not None:
                project_store.posts.pop(post, None)
                if not project_store.posts:
                    store.projects.pop(project, None)
        project_dir = post_dir.parent
        if project_dir.is_dir() and not any(project_dir.iterdir()):
            project_dir.rmdir()

    # ---------- rendering ----------

    def _render_markdown_index(self, meta: PostMeta, source_md: str) -> str:
        title = html.escape(meta.title)
        body = self._md.render(source_md or "")
        return (
            f'<article class="loft-post loft-md">'
            f'<header class="loft-post-header">'
            f'<h1>{title}</h1>'
            f'{self._render_byline(meta)}'
            f'</header>'
            f'<div class="loft-md-body">{body}</div>'
            f'</article>'
        )

    def _render_gallery_index(self, meta: PostMeta, gallery: Gallery) -> str:
        cfg = ConfigManager()
        title = html.escape(gallery.title or meta.title)
        description = html.escape(gallery.description)
        media_html = []
        for item in gallery.items:
            if not item.filename:
                continue
            name = html.escape(item.filename)
            if item.type == "video":
                sound_control = ""
                if item.has_audio:
                    sound_control = (
                        f'<button type="button" class="loft-video-sound" '
                        f'data-video-sound aria-label="Unmute video" aria-pressed="false">'
                        f'<i class="bi bi-volume-mute-fill" aria-hidden="true"></i>'
                        f'</button>'
                    )
                media_html.append(
                    f'<figure class="loft-gallery-photo loft-gallery-video">'
                    f'<video data-loft-video data-video-expand '
                    f'autoplay loop muted playsinline preload="metadata">'
                    f'<source src="{name}" type="video/mp4">'
                    f'</video>'
                    f'{sound_control}'
                    f'</figure>'
                )
            else:
                media_html.append(
                    f'<figure class="loft-gallery-photo">'
                    f'<button type="button" class="loft-gallery-photo-btn" data-full="{name}">'
                    f'<img loading="lazy" decoding="async" '
                    f'src="data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA=" '
                    f'data-gallery-src="{name}" alt="">'
                    f'</button>'
                    f'</figure>'
                )
        feed = "\n".join(media_html) if media_html else (
            '<p class="loft-gallery-empty">No media yet.</p>'
        )
        desc_block = f'<p class="loft-gallery-desc">{description}</p>' if description else ""
        return (
            f'<article class="loft-post loft-gallery" '
            f'data-gallery-stagger-ms="{cfg.loft.gallery_image_stagger_ms}" '
            f'data-gallery-max-retries="{cfg.loft.gallery_image_max_retries}" '
            f'data-gallery-retry-delay-ms="{cfg.loft.gallery_image_retry_delay_ms}">'
            f'<header class="loft-post-header">'
            f'<h1>{title}</h1>'
            f'{self._render_byline(meta)}'
            f'{desc_block}'
            f'</header>'
            f'<div class="loft-gallery-feed">{feed}</div>'
            f'</article>'
        )

    @staticmethod
    def _render_byline(meta: PostMeta) -> str:
        date = html.escape(meta.date[:10])
        owner = html.escape(meta.owner)
        if owner and date:
            inner = f'by <span class="loft-post-author">{owner}</span> &middot; {date}'
        elif owner:
            inner = f'by <span class="loft-post-author">{owner}</span>'
        elif date:
            inner = date
        else:
            return ""
        return f'<p class="loft-post-meta">{inner}</p>'

    # ---------- base hooks ----------

    def delete_user_data(self, user: User) -> None:
        with self.edit_meta() as store:
            for project, project_store in list(store.projects.items()):
                for post, meta in list(project_store.posts.items()):
                    if meta.owner == user.id:
                        shutil.rmtree(self._post_dir(project, post), ignore_errors=True)
                        project_store.posts.pop(post, None)
                if not project_store.posts:
                    store.projects.pop(project, None)
                    project_dir = self.projects_dir / project
                    if project_dir.is_dir() and not any(project_dir.iterdir()):
                        project_dir.rmdir()

    def backup_data(self, backup_dir: Path) -> None:
        if self._content_dir.exists():
            shutil.copytree(
                self._content_dir,
                backup_dir / "loft",
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    *ConfigManager().loft.gallery_backup_excluded_names
                ),
            )
