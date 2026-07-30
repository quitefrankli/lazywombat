import logging
import random
import secrets
import web_app.tubio as tubio_facade
from datetime import datetime, timedelta, timezone

from flask import request
from flask_login import login_required

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter, parse_request
from web_app.logging_utils import log_event
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.tubio.data_interface import AudioMetadata, DataInterface, Playlist
from web_app.tubio.services.playlists import (
    _add_track_occurrences,
    _playlist_track_data,
    get_cached_yt_vid_ids,
    get_playlists_data,
)
from web_app.tubio.surprise import reserve_audio_metadata
from web_app.tubio.services.surprise import (
    _active_surprise,
    _grow_surprise,
    _pick_surprise_candidates,
    _resolve_surprise_seed,
    _surprise_is_expired,
    _surprise_payload,
)

@tubio_api.route("/surprise", methods=["GET"])
def get_surprise_playlist():
    playlist = _active_surprise(touch=True)
    log_event("tubio", "tubio.surprise_restored", found=playlist is not None)
    return {"playlist": _surprise_payload(playlist) if playlist else None}


@tubio_api.route("/surprise", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def create_surprise_playlist():
    cfg = ConfigManager().tubio
    seed_crc_raw = request.form.get("seed_crc")
    seed_crc = None
    seed_video_id = None
    if seed_crc_raw is not None:
        try:
            seed_crc = int(seed_crc_raw)
        except (TypeError, ValueError):
            return {"error": "Invalid seed track"}, 400
        seed_video_id, seed_error, seed_error_status = _resolve_surprise_seed(
            seed_crc
        )
        if seed_error:
            assert seed_error_status is not None
            return {"error": seed_error}, seed_error_status

    playlist = Playlist(
        name=cfg.surprise_playlist_name,
        last_active=datetime.now(timezone.utc),
    )
    log_event(
        "tubio",
        "tubio.surprise_create_started",
        initial_tracks=cfg.surprise_buffer_size,
        seed_crc=seed_crc,
        seed_video_id=seed_video_id,
    )
    data = tubio_facade.DataInterface()
    try:
        with data.edit_metadata() as metadata:
            existing = metadata.get_user(
                cur_user().id
            ).get_surprise_playlist()
            if existing is not None and not _surprise_is_expired(existing):
                existing.last_active = datetime.now(timezone.utc)
        candidates = _pick_surprise_candidates(
            playlist,
            cfg.surprise_buffer_size,
            seed_video_id=seed_video_id,
        )
        if not candidates:
            empty_reason = (
                "no_library"
                if seed_video_id is None
                and not get_cached_yt_vid_ids(cur_user())
                else None
            )
            log_event(
                "tubio",
                "tubio.surprise_create_exhausted",
                level=logging.WARNING,
                empty_reason=empty_reason or "no_fresh_candidates",
            )
            return {
                "exhausted": True,
                "empty_reason": empty_reason,
            }, 200
        with data.edit_metadata() as metadata:
            playlist.last_active = datetime.now(timezone.utc)
            for candidate in candidates:
                crc = reserve_audio_metadata(metadata, candidate)
                if crc not in playlist.audio_crcs:
                    playlist.audio_crcs.append(crc)
            metadata.get_user(cur_user().id).set_surprise_playlist(playlist)
        playlist = playlist.model_copy(deep=True)
        log_event(
            "tubio",
            "tubio.surprise_created",
            tracks=len(playlist.audio_crcs),
        )
        return {"playlist": _surprise_payload(playlist)}, 200
    finally:
        try:
            data.cleanup_unused_resources()
        except Exception as error:
            log_event(
                "tubio",
                "tubio.surprise_cleanup_failed",
                level=logging.ERROR,
                exc_info=error,
                error_type=type(error).__name__,
            )


@tubio_api.route("/surprise/grow", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def grow_surprise_playlist():
    playlist = _active_surprise(touch=True)
    if playlist is None:
        log_event(
            "tubio",
            "tubio.surprise_grow_rejected",
            level=logging.WARNING,
            reason="playlist_not_found",
        )
        return {"error": "Surprise playlist not found"}, 404
    return _grow_surprise(playlist)


@tubio_api.route("/surprise/tracks/<int:crc>/favourite", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def favourite_surprise_track(crc: int):
    log_event("tubio", "tubio.surprise_favourite_requested", crc=crc)
    with tubio_facade.DataInterface().edit_metadata() as metadata:
        user_metadata = metadata.get_user(cur_user().id)
        playlist = user_metadata.get_surprise_playlist()
        if playlist is None or _surprise_is_expired(playlist):
            return {"error": "Surprise playlist not found"}, 404
        if crc not in playlist.audio_crcs:
            return {"error": "Surprise track not found"}, 404
        if crc not in metadata.audios:
            return {"error": "Audio metadata not found"}, 404
        playlist.last_active = datetime.now(timezone.utc)
        user_metadata.add_to_playlist(
            crc, ConfigManager().tubio.default_playlist_name
        )
        playlist = playlist.model_copy(deep=True)
    log_event("tubio", "tubio.surprise_favourite_completed", crc=crc)
    return {"success": True, "crc": crc, "playlist": _surprise_payload(playlist)}


@tubio_api.route("/surprise/save", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def save_surprise_playlist():
    playlist = _active_surprise(touch=True)
    if playlist is None:
        return {"error": "Surprise playlist not found"}, 404
    playlist_name = request.form.get("playlist_name", "").strip()
    log_event(
        "tubio",
        "tubio.surprise_save_requested",
        playlist=playlist_name,
        tracks=len(playlist.audio_crcs),
    )
    if not playlist_name:
        return {"error": "Playlist name cannot be empty"}, 400
    with tubio_facade.DataInterface().edit_metadata() as metadata:
        user_metadata = metadata.get_user(cur_user().id)
        if playlist_name in user_metadata.playlists:
            return {"error": f'Playlist "{playlist_name}" already exists'}, 409
        playlist = user_metadata.pop_surprise_playlist()
        if playlist is None or _surprise_is_expired(playlist):
            return {"error": "Surprise playlist not found"}, 404
        playlist.name = playlist_name
        playlist.audio_crcs.reverse()
        playlist.last_active = None
        user_metadata.playlists[playlist_name] = playlist
        saved_count = len(playlist.audio_crcs)
    log_event(
        "tubio",
        "tubio.surprise_saved",
        playlist=playlist_name,
        tracks=saved_count,
    )
    return {
        "success": True,
        "message": f'Saved playlist "{playlist_name}"',
        "playlist_name": playlist_name,
        "saved_count": saved_count,
        "skipped": [],
        "playlists": get_playlists_data(cur_user()),
    }
