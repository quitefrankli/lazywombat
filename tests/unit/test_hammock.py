"""Unit tests for Hammock data interface."""

import json
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

import pytest
import web_app.helpers as helpers
from PIL import Image
from werkzeug.datastructures import FileStorage

from web_app.errors import APIError
from web_app.hammock import hammock_api
from web_app.hammock.data_interface import DataInterface, VideoInfo
from web_app.users import User
from web_app.config import ConfigManager


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    d = tmp_path / "hammock" / "projects"
    d.mkdir(parents=True)

    def patched_init(self):
        from markdown_it import MarkdownIt
        self.projects_dir = d
        self._content_dir = d.parent
        self._md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})

    monkeypatch.setattr(DataInterface, "__init__", patched_init)
    return d


def _make_post(projects_dir: Path, project: str, post: str, date: str | None = None):
    post_dir = projects_dir / project / post
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "index.html").write_text("<h1>test</h1>")
    meta_path = projects_dir.parent / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"projects": {}}
    meta.setdefault("projects", {}).setdefault(project, {"posts": {}})["posts"][post] = {
        "type": "raw",
        "title": post,
        "date": date or "",
    }
    meta_path.write_text(json.dumps(meta))


def _post_meta(projects_dir: Path, project: str, post: str) -> dict:
    return json.loads((projects_dir.parent / "meta.json").read_text())["projects"][project]["posts"][post]


def _png_file_storage(name: str, size: tuple[int, int] = (40, 40)) -> FileStorage:
    """Return an in-memory PNG FileStorage so we don't need real fixture images."""
    buf = BytesIO()
    Image.new("RGB", size, color=(180, 200, 160)).save(buf, format="PNG")
    buf.seek(0)
    return FileStorage(stream=buf, filename=name, content_type="image/png")


def _mp4_file_storage(tmp_path: Path, name: str = "clip.mp4", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=15",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/mp4")


def _mov_file_storage(tmp_path: Path, name: str = "iphone.MOV", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=15",
            "-t", str(duration),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/quicktime")


def _av_mov_file_storage(tmp_path: Path, name: str = "sound.MOV", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=15",
            "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=44100",
            "-t", str(duration),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/quicktime")


def _portrait_mov_file_storage(tmp_path: Path, name: str = "portrait.MOV", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=540x960:rate=15",
            "-t", str(duration),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/quicktime")


class TestGetPostsByProjectSorting:
    def test_sorts_by_meta_date_descending(self, projects_dir):
        _make_post(projects_dir, "blog", "alpha", date="2024-01-01")
        _make_post(projects_dir, "blog", "beta", date="2024-06-01")
        _make_post(projects_dir, "blog", "gamma", date="2024-03-01")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["beta", "gamma", "alpha"]

    def test_falls_back_to_name_sort_when_no_meta(self, projects_dir):
        _make_post(projects_dir, "blog", "2024-03-zz")
        _make_post(projects_dir, "blog", "2024-01-aa")
        _make_post(projects_dir, "blog", "2024-06-bb")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["2024-06-bb", "2024-03-zz", "2024-01-aa"]

    def test_date_meta_takes_priority_over_name(self, projects_dir):
        _make_post(projects_dir, "blog", "zzz-newest-name", date="2023-01-01")
        _make_post(projects_dir, "blog", "aaa-oldest-name", date="2025-01-01")

        result = DataInterface().get_posts_by_project()
        assert result[0].posts == ["aaa-oldest-name", "zzz-newest-name"]

    def test_unlisted_posts_are_sidebar_hidden_except_for_elevated_users(self, projects_dir):
        _make_post(projects_dir, "ideas", "public")
        _make_post(projects_dir, "ideas", "private")
        _make_post(projects_dir, "private-project", "only-post")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["ideas"]["posts"]["private"]["visibility"] = "unlisted"
        meta["projects"]["private-project"]["posts"]["only-post"]["visibility"] = "unlisted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        normal = User("alice", "x", "fa", is_admin=False)
        elevated = User("eve", "x", "fe", is_elevated=True)
        admin = User("root", "x", "fr", is_admin=True)

        assert DataInterface().get_posts_by_project()[0].posts == ["public"]
        assert DataInterface().get_posts_by_project(normal)[0].posts == ["public"]
        assert [p.name for p in DataInterface().get_posts_by_project()] == ["ideas"]
        assert DataInterface().get_posts_by_project(elevated)[0].posts == ["public", "private"]
        assert DataInterface().get_posts_by_project(admin)[0].posts == ["public", "private"]
        assert "<h1>test</h1>" in DataInterface().get_post_content("ideas", "private")

    def test_restricted_visibility_requires_elevated_access(self, projects_dir):
        _make_post(projects_dir, "ideas", "public")
        _make_post(projects_dir, "ideas", "private")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["ideas"]["posts"]["private"]["visibility"] = "restricted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        normal = User("alice", "x", "fa", is_admin=False)
        elevated = User("eve", "x", "fe", is_elevated=True)

        assert DataInterface().get_posts_by_project(normal)[0].posts == ["public"]
        assert DataInterface().get_posts_by_project(elevated)[0].posts == ["public", "private"]
        assert DataInterface().user_can_view(normal, "ideas", "private") is False
        assert DataInterface().user_can_view(elevated, "ideas", "private") is True
        assert DataInterface().user_can_view(None, "ideas", "private") is False


class TestMarkdownLifecycleAndAuthz:
    def test_markdown_create_render_edit_and_ownership(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        bob = User("bob", "x", "fb", is_admin=False)
        admin = User("root", "x", "fr", is_admin=True)

        proj, post = di.create_markdown_post(alice, "My Blog", "Hello World", "Hi **bob**.")

        # Slug + persistence + meta
        assert proj == "my-blog"
        assert post == "hello-world"
        post_dir = projects_dir / proj / post
        meta = _post_meta(projects_dir, proj, post)
        assert meta["owner"] == "alice"
        assert meta["type"] == "markdown"
        assert (post_dir / "source.md").read_text() == "Hi **bob**."
        assert not (post_dir / "meta.json").exists()
        assert not (post_dir / "index.html").exists()

        # Rendered body contains the markdown→HTML conversion and the byline
        content = di.get_post_content(proj, post)
        assert "<strong>bob</strong>" in content
        assert 'class="hammock-post-author">alice' in content

        # Authorization: owner + admin allowed; stranger denied; legacy (no owner) is admin-only
        assert di.user_can_edit(alice, proj, post) is True
        assert di.user_can_edit(admin, proj, post) is True
        assert di.user_can_edit(bob, proj, post) is False

        _make_post(projects_dir, "legacy", "old-post")
        assert di.user_can_edit(admin, "legacy", "old-post") is True
        assert di.user_can_edit(alice, "legacy", "old-post") is False

        di.update_markdown_post(proj, post, "New Title", "Updated.")
        assert (post_dir / "source.md").read_text() == "Updated."
        assert _post_meta(projects_dir, proj, post)["title"] == "New Title"
        assert not (post_dir / "index.html").exists()
        assert "Updated." in di.get_post_content(proj, post)

    def test_duplicate_post_title_raises(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        di.create_markdown_post(alice, "blog", "Same Title", "a")
        with pytest.raises(APIError, match="already exists"):
            di.create_markdown_post(alice, "blog", "Same Title", "b")


class TestGalleryUploadAndDelete:
    def test_video_max_height_defaults_to_720p(self):
        assert ConfigManager().hammock.gallery_video_max_height_px == 720

    def test_gallery_video_resets_shared_post_media_margin(self):
        css = (
            Path(__file__).parents[2]
            / "web_app"
            / "hammock"
            / "static"
            / "hammock.css"
        ).read_text()

        gallery_video_rule = css.split(".hammock-gallery-video video {", 1)[1].split(
            "}", 1
        )[0]
        assert "margin: 0;" in gallery_video_rule

    def test_expanded_gallery_video_keeps_controls_inside_viewport(self):
        css = (
            Path(__file__).parents[2]
            / "web_app"
            / "hammock"
            / "static"
            / "hammock.css"
        ).read_text()

        expanded_rule = css.split(".hammock-gallery-video.is-expanded {", 1)[1].split(
            "}", 1
        )[0]
        assert "box-sizing: border-box;" in expanded_rule
        assert ".hammock-gallery-video.is-expanded .hammock-video-sound {" in css
        assert "env(safe-area-inset-bottom)" in css

    def test_add_then_delete_image_generates_thumb_and_updates_state(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)

        proj, post = di.create_gallery_post(alice, "Album", "Trip", "desc")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_images(alice, proj, post, [_png_file_storage("photo.png")])
        assert n == 1
        assert not (post_dir / "photo.png").exists()
        assert (post_dir / "photo.webp").exists()
        assert not (post_dir / "thumbs").exists()

        assert not (post_dir / "gallery.json").exists()
        assert not (post_dir / "index.html").exists()
        gallery = _post_meta(projects_dir, proj, post)
        assert "images" not in gallery
        assert gallery["template-data"]["items"] == [
            {"type": "image", "filename": "photo.webp"}
        ]

        rendered = di.get_post_content(proj, post)
        assert 'data-full="photo.webp"' in rendered
        assert 'data-gallery-src="photo.webp"' in rendered
        assert 'src="data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="' in rendered
        assert "alice" in rendered

        di.delete_gallery_image(proj, post, "photo.webp")
        assert not (post_dir / "photo.png").exists()
        assert not (post_dir / "photo.webp").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    @pytest.mark.ffmpeg
    def test_add_then_delete_video_transcodes_and_updates_state(self, projects_dir, tmp_path):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)

        proj, post = di.create_gallery_post(alice, "Album", "Clips", "desc")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_media(alice, proj, post, [_mp4_file_storage(tmp_path)])
        assert n == 1
        assert (post_dir / "clip.mp4").exists()
        assert not (post_dir / ".upload-clip.mp4").exists()
        assert not (post_dir / "thumbs").exists()

        gallery = _post_meta(projects_dir, proj, post)
        assert "images" not in gallery
        assert gallery["template-data"]["items"] == [
            {"type": "video", "filename": "clip.mp4", "has_audio": False}
        ]

        rendered = di.get_post_content(proj, post)
        assert (
            '<video data-hammock-video data-video-expand '
            'autoplay loop muted playsinline preload="metadata">'
        ) in rendered
        assert "controls" not in rendered
        assert "hammock-video-sound" not in rendered
        assert '<source src="clip.mp4" type="video/mp4">' in rendered

        di.delete_gallery_media(proj, post, "clip.mp4")
        assert not (post_dir / "clip.mp4").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    @pytest.mark.ffmpeg
    def test_phone_video_upload_is_normalized_to_mp4(self, projects_dir, tmp_path):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Phone Clip", "")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_media(alice, proj, post, [_mov_file_storage(tmp_path)])
        assert n == 1
        assert (post_dir / "iphone.mp4").exists()
        assert not (post_dir / "iphone.MOV").exists()
        assert not (post_dir / "thumbs").exists()

        gallery = _post_meta(projects_dir, proj, post)
        assert gallery["template-data"]["items"] == [
            {"type": "video", "filename": "iphone.mp4", "has_audio": False}
        ]

    @pytest.mark.ffmpeg
    def test_video_with_audio_preserves_one_audio_stream_and_renders_sound_control(
        self, projects_dir, tmp_path
    ):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Sound Clip", "")
        post_dir = projects_dir / proj / post

        assert di.add_gallery_media(alice, proj, post, [_av_mov_file_storage(tmp_path)]) == 1

        output = post_dir / "sound.mp4"
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                "-of", "json", str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(probe.stdout)["streams"]
        assert [stream["codec_type"] for stream in streams] == ["video", "audio"]
        assert streams[1]["codec_name"] == "aac"
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == [
            {"type": "video", "filename": "sound.mp4", "has_audio": True}
        ]
        rendered = di.get_post_content(proj, post)
        assert 'class="hammock-video-sound"' in rendered
        assert 'aria-label="Unmute video"' in rendered

    def test_probe_skips_mebx_like_unknown_audio_and_selects_real_audio(
        self, projects_dir, tmp_path, monkeypatch
    ):
        payload = {
            "format": {"duration": "0.5"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "hevc"},
                {"index": 1, "codec_type": "audio", "codec_name": "none", "codec_tag_string": "mebx"},
                {"index": 2, "codec_type": "data", "codec_name": "bin_data"},
                {"index": 3, "codec_type": "audio", "codec_name": "aac"},
                {"index": 4, "codec_type": "audio", "codec_name": "aac"},
            ],
        }
        monkeypatch.setattr(
            DataInterface,
            "_run_media_command",
            staticmethod(lambda *args: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), "")),
        )

        info = DataInterface()._probe_video_info(tmp_path / "phone.mov", "phone.mov")

        assert info.video_index == 0
        assert info.audio_index == 3
        assert info.duration == 0.5

    @pytest.mark.parametrize(("audio_index", "expects_audio"), [(None, False), (3, True)])
    def test_transcode_maps_only_selected_media_streams(
        self, projects_dir, tmp_path, monkeypatch, audio_index, expects_audio
    ):
        commands = []
        monkeypatch.setattr(
            DataInterface,
            "_run_media_command",
            staticmethod(
                lambda cmd, timeout_s, error_message: (
                    commands.append(cmd)
                    or subprocess.CompletedProcess(cmd, 0, "", "")
                )
            ),
        )

        DataInterface()._transcode_video(
            tmp_path / "source.mov",
            tmp_path / "output.mp4",
            "source.mov",
            VideoInfo(duration=0.5, video_index=0, audio_index=audio_index),
        )

        cmd = commands[0]
        mapped = [cmd[index + 1] for index, value in enumerate(cmd[:-1]) if value == "-map"]
        assert mapped == (["0:0", "0:3"] if expects_audio else ["0:0"])
        assert ("-an" in cmd) is not expects_audio
        assert "0:a?" not in cmd
        assert "-dn" in cmd
        assert "-sn" in cmd

    @pytest.mark.ffmpeg
    def test_audio_only_upload_is_rejected_and_cleaned_up(self, projects_dir, tmp_path):
        audio_path = tmp_path / "voice.m4a"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "0.2", "-c:a", "aac", str(audio_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Audio Only", "")
        upload = FileStorage(
            stream=BytesIO(audio_path.read_bytes()),
            filename="fake-video.mp4",
            content_type="video/mp4",
        )

        with pytest.raises(APIError, match="video stream"):
            di.add_gallery_media(alice, proj, post, [upload])

        assert list((projects_dir / proj / post).iterdir()) == []
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    def test_gallery_reorder_validates_exact_filenames(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Order", "")
        di.add_gallery_images(
            alice, proj, post,
            [_png_file_storage("one.png"), _png_file_storage("two.png"), _png_file_storage("three.png")],
        )

        di.update_gallery_meta(
            proj, post, "Order", "", ["three.webp", "one.webp", "two.webp"]
        )
        assert [
            item["filename"]
            for item in _post_meta(projects_dir, proj, post)["template-data"]["items"]
        ] == ["three.webp", "one.webp", "two.webp"]

        with pytest.raises(APIError, match="Media order"):
            di.update_gallery_meta(proj, post, "Order", "", ["one.webp", "missing.webp"])
        assert [
            item["filename"]
            for item in _post_meta(projects_dir, proj, post)["template-data"]["items"]
        ] == ["three.webp", "one.webp", "two.webp"]

    @pytest.mark.ffmpeg
    def test_portrait_video_transcode_outputs_even_dimensions(self, projects_dir, tmp_path):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Portrait Clip", "")
        post_dir = projects_dir / proj / post

        n = di.add_gallery_media(alice, proj, post, [_portrait_mov_file_storage(tmp_path)])

        assert n == 1
        output = post_dir / "portrait.mp4"
        assert output.exists()
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        width, height = [int(value) for value in result.stdout.strip().split(",")]
        assert width % 2 == 0
        assert height % 2 == 0
        assert height == 720

    def test_video_with_missing_duration_metadata_can_continue(self, projects_dir, tmp_path, monkeypatch):
        di = DataInterface()

        def fake_run(cmd, timeout_s, error_message):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"format":{},"streams":[{"index":0,"codec_type":"video","codec_name":"h264"}]}',
                stderr="",
            )

        monkeypatch.setattr(DataInterface, "_run_media_command", staticmethod(fake_run))
        di._validate_video(tmp_path / "upload.mp4", "phone-video.mp4")

    def test_video_processing_error_uses_original_filename(self, projects_dir, tmp_path, monkeypatch):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Broken Clip", "")

        def fake_run(cmd, timeout_s, error_message):
            if cmd[0] == "ffprobe":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=(
                        '{"format":{"duration":"0.5"},"streams":'
                        '[{"index":0,"codec_type":"video","codec_name":"h264","duration":"0.5"}]}'
                    ),
                    stderr="",
                )
            raise APIError(error_message)

        monkeypatch.setattr(DataInterface, "_run_media_command", staticmethod(fake_run))
        bad_video = FileStorage(
            stream=BytesIO(b"not a video"),
            filename="broken.MOV",
            content_type="video/quicktime",
        )

        with pytest.raises(APIError) as exc:
            di.add_gallery_media(alice, proj, post, [bad_video])

        assert "broken.MOV" in str(exc.value)
        assert "tmp" not in str(exc.value)

    @pytest.mark.ffmpeg
    def test_video_over_duration_limit_is_rejected(self, projects_dir, tmp_path, monkeypatch):
        from web_app.config import ConfigManager
        monkeypatch.setattr(ConfigManager().hammock, "gallery_video_max_duration_s", 0)

        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Long Clip", "")
        post_dir = projects_dir / proj / post

        with pytest.raises(APIError, match="too long"):
            di.add_gallery_media(alice, proj, post, [_mp4_file_storage(tmp_path)])

        assert not (post_dir / "clip.mp4").exists()
        assert not (post_dir / ".upload-clip.mp4").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    def test_quota_blocks_uploads_over_limit(self, projects_dir, monkeypatch):
        from web_app.config import ConfigManager
        monkeypatch.setattr(ConfigManager().hammock, "non_admin_quota_bytes", 1)

        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "tiny", "Holiday", "")

        # Quota is checked against the persisted WebP derivative, not an original.
        with pytest.raises(APIError, match="quota"):
            di.add_gallery_images(alice, proj, post, [_png_file_storage("big.png", size=(200, 200))])

    def test_non_image_bytes_with_image_extension_are_rejected(self, projects_dir):
        """A file named .png that isn't actually a valid image must NOT be saved or listed."""
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "trip", "Album", "")
        post_dir = projects_dir / proj / post

        evil = FileStorage(
            stream=BytesIO(b"<script>alert(1)</script>"),
            filename="evil.png",
            content_type="image/png",
        )
        with pytest.raises(APIError, match="image"):
            di.add_gallery_images(alice, proj, post, [evil])

        # Critical: the original must not be left on disk and the gallery must
        # not list it.
        assert not (post_dir / "evil.png").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == []

    def test_oversized_title_rejected(self, projects_dir):
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        with pytest.raises(APIError, match="too long"):
            di.create_markdown_post(alice, "blog", "x" * 5000, "body")


class TestGalleryEditRoute:
    def test_new_gallery_post_upload_uses_ajax_progress_contract(self, client, projects_dir, monkeypatch):
        if "hammock" not in client.application.blueprints:
            client.application.register_blueprint(hammock_api)
        alice = User("alice", "x", "fa", is_admin=False)
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )

        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        new_response = client.get("/hammock/new")
        assert b"data-gallery-upload-form" in new_response.data
        assert b"data-gallery-upload-progress" in new_response.data
        assert b"looping 720p MP4" in new_response.data
        assert b"Topics" in new_response.data
        assert b"Pick an existing topic" in new_response.data
        assert b"new topic name" in new_response.data
        assert b"Projects" not in new_response.data

        response = client.post(
            "/hammock/new",
            data={
                "project_new": "Album",
                "title": "Trip",
                "template": "gallery",
                "description": "desc",
                "files": (BytesIO(_png_file_storage("photo.png").read()), "photo.png"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        assert response.get_json()["redirect_url"] == "/hammock/album/trip/"
        post_dir = projects_dir / "album" / "trip"
        assert not (post_dir / "photo.png").exists()
        assert (post_dir / "photo.webp").exists()
        assert not (post_dir / "thumbs").exists()

    def test_update_post_uploads_selected_images(self, client, projects_dir, monkeypatch):
        if "hammock" not in client.application.blueprints:
            client.application.register_blueprint(hammock_api)
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Trip", "desc")
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )

        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        edit_response = client.get(f"/hammock/{proj}/{post}/edit")
        assert b">Upload<" not in edit_response.data

        response = client.post(
            f"/hammock/{proj}/{post}/edit",
            data={
                "title": "Updated Trip",
                "description": "new desc",
                "files": (BytesIO(_png_file_storage("photo.png").read()), "photo.png"),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        assert response.get_json()["redirect_url"] == f"/hammock/{proj}/{post}/"
        post_dir = projects_dir / proj / post
        assert not (post_dir / "photo.png").exists()
        assert (post_dir / "photo.webp").exists()
        assert _post_meta(projects_dir, proj, post)["template-data"]["items"] == [
            {"type": "image", "filename": "photo.webp"}
        ]

    def test_edit_gallery_exposes_audio_controls_and_saves_media_order(
        self, client, projects_dir, monkeypatch
    ):
        if "hammock" not in client.application.blueprints:
            client.application.register_blueprint(hammock_api)
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Reorder", "")
        di.add_gallery_images(
            alice, proj, post, [_png_file_storage("one.png"), _png_file_storage("two.png")]
        )
        with di.edit_meta() as store:
            td = store.projects[proj].posts[post].template_data
            td.items.append(type(td.items[0])(type="video", filename="sound.mp4", has_audio=True))
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )
        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        edit_response = client.get(f"/hammock/{proj}/{post}/edit")
        assert b"data-reorder-grid" in edit_response.data
        assert b"data-reorder-handle" not in edit_response.data
        assert b"data-reorder-previous" in edit_response.data
        assert b"data-reorder-next" in edit_response.data
        assert b"data-video-sound" in edit_response.data

        response = client.post(
            f"/hammock/{proj}/{post}/edit",
            data={
                "title": "Reorder",
                "description": "",
                "media_order": '["sound.mp4","two.webp","one.webp"]',
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert response.status_code == 200
        assert [
            item["filename"]
            for item in _post_meta(projects_dir, proj, post)["template-data"]["items"]
        ] == ["sound.mp4", "two.webp", "one.webp"]

    def test_edit_gallery_rejects_stale_media_order(self, client, projects_dir, monkeypatch):
        if "hammock" not in client.application.blueprints:
            client.application.register_blueprint(hammock_api)
        di = DataInterface()
        alice = User("alice", "x", "fa", is_admin=False)
        proj, post = di.create_gallery_post(alice, "Album", "Reorder", "")
        di.add_gallery_images(alice, proj, post, [_png_file_storage("one.png")])
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: alice if username == alice.id else None,
        )
        with client.session_transaction() as sess:
            sess["_user_id"] = alice.id
            sess["_fresh"] = True

        response = client.post(
            f"/hammock/{proj}/{post}/edit",
            data={"title": "Reorder", "description": "", "media_order": '["missing.webp"]'},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert response.status_code == 400
        assert "Media order" in response.get_json()["error"]
        assert _post_meta(projects_dir, proj, post)["title"] == "Reorder"


class TestRestrictedPostRoute:
    def test_restricted_post_direct_url_requires_elevated_user(self, client, projects_dir, monkeypatch):
        if "hammock" not in client.application.blueprints:
            client.application.register_blueprint(hammock_api)
        _make_post(projects_dir, "ideas", "private")
        meta = json.loads((projects_dir.parent / "meta.json").read_text())
        meta["projects"]["ideas"]["posts"]["private"]["visibility"] = "restricted"
        (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

        assert client.get("/hammock/ideas/private/").status_code == 404

        elevated = User("eve", "x", "fe", is_elevated=True)
        monkeypatch.setattr(
            helpers.login_manager,
            "_user_callback",
            lambda username: elevated if username == elevated.id else None,
        )
        with client.session_transaction() as sess:
            sess["_user_id"] = elevated.id
            sess["_fresh"] = True

        response = client.get("/hammock/ideas/private/")

        assert response.status_code == 200
        assert b"<h1>test</h1>" in response.data


class TestHammockBackup:
    def test_backup_copies_meta_and_nested_posts(self, projects_dir, tmp_path):
        """backup_data copies the whole content dir (meta.json + per-post files)
        into a namespaced 'hammock/' subdir of the shared backup dir."""
        di = DataInterface()
        _make_post(projects_dir, "blog", "hello", date="2026-01-01")

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        di.backup_data(backup_dir)

        assert (backup_dir / "hammock" / "meta.json").exists()
        assert (backup_dir / "hammock" / "projects" / "blog" / "hello" / "index.html").exists()
        # The copied meta still describes the post.
        copied_meta = json.loads((backup_dir / "hammock" / "meta.json").read_text())
        assert "hello" in copied_meta["projects"]["blog"]["posts"]

    def test_backup_is_noop_when_no_content_dir(self, projects_dir, tmp_path):
        """A missing content dir must not raise (fresh install, nothing posted)."""
        di = DataInterface()
        shutil.rmtree(di._content_dir)
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        di.backup_data(backup_dir)  # must not raise
        assert not (backup_dir / "hammock").exists()
