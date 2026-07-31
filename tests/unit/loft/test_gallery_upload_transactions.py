"""Transactional behavior tests for Loft gallery uploads."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.errors import APIError
from web_app.loft.data_interface import DataInterface, GalleryItem, VideoInfo
from web_app.users import User


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    projects = tmp_path / "loft" / "projects"
    projects.mkdir(parents=True)

    def patched_init(self):
        from markdown_it import MarkdownIt

        self.projects_dir = projects
        self._content_dir = projects.parent
        self._md = MarkdownIt(
            "commonmark",
            {"html": False, "linkify": True, "breaks": True},
        )

    monkeypatch.setattr(DataInterface, "__init__", patched_init)
    return projects


@pytest.fixture
def gallery(projects_dir):
    data_interface = DataInterface()
    owner = User("alice", "x", "fa", is_admin=False)
    project, post = data_interface.create_gallery_post(
        owner,
        "Transactional uploads",
        "Gallery",
        "",
    )
    return data_interface, owner, project, post, projects_dir / project / post


def _image_upload(filename: str, color: tuple[int, int, int]) -> FileStorage:
    buffer = BytesIO()
    Image.new("RGB", (48, 32), color=color).save(buffer, format="PNG")
    buffer.seek(0)
    return FileStorage(
        stream=buffer,
        filename=filename,
        content_type="image/png",
    )


def _video_upload(filename: str, data: bytes) -> FileStorage:
    return FileStorage(
        stream=BytesIO(data),
        filename=filename,
        content_type="video/quicktime",
    )


def _gallery_filenames(
    data_interface: DataInterface,
    project: str,
    post: str,
) -> list[str]:
    return [
        item.filename
        for item in data_interface.get_gallery(project, post).items
    ]


def _mock_video_validation(monkeypatch) -> None:
    def valid_video(self, path, display_name):
        return VideoInfo(
            duration=0.25,
            video_index=0,
            audio_index=None,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="h264",
            width=320,
            height=240,
            pixel_format="yuv420p",
        )

    monkeypatch.setattr(DataInterface, "_validate_video", valid_video)
    monkeypatch.setattr(DataInterface, "_validate_normalized_video", valid_video)


def test_failed_later_item_rolls_back_whole_batch(gallery, monkeypatch):
    data_interface, owner, project, post, post_dir = gallery

    def reject_video(self, path, display_name):
        raise APIError(f"Could not process {display_name} as a video")

    monkeypatch.setattr(DataInterface, "_validate_video", reject_video)

    with pytest.raises(APIError, match="broken.mov"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [
                _image_upload("first.png", (230, 40, 40)),
                _video_upload("broken.mov", b"not a video"),
            ],
        )

    assert _gallery_filenames(data_interface, project, post) == []
    assert list(post_dir.iterdir()) == []


def test_concurrent_same_basename_uploads_publish_unique_files(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    both_uploads_prepared = Barrier(2, timeout=5)
    original_normalize_image = DataInterface._normalize_gallery_image

    def synchronize_preparation(self, source, filename):
        result = original_normalize_image(self, source, filename)
        both_uploads_prepared.wait()
        return result

    monkeypatch.setattr(
        DataInterface,
        "_normalize_gallery_image",
        synchronize_preparation,
    )

    uploads = [
        _image_upload("photo.png", (240, 30, 30)),
        _image_upload("photo.png", (30, 30, 240)),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                DataInterface().add_gallery_media,
                owner,
                project,
                post,
                [upload],
            )
            for upload in uploads
        ]
        assert [future.result(timeout=5) for future in futures] == [1, 1]

    filenames = _gallery_filenames(data_interface, project, post)
    assert len(filenames) == 2
    assert len(set(filenames)) == 2
    assert set(filenames) == {"photo.webp", "photo-2.webp"}
    assert {path.name for path in post_dir.iterdir()} == set(filenames)


def test_post_deleted_during_preparation_cannot_be_recreated(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    preparation_started = Event()
    allow_preparation_to_finish = Event()
    original_normalize_image = DataInterface._normalize_gallery_image

    def pause_preparation(self, source, filename):
        preparation_started.set()
        if not allow_preparation_to_finish.wait(timeout=5):
            raise TimeoutError("test did not release gallery preparation")
        return original_normalize_image(self, source, filename)

    monkeypatch.setattr(
        DataInterface,
        "_normalize_gallery_image",
        pause_preparation,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        upload = executor.submit(
            data_interface.add_gallery_media,
            owner,
            project,
            post,
            [_image_upload("late.png", (80, 160, 220))],
        )
        assert preparation_started.wait(timeout=5)
        try:
            data_interface.delete_post(project, post)
        finally:
            allow_preparation_to_finish.set()

        with pytest.raises(APIError, match="gallery post"):
            upload.result(timeout=5)

    assert not post_dir.exists()
    assert data_interface._post_entry(project, post) is None


def test_quota_allows_large_source_when_finalized_video_fits(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    finalized_bytes = b"small normalized mp4"
    source_bytes = b"x" * 4096
    monkeypatch.setattr(
        ConfigManager().loft,
        "non_admin_quota_bytes",
        len(finalized_bytes) + 1,
    )
    _mock_video_validation(monkeypatch)

    def write_normalized_video(self, src, dst, display_name, info):
        self.atomic_write(dst, data=finalized_bytes, mode="wb")

    monkeypatch.setattr(
        DataInterface,
        "_transcode_video",
        write_normalized_video,
    )

    assert data_interface.add_gallery_media(
        owner,
        project,
        post,
        [_video_upload("compressible.mov", source_bytes)],
    ) == 1
    assert (post_dir / "compressible.mp4").read_bytes() == finalized_bytes
    assert _gallery_filenames(data_interface, project, post) == [
        "compressible.mp4"
    ]


def test_quota_rejects_oversized_finalized_video_without_orphan(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    source_bytes = b"small source"
    finalized_bytes = b"x" * 512
    monkeypatch.setattr(
        ConfigManager().loft,
        "non_admin_quota_bytes",
        len(source_bytes) + 1,
    )
    _mock_video_validation(monkeypatch)

    def write_normalized_video(self, src, dst, display_name, info):
        self.atomic_write(dst, data=finalized_bytes, mode="wb")

    monkeypatch.setattr(
        DataInterface,
        "_transcode_video",
        write_normalized_video,
    )

    with pytest.raises(APIError, match="quota"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [_video_upload("expands.mov", source_bytes)],
        )

    assert _gallery_filenames(data_interface, project, post) == []
    assert list(post_dir.iterdir()) == []


def test_next_upload_removes_stale_crashed_worker_staging(
    gallery,
    monkeypatch,
    tmp_path,
):
    data_interface, owner, project, post, _ = gallery
    staging_root = tmp_path / "gallery-staging"
    monkeypatch.setattr(
        ConfigManager().loft,
        "gallery_staging_root",
        staging_root,
        raising=False,
    )
    stale = staging_root / "abandoned-upload"
    stale.mkdir(parents=True)
    (stale / "large.source").write_bytes(b"orphaned")
    old_timestamp = time.time() - 120
    os.utime(stale, (old_timestamp, old_timestamp))
    monkeypatch.setattr(
        ConfigManager().loft,
        "gallery_staging_max_age_s",
        60,
        raising=False,
    )

    assert data_interface.add_gallery_media(
        owner,
        project,
        post,
        [_image_upload("new.png", (40, 120, 200))],
    ) == 1

    assert not stale.exists()


def test_batch_rejects_second_video_before_second_transcode(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    _mock_video_validation(monkeypatch)
    transcodes = 0

    def write_normalized_video(self, src, dst, display_name, info):
        nonlocal transcodes
        transcodes += 1
        self.atomic_write(dst, data=b"normalized mp4", mode="wb")

    monkeypatch.setattr(
        DataInterface,
        "_transcode_video",
        write_normalized_video,
    )
    monkeypatch.setattr(
        ConfigManager().loft,
        "gallery_max_videos_per_upload",
        1,
        raising=False,
    )

    with pytest.raises(APIError, match="Too many videos"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [
                _video_upload("one.mov", b"video one"),
                _video_upload("two.mov", b"video two"),
            ],
        )

    assert transcodes == 0
    assert _gallery_filenames(data_interface, project, post) == []
    assert list(post_dir.iterdir()) == []


def test_chmod_failure_rolls_back_moved_file_and_metadata(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    original_chmod = Path.chmod

    def fail_published_chmod(path, mode):
        if path.parent == post_dir and path.suffix == ".webp":
            raise OSError("simulated chmod failure")
        return original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", fail_published_chmod)

    with pytest.raises(APIError, match="finalize"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [_image_upload("photo.png", (50, 100, 150))],
        )

    assert _gallery_filenames(data_interface, project, post) == []
    assert list(post_dir.iterdir()) == []


def test_next_upload_recovers_unreferenced_crashed_publish(
    gallery,
):
    data_interface, owner, project, post, post_dir = gallery
    orphan = post_dir / "orphan.webp"
    orphan.write_bytes(b"normalized but never committed")
    journal = post_dir / ".gallery-publish-test.json"
    journal.write_text(
        '{"filenames": ["orphan.webp"]}',
        encoding="utf-8",
    )

    assert data_interface.add_gallery_media(
        owner,
        project,
        post,
        [_image_upload("new.png", (30, 120, 210))],
    ) == 1

    assert not orphan.exists()
    assert not journal.exists()
    assert _gallery_filenames(data_interface, project, post) == ["new.webp"]


def test_recovery_keeps_publish_committed_before_worker_crash(
    gallery,
):
    data_interface, owner, project, post, post_dir = gallery
    committed = post_dir / "committed.webp"
    committed.write_bytes(b"committed normalized image")
    with data_interface.edit_meta() as store:
        meta = data_interface._post_in_store(store, project, post)
        meta.template_data.items.append(
            GalleryItem(type="image", filename="committed.webp")
        )
    journal = post_dir / ".gallery-publish-test.json"
    journal.write_text(
        '{"filenames": ["committed.webp"]}',
        encoding="utf-8",
    )

    assert data_interface.add_gallery_media(
        owner,
        project,
        post,
        [_image_upload("new.png", (30, 120, 210))],
    ) == 1

    assert committed.exists()
    assert not journal.exists()
    assert _gallery_filenames(data_interface, project, post) == [
        "committed.webp",
        "new.webp",
    ]


def test_admin_upload_is_charged_to_gallery_owner_quota(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    admin = User("admin", "x", "admin", is_admin=True)
    monkeypatch.setattr(
        BaseDataInterface,
        "load_users",
        lambda self: {owner.id: owner},
    )
    monkeypatch.setattr(
        ConfigManager().loft,
        "non_admin_quota_bytes",
        1,
    )

    with pytest.raises(APIError, match="quota"):
        data_interface.add_gallery_media(
            admin,
            project,
            post,
            [_image_upload("admin.png", (80, 130, 180))],
        )

    assert _gallery_filenames(data_interface, project, post) == []
    assert list(post_dir.iterdir()) == []


def test_admin_upload_uses_elevated_gallery_owner_quota(
    gallery,
    monkeypatch,
):
    data_interface, _, _, _, _ = gallery
    owner = User("elevated", "x", "owner", is_elevated=True)
    admin = User("admin", "x", "admin", is_admin=True)
    project, post = data_interface.create_gallery_post(
        owner,
        "Elevated uploads",
        "Gallery",
        "",
    )
    monkeypatch.setattr(
        BaseDataInterface,
        "load_users",
        lambda self: {owner.id: owner},
    )
    monkeypatch.setattr(
        ConfigManager().loft,
        "non_admin_quota_bytes",
        1,
    )

    assert data_interface.add_gallery_media(
        admin,
        project,
        post,
        [_image_upload("admin.png", (80, 130, 180))],
    ) == 1
