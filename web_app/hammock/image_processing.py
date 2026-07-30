"""Content-driven image normalization for Hammock gallery uploads."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from web_app.config import ConfigManager
from web_app.errors import APIError

try:
    from pillow_heif import register_heif_opener
except ImportError:  # The dependency may be absent in an unprovisioned dev env.
    register_heif_opener = None
else:
    register_heif_opener()


class ImageProcessingError(APIError):
    """Raised when an upload cannot be safely normalized as an image."""


def identify_image_format(source: Path) -> str | None:
    """Return Pillow's content-derived format name, or ``None`` if not an image."""

    try:
        with Image.open(source) as image:
            return image.format.upper() if image.format else None
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return None


def image_decoded_pixels(source: Path) -> int:
    """Return decoded canvas pixels without loading the full raster."""

    try:
        with Image.open(source) as image:
            width, height = image.size
            return width * height
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as error:
        raise ImageProcessingError(
            f"Could not inspect {source.name} as an image"
        ) from error


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or "transparency" in image.info


def _convert_to_srgb(image: Image.Image) -> Image.Image:
    has_alpha = _has_alpha(image)
    if image.mode == "P" and "transparency" in image.info:
        image = image.copy()
        image.apply_transparency()
    alpha = image.convert("RGBA").getchannel("A") if has_alpha else None
    colour = image.convert("RGB")
    icc_profile = image.info.get("icc_profile")

    if icc_profile:
        try:
            from PIL import ImageCms

            source_profile = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
            srgb_profile = ImageCms.createProfile("sRGB")
            colour = ImageCms.profileToProfile(
                colour,
                source_profile,
                srgb_profile,
                outputMode="RGB",
            )
        except (ImageCms.PyCMSError, OSError, TypeError, ValueError):
            # Malformed or unsupported profiles should not prevent an otherwise
            # valid image from being normalized.
            pass

    if alpha is None:
        return colour

    colour.putalpha(alpha)
    return colour


def normalize_image_to_webp(source: Path) -> bytes:
    """Decode ``source`` by content and return metadata-free WebP bytes.

    The first frame is used for animated inputs. Orientation is applied before
    resizing, transparency is retained, and embedded colour is transformed to
    sRGB when Pillow can interpret the source profile.
    """

    cfg = ConfigManager().hammock
    try:
        with Image.open(source) as opened:
            width, height = opened.size
            if width * height > cfg.max_image_pixels:
                raise ImageProcessingError(
                    f"Image {source.name} exceeds the decoded pixel limit"
                )

            opened.seek(0)
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            normalized = _convert_to_srgb(oriented)
            normalized.thumbnail(
                (cfg.gallery_thumb_max_px, cfg.gallery_thumb_max_px),
                Image.Resampling.LANCZOS,
            )

            # Detach the pixels from Pillow's source info dictionary so EXIF,
            # GPS, ICC, and XMP cannot be copied to the browser-facing output.
            metadata_free = Image.frombytes(
                normalized.mode,
                normalized.size,
                normalized.tobytes(),
            )
            output = BytesIO()
            metadata_free.save(
                output,
                "WEBP",
                quality=cfg.gallery_thumb_quality,
                method=6,
            )
            return output.getvalue()
    except ImageProcessingError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as error:
        raise ImageProcessingError(
            f"Could not decode {source.name} as an image"
        ) from error
