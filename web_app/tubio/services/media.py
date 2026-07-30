import logging
import time
import web_app.tubio as tubio_facade

from web_app.config import ConfigManager
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis
from web_app.tubio.data_interface import AudioMetadata

def redownload_audio(audio_metadata: AudioMetadata) -> None:
    # TODO: redownload might take sometime so it could be jarring for end user
    # make it more obvious what is going on in the background

    file_path = tubio_facade.DataInterface().get_audio_path(audio_metadata.crc)

    if file_path.exists():
        log_event(
            "tubio",
            "tubio.cache_metadata_repaired",
            level=logging.WARNING,
            crc=audio_metadata.crc,
        )
        audio_metadata.is_cached = True
        tubio_facade.DataInterface().upsert_audio_metadata(audio_metadata)
        return

    if not audio_metadata.yt_video_id:
        log_event(
            "tubio",
            "tubio.redownload_rejected",
            level=logging.ERROR,
            crc=audio_metadata.crc,
            reason="missing_video_id",
        )
        raise ValueError("No YouTube video ID associated with this audio.")

    log_event(
        "tubio",
        "tubio.redownload_started",
        crc=audio_metadata.crc,
        video_id=audio_metadata.yt_video_id,
    )
    tubio_facade.AudioDownloader.cache_youtube_audio(audio_metadata)

    log_event(
        "tubio",
        "tubio.redownload_completed",
        crc=audio_metadata.crc,
        video_id=audio_metadata.yt_video_id,
    )
