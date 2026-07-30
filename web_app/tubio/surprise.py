import binascii
import logging

from web_app.config import ConfigManager
from web_app.tubio.data_interface import AudioMetadata, Metadata
from web_app.logging_utils import log_event


def _candidate_crc(video_id: str, attempt: int) -> int:
    discriminator = video_id if attempt == 0 else f"{video_id}:{attempt}"
    return binascii.crc32(discriminator.encode())


def reserve_audio_metadata(metadata: Metadata, candidate: dict) -> int:
    video_id = candidate["video_id"]
    for audio in metadata.audios.values():
        if audio.yt_video_id == video_id:
            log_event(
                "tubio", "tubio.surprise_metadata_reused",
                crc=audio.crc, video_id=video_id, cached=audio.is_cached,
            )
            return audio.crc

    for attempt in range(ConfigManager().tubio.surprise_crc_collision_attempts):
        crc = _candidate_crc(video_id, attempt)
        if crc not in metadata.audios:
            metadata.audios[crc] = AudioMetadata(
                crc=crc,
                title=candidate.get("title", ""),
                yt_video_id=video_id,
                is_cached=False,
                source_url=f"https://www.youtube.com/watch?v={video_id}",
            )
            log_event(
                "tubio", "tubio.surprise_metadata_reserved",
                crc=crc, video_id=video_id, collision_attempt=attempt,
            )
            return crc
    raise RuntimeError("Could not reserve a unique audio identifier")
