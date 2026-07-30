import logging
import secrets
import time
import web_app.tubio as tubio_facade
from datetime import datetime, timezone

from flask import request

from web_app.config import ConfigManager
from web_app.helpers import cur_user
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis
from web_app.tubio.data_interface import AudioMetadata
from web_app.tubio.services.surprise import _surprise_is_expired

def cache_audio_for_user(crc: int):
    log_event("tubio", "tubio.cache_requested", crc=crc)
    data = tubio_facade.DataInterface()
    with data.edit_metadata() as metadata:
        user_metadata = metadata.get_user(cur_user().id)
        surprise = user_metadata.get_surprise_playlist()
        if surprise is not None and _surprise_is_expired(surprise):
            surprise = None
        can_access = any(
            crc in playlist.audio_crcs
            for playlist in user_metadata.playlists.values()
            if playlist.last_active is None
        )
        if surprise is not None and crc in surprise.audio_crcs:
            surprise.last_active = datetime.now(timezone.utc)
            can_access = True
        audio = metadata.audios.get(crc)
        if audio is not None:
            audio = audio.model_copy(deep=True)
    if not can_access:
        log_event(
            "tubio",
            "tubio.cache_rejected",
            level=logging.WARNING,
            crc=crc,
            reason="access_denied",
        )
        return {"error": "Audio not found"}, 404
    if audio is None:
        return {"error": "Audio metadata not found"}, 404

    file_path = data.app_audio_dir / f"{crc}.m4a"
    if audio.is_cached and file_path.exists():
        log_event(
            "tubio",
            "tubio.cache_completed",
            crc=crc,
            video_id=audio.yt_video_id,
            source="cache_hit",
        )
        return {"success": True, "is_cached": True, "video_id": audio.yt_video_id}

    cfg = ConfigManager().tubio
    key = cfg.surprise_cache_redis_prefix + str(crc)
    token = secrets.token_urlsafe(cfg.surprise_cache_claim_token_bytes).encode()
    redis = get_redis()
    if not redis.set(key, token, nx=True, ex=cfg.surprise_cache_claim_ttl_s):
        log_event(
            "tubio",
            "tubio.cache_in_progress",
            crc=crc,
            video_id=audio.yt_video_id,
        )
        return {
            "success": False,
            "is_cached": False,
            "status": "in_progress",
            "video_id": audio.yt_video_id,
        }, 202

    try:
        log_event(
            "tubio",
            "tubio.cache_materialization_started",
            crc=crc,
            video_id=audio.yt_video_id,
        )
        tubio_facade.AudioDownloader.cache_youtube_audio(audio)
        log_event(
            "tubio",
            "tubio.cache_completed",
            crc=crc,
            video_id=audio.yt_video_id,
            source="materialized",
        )
        return {"success": True, "is_cached": True, "video_id": audio.yt_video_id}
    except Exception as error:
        log_event(
            "tubio",
            "tubio.cache_failed",
            level=logging.ERROR,
            crc=crc,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {"error": "Could not convert this track"}, 500
    finally:
        if redis.get(key) == token:
            redis.delete(key)
