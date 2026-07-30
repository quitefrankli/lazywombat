"""Cross-device media compatibility tests for Hammock galleries."""

import json
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from web_app.config import ConfigManager
from web_app.errors import APIError
from web_app.hammock.data_interface import DataInterface, VideoInfo
from web_app.users import User


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    projects = tmp_path / "hammock" / "projects"
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
        "Device uploads",
        "Compatibility",
        "",
    )
    return data_interface, owner, project, post, projects_dir / project / post


def _image_upload(
    image_format: str,
    filename: str,
    *,
    size: tuple[int, int] = (96, 64),
    exif: Image.Exif | None = None,
) -> FileStorage:
    buffer = BytesIO()
    image = Image.new("RGB", size, color=(80, 140, 210))
    save_kwargs = {"exif": exif} if exif is not None else {}
    image.save(buffer, format=image_format, **save_kwargs)
    buffer.seek(0)
    return FileStorage(
        stream=buffer,
        filename=filename,
        content_type="application/octet-stream",
    )


@pytest.mark.parametrize(
    ("image_format", "filename"),
    [
        ("JPEG", "iphone.JPG"),
        ("PNG", "android.png"),
        ("WEBP", "browser.webp"),
        ("GIF", "shared.gif"),
        ("BMP", "desktop.bmp"),
        ("TIFF", "scanner.tiff"),
        ("AVIF", "android.avif"),
        ("HEIF", "iphone.heic"),
    ],
)
def test_common_device_image_formats_normalize_to_webp(
    gallery,
    image_format,
    filename,
):
    data_interface, owner, project, post, post_dir = gallery

    assert data_interface.add_gallery_media(
        owner,
        project,
        post,
        [_image_upload(image_format, filename)],
    ) == 1

    output = post_dir / f"{Path(filename).stem}.webp"
    with Image.open(output) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (96, 64)


def test_exif_oriented_phone_photo_is_upright_and_private_metadata_is_removed(
    gallery,
):
    data_interface, owner, project, post, post_dir = gallery
    exif = Image.Exif()
    exif[274] = 6  # Rotate 90 degrees clockwise for display.
    exif[34853] = {1: "N"}  # GPS metadata must not survive normalization.

    assert data_interface.add_gallery_media(
        owner,
        project,
        post,
        [_image_upload("JPEG", "portrait.jpg", size=(80, 40), exif=exif)],
    ) == 1

    with Image.open(post_dir / "portrait.webp") as normalized:
        assert normalized.size == (40, 80)
        assert not normalized.getexif()


def test_decoder_uses_image_bytes_instead_of_client_mime_or_exact_format(
    gallery,
):
    data_interface, owner, project, post, post_dir = gallery
    png_named_as_jpeg = _image_upload("PNG", "renamed.jpg")

    assert data_interface.add_gallery_media(
        owner,
        project,
        post,
        [png_named_as_jpeg],
    ) == 1

    with Image.open(post_dir / "renamed.webp") as normalized:
        assert normalized.format == "WEBP"


def test_image_encoded_byte_limit_is_enforced_before_decode(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    monkeypatch.setattr(
        ConfigManager().hammock,
        "gallery_image_max_upload_bytes",
        1,
        raising=False,
    )

    with pytest.raises(APIError, match="too large"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [_image_upload("PNG", "oversized.png")],
        )

    assert list(post_dir.iterdir()) == []


def test_media_count_limit_rejects_entire_batch(gallery, monkeypatch):
    data_interface, owner, project, post, post_dir = gallery
    monkeypatch.setattr(
        ConfigManager().hammock,
        "gallery_max_files_per_upload",
        1,
        raising=False,
    )

    with pytest.raises(APIError, match="Too many"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [
                _image_upload("PNG", "one.png"),
                _image_upload("PNG", "two.png"),
            ],
        )

    assert list(post_dir.iterdir()) == []


def test_image_batch_decoded_pixel_budget_rejects_entire_batch(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    monkeypatch.setattr(
        ConfigManager().hammock,
        "gallery_image_max_batch_pixels",
        1,
    )

    with pytest.raises(APIError, match="decoded pixel budget"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [_image_upload("PNG", "compressed.png")],
        )

    assert list(post_dir.iterdir()) == []


def test_normalized_video_allows_mux_duration_tolerance(
    projects_dir,
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "boundary.mp4"
    path.write_bytes(b"mp4")
    info = VideoInfo(
        duration=60.25,
        video_index=0,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        video_codec="h264",
        video_codec_tag="avc1",
        width=640,
        height=360,
        pixel_format="yuv420p",
        sample_aspect_ratio="1:1",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
    )
    monkeypatch.setattr(
        DataInterface,
        "_probe_video_info",
        lambda self, src, display_name: info,
    )
    monkeypatch.setattr(
        DataInterface,
        "_mp4_has_faststart",
        staticmethod(lambda src: True),
    )

    assert DataInterface()._validate_normalized_video(
        path,
        "boundary.mp4",
    ) == info


def test_normalized_video_enforces_output_byte_cap(
    projects_dir,
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "oversized.mp4"
    path.write_bytes(b"xx")
    monkeypatch.setattr(
        ConfigManager().hammock,
        "gallery_video_max_output_bytes",
        1,
    )

    with pytest.raises(APIError, match="output size limit"):
        DataInterface()._validate_normalized_video(path, "oversized.mp4")


def test_untagged_sd_video_gets_explicit_bt601_input_conversion(
    projects_dir,
    tmp_path,
    monkeypatch,
):
    commands = []
    monkeypatch.setattr(
        DataInterface,
        "_run_media_command",
        lambda self, command, timeout, message: commands.append(command),
    )
    info = VideoInfo(
        duration=1,
        video_index=0,
        format_name="mov",
        video_codec="h263",
        width=352,
        height=288,
        pixel_format="yuv420p",
    )

    DataInterface()._transcode_video(
        tmp_path / "source.3gp",
        tmp_path / "output.mp4",
        "source.3gp",
        info,
    )

    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert "iall=smpte170m" in video_filter
    assert "all=bt709" in video_filter


def test_video_encoded_byte_limit_is_enforced_before_probe(
    gallery,
    monkeypatch,
):
    data_interface, owner, project, post, post_dir = gallery
    monkeypatch.setattr(
        ConfigManager().hammock,
        "gallery_video_max_upload_bytes",
        1,
    )
    upload = FileStorage(
        stream=BytesIO(b"oversized"),
        filename="clip.mov",
        content_type="video/quicktime",
    )

    with pytest.raises(APIError, match="too large"):
        data_interface.add_gallery_media(owner, project, post, [upload])

    assert list(post_dir.iterdir()) == []


def test_probe_skips_mebx_like_unknown_audio_and_selects_real_audio(
    projects_dir,
    tmp_path,
    monkeypatch,
):
    payload = {
        "format": {"duration": "0.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "codec_tag_string": "mebx",
                "sample_rate": "48000",
                "channels": 2,
            },
            {"index": 2, "codec_type": "data", "codec_name": "bin_data"},
            {
                "index": 3,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
            {
                "index": 4,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
    }
    monkeypatch.setattr(
        DataInterface,
        "_run_media_command",
        staticmethod(
            lambda *args: subprocess.CompletedProcess(
                args[0],
                0,
                json.dumps(payload),
                "",
            )
        ),
    )

    info = DataInterface()._probe_video_info(tmp_path / "phone.mov", "phone.mov")

    assert info.video_index == 0
    assert info.audio_index == 3
    assert info.duration == 0.5


def test_probe_selects_default_primary_video_and_ignores_auxiliary_tracks(
    projects_dir,
    tmp_path,
    monkeypatch,
):
    payload = {
        "format": {
            "duration": "1.0",
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        },
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "width": 4000,
                "height": 3000,
                "disposition": {"attached_pic": 1},
            },
            {
                "index": 1,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "disposition": {"dependent": 1},
            },
        ],
    }
    monkeypatch.setattr(
        DataInterface,
        "_run_media_command",
        staticmethod(
            lambda *args: subprocess.CompletedProcess(
                args[0],
                0,
                json.dumps(payload),
                "",
            )
        ),
    )

    info = DataInterface()._probe_video_info(
        tmp_path / "cinematic.mov",
        "cinematic.mov",
    )

    assert info.video_index == 1
    assert (info.width, info.height) == (1920, 1080)
    assert info.other_stream_count == 0


def test_probe_ignores_unselected_metadata_duration(
    projects_dir,
    tmp_path,
    monkeypatch,
):
    payload = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "duration": "0.75",
            },
            {
                "index": 1,
                "codec_type": "data",
                "codec_name": "bin_data",
                "duration": "600",
            },
        ],
    }
    monkeypatch.setattr(
        DataInterface,
        "_run_media_command",
        staticmethod(
            lambda *args: subprocess.CompletedProcess(
                args[0],
                0,
                json.dumps(payload),
                "",
            )
        ),
    )

    info = DataInterface()._probe_video_info(
        tmp_path / "timed-metadata.mov",
        "timed-metadata.mov",
    )

    assert info.duration == 0.75


def test_normalized_output_validator_rejects_non_browser_safe_codec(
    projects_dir,
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "unsafe.mp4"
    path.write_bytes(b"unsafe")
    monkeypatch.setattr(
        DataInterface,
        "_probe_video_info",
        lambda *args: VideoInfo(
            duration=1,
            video_index=0,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="hevc",
            video_codec_tag="hvc1",
            width=1280,
            height=720,
            pixel_format="yuv420p10le",
        ),
    )

    with pytest.raises(APIError, match="browser-safe"):
        DataInterface()._validate_normalized_video(
            path,
            "unsafe.mp4",
        )


@pytest.mark.ffmpeg
def test_playlist_disguised_as_video_is_rejected_without_publishing(gallery):
    data_interface, owner, project, post, post_dir = gallery
    playlist = FileStorage(
        stream=BytesIO(
            b"#EXTM3U\n#EXTINF:5,\nhttps://example.invalid/private.ts\n"
        ),
        filename="vacation.mp4",
        content_type="video/mp4",
    )

    with pytest.raises(APIError, match="Unsupported or invalid media"):
        data_interface.add_gallery_media(
            owner,
            project,
            post,
            [playlist],
        )

    assert list(post_dir.iterdir()) == []


@pytest.mark.ffmpeg
def test_iphone_style_hevc_mov_normalizes_to_browser_safe_mp4(gallery, tmp_path):
    data_interface, owner, project, post, post_dir = gallery
    source = tmp_path / "iphone.MOV"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=15",
            "-t",
            "0.2",
            "-c:v",
            "libx265",
            "-x265-params",
            "pools=1:frame-threads=1:log-level=error",
            "-pix_fmt",
            "yuv420p10le",
            "-metadata",
            "title=private title",
            "-metadata",
            "location=-33.8688+151.2093/",
            source,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    upload = FileStorage(
        stream=BytesIO(source.read_bytes()),
        filename="renamed-without-media-extension.bin",
        content_type="application/octet-stream",
    )

    assert data_interface.add_gallery_media(owner, project, post, [upload]) == 1

    output = post_dir / "renamed-without-media-extension.mp4"
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt:format_tags",
            "-of",
            "json",
            output,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream == {"codec_name": "h264", "pix_fmt": "yuv420p"}
    output_tags = json.loads(probe.stdout).get("format", {}).get("tags", {})
    assert "title" not in output_tags
    assert "location" not in output_tags


@pytest.mark.ffmpeg
def test_phone_rotation_metadata_is_applied_to_output_pixels(gallery, tmp_path):
    data_interface, owner, project, post, post_dir = gallery
    base = tmp_path / "landscape.mp4"
    rotated = tmp_path / "rotated.MOV"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=15",
            "-t",
            "0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            base,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-display_rotation:v:0",
            "90",
            "-i",
            base,
            "-c",
            "copy",
            rotated,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    upload = FileStorage(
        stream=BytesIO(rotated.read_bytes()),
        filename="rotated.MOV",
        content_type="video/quicktime",
    )

    assert data_interface.add_gallery_media(owner, project, post, [upload]) == 1

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:stream_side_data=rotation",
            "-of",
            "json",
            post_dir / "rotated.mp4",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output_stream = json.loads(probe.stdout)["streams"][0]
    assert (output_stream["width"], output_stream["height"]) == (90, 160)
    assert not output_stream.get("side_data_list")


@pytest.mark.ffmpeg
def test_phone_hlg_hdr_video_is_tone_mapped_to_bt709(gallery, tmp_path):
    data_interface, owner, project, post, post_dir = gallery
    source = tmp_path / "hdr-phone.MOV"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=15",
            "-t",
            "0.2",
            "-vf",
            "format=yuv420p10le",
            "-c:v",
            "libx265",
            "-x265-params",
            "pools=1:frame-threads=1:log-level=error",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-colorspace",
            "bt2020nc",
            source,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    upload = FileStorage(
        stream=BytesIO(source.read_bytes()),
        filename=source.name,
        content_type="video/quicktime",
    )

    assert data_interface.add_gallery_media(owner, project, post, [upload]) == 1

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=pix_fmt,color_space,color_transfer,color_primaries",
            "-of",
            "json",
            post_dir / "hdr-phone.mp4",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output_stream = json.loads(probe.stdout)["streams"][0]
    assert output_stream == {
        "pix_fmt": "yuv420p",
        "color_space": "bt709",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
    }


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    ("extension", "codec"),
    [("webm", "libvpx-vp9"), ("3gp", "h263")],
)
def test_other_common_phone_and_browser_containers_normalize_to_mp4(
    gallery,
    tmp_path,
    extension,
    codec,
):
    data_interface, owner, project, post, post_dir = gallery
    source = tmp_path / f"capture.{extension}"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=176x144:rate=15",
            "-t",
            "0.2",
            "-c:v",
            codec,
            "-pix_fmt",
            "yuv420p",
            source,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    upload = FileStorage(
        stream=BytesIO(source.read_bytes()),
        filename=source.name,
        content_type="application/octet-stream",
    )

    assert data_interface.add_gallery_media(owner, project, post, [upload]) == 1

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "json",
            post_dir / "capture.mp4",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(probe.stdout)["streams"][0] == {
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
    }
