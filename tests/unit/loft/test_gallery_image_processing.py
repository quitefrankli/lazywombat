from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, features

from web_app.config import ConfigManager
from web_app.loft.image_processing import (
    ImageProcessingError,
    normalize_image_to_webp,
)


def _write_image(
    path: Path,
    image: Image.Image,
    image_format: str,
    **save_kwargs,
) -> Path:
    image.save(path, format=image_format, **save_kwargs)
    return path


def _normalized_image(path: Path) -> Image.Image:
    output = Image.open(BytesIO(normalize_image_to_webp(path)))
    output.load()
    return output


def test_decodes_image_content_instead_of_trusting_filename_extension(tmp_path):
    source = _write_image(
        tmp_path / "actually-a-png.jpg",
        Image.new("RGB", (17, 11), (20, 80, 140)),
        "PNG",
    )

    with _normalized_image(source) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (17, 11)


def test_applies_exif_orientation_and_strips_private_metadata(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private description"
    exif[34853] = {1: "N"}
    source = _write_image(
        tmp_path / "phone.jpg",
        Image.new("RGB", (18, 10), (50, 100, 150)),
        "JPEG",
        exif=exif,
    )

    with _normalized_image(source) as normalized:
        assert normalized.size == (10, 18)
        assert not normalized.getexif()
        assert "exif" not in normalized.info
        assert "icc_profile" not in normalized.info
        assert "xmp" not in normalized.info


def test_preserves_alpha_when_normalizing_palette_image(tmp_path):
    image = Image.new("P", (3, 1))
    image.putpalette(
        [
            255, 0, 0,
            0, 255, 0,
            0, 0, 255,
        ]
        + [0] * (256 * 3 - 9)
    )
    image.putdata([0, 1, 2])
    image.info["transparency"] = bytes([0, 127, 255])
    source = _write_image(tmp_path / "alpha.png", image, "PNG")

    with _normalized_image(source) as normalized:
        assert normalized.mode == "RGBA"
        assert list(normalized.getchannel("A").getdata()) == [0, 127, 255]


def test_converts_embedded_colour_profile_to_srgb_and_does_not_embed_it(
    tmp_path,
):
    pytest.importorskip("PIL.ImageCms")
    from PIL import ImageCms

    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    source = _write_image(
        tmp_path / "profiled.tiff",
        Image.new("RGB", (9, 7), (45, 90, 135)),
        "TIFF",
        icc_profile=profile.tobytes(),
    )

    with _normalized_image(source) as normalized:
        assert normalized.mode == "RGB"
        assert "icc_profile" not in normalized.info
        assert normalized.getpixel((0, 0)) == pytest.approx((45, 90, 135), abs=5)


def test_rejects_image_above_configured_decoded_pixel_limit(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(ConfigManager().loft, "max_image_pixels", 99)
    source = _write_image(
        tmp_path / "too-many-pixels.png",
        Image.new("RGB", (10, 10)),
        "PNG",
    )

    with pytest.raises(ImageProcessingError, match="decoded pixel limit"):
        normalize_image_to_webp(source)


def test_downsizes_to_configured_maximum_dimension(tmp_path, monkeypatch):
    monkeypatch.setattr(ConfigManager().loft, "gallery_thumb_max_px", 40)
    source = _write_image(
        tmp_path / "wide.png",
        Image.new("RGB", (100, 50)),
        "PNG",
    )

    with _normalized_image(source) as normalized:
        assert normalized.size == (40, 20)


def test_invalid_content_has_a_stable_processing_error(tmp_path):
    source = tmp_path / "not-an-image.heic"
    source.write_bytes(b"not an image")

    with pytest.raises(ImageProcessingError, match="Could not decode"):
        normalize_image_to_webp(source)


def test_animated_image_uses_documented_first_frame(tmp_path):
    source = tmp_path / "animation.gif"
    frames = [
        Image.new("RGB", (8, 6), (220, 20, 20)),
        Image.new("RGB", (8, 6), (20, 20, 220)),
    ]
    frames[0].save(
        source,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )

    with _normalized_image(source) as normalized:
        red, green, blue = normalized.getpixel((0, 0))
        assert red > 180
        assert green < 60
        assert blue < 60


@pytest.mark.skipif(
    not features.check("avif"),
    reason="Pillow was built without AVIF support",
)
def test_normalizes_avif_input(tmp_path):
    source = _write_image(
        tmp_path / "photo.avif",
        Image.new("RGB", (13, 8), (70, 120, 180)),
        "AVIF",
    )

    with _normalized_image(source) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (13, 8)


def test_normalizes_heif_input_when_pillow_heif_is_available(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    source = _write_image(
        tmp_path / "photo.heic",
        Image.new("RGB", (12, 7), (70, 120, 180)),
        "HEIF",
    )

    with _normalized_image(source) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (12, 7)
