import binascii
import logging
import random

from datetime import datetime, timedelta, timezone
from flask import render_template, request

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter
from web_app.logging_utils import log_event
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.tubio.data_interface import (
    AudioMetadata,
    DataInterface,
    Metadata,
    Playlist,
)
from web_app.tubio.routes.playlists import (
    _track_data,
    add_track_occurrences,
    get_cached_yt_vid_ids,
    get_playlists_data,
)


def reserve_audio_metadata(metadata: Metadata, candidate: dict) -> int:
    video_id = candidate["video_id"]
    for audio in metadata.audios.values():
        if audio.yt_video_id == video_id:
            log_event(
                "tubio",
                "tubio.surprise_metadata_reused",
                crc=audio.crc,
                video_id=video_id,
                cached=audio.is_cached,
            )
            return audio.crc

    attempts = ConfigManager().tubio.surprise_crc_collision_attempts
    for attempt in range(attempts):
        discriminator = video_id if attempt == 0 else f"{video_id}:{attempt}"
        crc = binascii.crc32(discriminator.encode())
        if crc in metadata.audios:
            continue
        metadata.audios[crc] = AudioMetadata(
            crc=crc,
            title=candidate.get("title", ""),
            yt_video_id=video_id,
            is_cached=False,
            source_url=ConfigManager().tubio.youtube_watch_url_template.format(
                video_id=video_id
            ),
        )
        log_event(
            "tubio",
            "tubio.surprise_metadata_reserved",
            crc=crc,
            video_id=video_id,
            collision_attempt=attempt,
        )
        return crc
    raise RuntimeError("Could not reserve a unique audio identifier")


def _surprise_is_expired(
    playlist: Playlist,
    now: datetime | None = None,
) -> bool:
    if playlist.last_active is None:
        return True
    last_active = playlist.last_active
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=timezone.utc)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        seconds=ConfigManager().tubio.surprise_playlist_inactivity_ttl_s
    )
    return last_active < cutoff


def _active_surprise(
    *,
    touch: bool = False,
    data: DataInterface | None = None,
) -> Playlist | None:
    data = data or DataInterface()
    now = datetime.now(timezone.utc)
    with data.edit_metadata() as metadata:
        user_metadata = metadata.users.get(cur_user().id)
        if user_metadata is None:
            return None
        playlist = user_metadata.get_surprise_playlist()
        if playlist is None or _surprise_is_expired(playlist, now):
            return None
        if touch:
            playlist.last_active = now
        return playlist.model_copy(deep=True)


def _surprise_payload(
    playlist: Playlist,
    *,
    data: DataInterface | None = None,
) -> dict:
    data = data or DataInterface()
    metadata = data.get_metadata()
    user_metadata = metadata.users.get(cur_user().id)
    if user_metadata is None:
        tracks = []
    else:
        favourites = set(
            user_metadata.get_playlist(
                ConfigManager().tubio.default_playlist_name
            ).audio_crcs
        )
        tracks = add_track_occurrences([
            _track_data(
                data,
                metadata.audios[crc],
                user_metadata,
                is_favourite=crc in favourites,
            )
            for crc in playlist.audio_crcs
            if crc in metadata.audios
        ])

    missing_count = len(playlist.audio_crcs) - len(tracks)
    log_event(
        "tubio",
        "tubio.surprise_payload_prepared",
        tracks=len(tracks),
        missing_metadata=missing_count,
        last_active=playlist.last_active,
    )
    if missing_count:
        log_event(
            "tubio",
            "tubio.surprise_metadata_missing",
            level=logging.WARNING,
            missing=missing_count,
        )
    payload = playlist.model_dump(mode="json")
    payload["html"] = render_template(
        "surprise_playlist.html",
        playlist_name=playlist.name,
        playlist_data=tracks,
    )
    return payload


def _resolve_surprise_seed(
    seed_crc: int,
    *,
    data: DataInterface,
) -> tuple[str | None, str | None, int | None]:
    metadata = data.get_metadata()
    user_metadata = metadata.users.get(cur_user().id)
    audio = metadata.audios.get(seed_crc)
    if user_metadata is None or audio is None:
        return None, "Track not found", 404

    accessible = any(
        seed_crc in playlist.audio_crcs
        for playlist in user_metadata.get_playlists()
    )
    surprise = user_metadata.get_surprise_playlist()
    if (
        surprise is not None
        and not _surprise_is_expired(surprise)
        and seed_crc in surprise.audio_crcs
    ):
        accessible = True
    if not accessible:
        return None, "Track not found", 404
    if audio.yt_video_id:
        return audio.yt_video_id, None, None

    query = f"{ConfigManager().tubio.search_prefix}{audio.title}".strip()
    if not query:
        return None, "Could not match this uploaded track on YouTube", 422
    try:
        search_data = AudioDownloader.search_youtube(query, set(), page=0)
    except Exception as error:
        log_event(
            "tubio",
            "tubio.surprise_seed_match_failed",
            level=logging.ERROR,
            crc=seed_crc,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return None, "Could not match this uploaded track on YouTube", 422
    match = next((
        result.get("video_id")
        for result in search_data.get("results", [])
        if result.get("video_id")
    ), None)
    if match is None:
        return None, "Could not match this uploaded track on YouTube", 422
    return match, None, None


def _pick_surprise_candidates(
    playlist: Playlist,
    count: int,
    *,
    data: DataInterface,
    seed_video_id: str | None = None,
) -> list[dict]:
    cfg = ConfigManager().tubio
    owned_ids = get_cached_yt_vid_ids(cur_user(), data=data)
    if not owned_ids and seed_video_id is None:
        log_event("tubio", "tubio.surprise_candidates_empty_library")
        return []

    metadata = data.get_metadata()
    seen_video_ids = {
        metadata.audios[crc].yt_video_id
        for crc in playlist.audio_crcs
        if crc in metadata.audios and metadata.audios[crc].yt_video_id
    }
    last_video_id = next((
        metadata.audios[crc].yt_video_id
        for crc in reversed(playlist.audio_crcs)
        if crc in metadata.audios and metadata.audios[crc].yt_video_id
    ), "")
    skip = owned_ids | seen_video_ids
    if seed_video_id is not None:
        seeds = [seed_video_id]
        skip.add(seed_video_id)
    else:
        seeds = [last_video_id] if last_video_id else []
        remaining = list(owned_ids - set(seeds))
        random.shuffle(remaining)
        seeds.extend(remaining)

    selected = []
    log_event(
        "tubio",
        "tubio.surprise_candidate_selection_started",
        requested=count,
        owned=len(owned_ids),
        seen=len(seen_video_ids),
        seeds=len(seeds),
    )
    for seed in seeds:
        candidates = AudioDownloader.get_mix_related(seed)
        log_event(
            "tubio",
            "tubio.surprise_mix_loaded",
            seed=seed,
            candidates=len(candidates),
        )
        random.shuffle(candidates)
        for candidate in candidates:
            if candidate["video_id"] in skip:
                continue
            if timedelta(seconds=candidate.get("duration_s", 0)) > cfg.max_video_length:
                continue
            selected.append(candidate)
            skip.add(candidate["video_id"])
            if len(selected) >= count:
                log_event(
                    "tubio",
                    "tubio.surprise_candidate_selection_completed",
                    selected=len(selected),
                )
                return selected
    log_event(
        "tubio",
        "tubio.surprise_candidate_selection_exhausted",
        selected=len(selected),
        requested=count,
    )
    return selected


def _grow_surprise(
    playlist: Playlist,
    count: int | None = None,
    *,
    data: DataInterface | None = None,
):
    data = data or DataInterface()
    count = count or ConfigManager().tubio.surprise_grow_batch_size
    log_event(
        "tubio",
        "tubio.surprise_grow_started",
        requested=count,
        existing=len(playlist.audio_crcs),
    )
    candidates = _pick_surprise_candidates(playlist, count, data=data)
    if not candidates:
        empty_reason = (
            "no_library"
            if not get_cached_yt_vid_ids(cur_user(), data=data)
            else None
        )
        log_event(
            "tubio",
            "tubio.surprise_grow_exhausted",
            empty_reason=empty_reason or "no_fresh_candidates",
        )
        return {"exhausted": True, "empty_reason": empty_reason}, 200

    try:
        with data.edit_metadata() as metadata:
            current = metadata.get_user(cur_user().id).get_surprise_playlist()
            if current is None or _surprise_is_expired(current):
                log_event(
                    "tubio",
                    "tubio.surprise_grow_rejected",
                    level=logging.WARNING,
                    reason="playlist_not_found",
                )
                return {"error": "Surprise playlist not found"}, 404
            for candidate in candidates:
                crc = reserve_audio_metadata(metadata, candidate)
                if crc not in current.audio_crcs:
                    current.audio_crcs.append(crc)
            current.last_active = datetime.now(timezone.utc)
            playlist = current.model_copy(deep=True)
    except Exception as error:
        log_event(
            "tubio",
            "tubio.surprise_grow_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {"error": "Surprise playlist changed; reload it and try again."}, 409

    log_event(
        "tubio",
        "tubio.surprise_grow_completed",
        added=len(candidates),
        total=len(playlist.audio_crcs),
    )
    return {"playlist": _surprise_payload(playlist, data=data)}, 200


@tubio_api.route("/surprise", methods=["GET"])
def get_surprise_playlist():
    data = DataInterface()
    playlist = _active_surprise(touch=True, data=data)
    log_event("tubio", "tubio.surprise_restored", found=playlist is not None)
    return {
        "playlist": _surprise_payload(playlist, data=data) if playlist else None,
    }


@tubio_api.route("/surprise", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def create_surprise_playlist():
    cfg = ConfigManager().tubio
    data = DataInterface()
    seed_crc_raw = request.form.get("seed_crc")
    seed_crc = None
    seed_video_id = None
    if seed_crc_raw is not None:
        try:
            seed_crc = int(seed_crc_raw)
        except (TypeError, ValueError):
            log_event(
                "tubio",
                "tubio.surprise_create_rejected",
                level=logging.WARNING,
                reason="invalid_seed",
            )
            return {"error": "Invalid seed track"}, 400
        seed_video_id, seed_error, seed_status = _resolve_surprise_seed(
            seed_crc,
            data=data,
        )
        if seed_error:
            log_event(
                "tubio",
                "tubio.surprise_create_rejected",
                level=logging.WARNING,
                reason="unavailable_seed",
                seed_crc=seed_crc,
            )
            return {"error": seed_error}, seed_status

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
    try:
        with data.edit_metadata() as metadata:
            existing = metadata.get_user(cur_user().id).get_surprise_playlist()
            if existing is not None and not _surprise_is_expired(existing):
                existing.last_active = datetime.now(timezone.utc)

        candidates = _pick_surprise_candidates(
            playlist,
            cfg.surprise_buffer_size,
            data=data,
            seed_video_id=seed_video_id,
        )
        if not candidates:
            empty_reason = (
                "no_library"
                if seed_video_id is None
                and not get_cached_yt_vid_ids(cur_user(), data=data)
                else None
            )
            log_event(
                "tubio",
                "tubio.surprise_create_exhausted",
                level=logging.WARNING,
                empty_reason=empty_reason or "no_fresh_candidates",
            )
            return {"exhausted": True, "empty_reason": empty_reason}, 200

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
        return {"playlist": _surprise_payload(playlist, data=data)}, 200
    except Exception as error:
        log_event(
            "tubio",
            "tubio.surprise_create_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {"error": "Could not create Surprise playlist"}, 500
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
    data = DataInterface()
    playlist = _active_surprise(touch=True, data=data)
    if playlist is None:
        log_event(
            "tubio",
            "tubio.surprise_grow_rejected",
            level=logging.WARNING,
            reason="playlist_not_found",
        )
        return {"error": "Surprise playlist not found"}, 404
    return _grow_surprise(playlist, data=data)


@tubio_api.route("/surprise/tracks/<int:crc>/favourite", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def favourite_surprise_track(crc: int):
    data = DataInterface()
    with data.edit_metadata() as metadata:
        user_metadata = metadata.users.get(cur_user().id)
        playlist = (
            user_metadata.get_surprise_playlist()
            if user_metadata is not None
            else None
        )
        if playlist is None or _surprise_is_expired(playlist):
            log_event(
                "tubio",
                "tubio.surprise_favourite_rejected",
                level=logging.WARNING,
                crc=crc,
                reason="playlist_not_found",
            )
            return {"error": "Surprise playlist not found"}, 404
        if crc not in playlist.audio_crcs or crc not in metadata.audios:
            log_event(
                "tubio",
                "tubio.surprise_favourite_rejected",
                level=logging.WARNING,
                crc=crc,
                reason="track_not_found",
            )
            return {"error": "Surprise track not found"}, 404
        playlist.last_active = datetime.now(timezone.utc)
        user_metadata.add_to_playlist(
            crc,
            ConfigManager().tubio.default_playlist_name,
        )
        playlist = playlist.model_copy(deep=True)

    log_event("tubio", "tubio.surprise_favourite_completed", crc=crc)
    return {
        "success": True,
        "crc": crc,
        "playlist": _surprise_payload(playlist, data=data),
        "library_html": render_template(
            "playlists.html",
            playlists=get_playlists_data(cur_user(), data=data),
        ),
    }


@tubio_api.route("/surprise/save", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def save_surprise_playlist():
    data = DataInterface()
    playlist = _active_surprise(touch=True, data=data)
    if playlist is None:
        log_event(
            "tubio",
            "tubio.surprise_save_rejected",
            level=logging.WARNING,
            reason="playlist_not_found",
        )
        return {"error": "Surprise playlist not found"}, 404

    playlist_name = request.form.get("playlist_name", "").strip()
    if not playlist_name:
        log_event(
            "tubio",
            "tubio.surprise_save_rejected",
            level=logging.WARNING,
            reason="empty_name",
        )
        return {"error": "Playlist name cannot be empty"}, 400

    try:
        with data.edit_metadata() as metadata:
            user_metadata = metadata.get_user(cur_user().id)
            if playlist_name in user_metadata.playlists:
                log_event(
                    "tubio",
                    "tubio.surprise_save_rejected",
                    level=logging.WARNING,
                    reason="already_exists",
                )
                return {"error": f'Playlist "{playlist_name}" already exists'}, 409
            playlist = user_metadata.pop_surprise_playlist()
            if playlist is None or _surprise_is_expired(playlist):
                log_event(
                    "tubio",
                    "tubio.surprise_save_rejected",
                    level=logging.WARNING,
                    reason="playlist_changed",
                )
                return {"error": "Surprise playlist not found"}, 404
            playlist.name = playlist_name
            playlist.audio_crcs.reverse()
            playlist.last_active = None
            user_metadata.playlists[playlist_name] = playlist
            saved_count = len(playlist.audio_crcs)
    except Exception as error:
        log_event(
            "tubio",
            "tubio.surprise_save_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {"error": "Could not save playlist"}, 500

    log_event("tubio", "tubio.surprise_saved", tracks=saved_count)
    return {
        "success": True,
        "message": f'Saved playlist "{playlist_name}"',
        "playlist_name": playlist_name,
        "saved_count": saved_count,
        "skipped": [],
        "library_html": render_template(
            "playlists.html",
            playlists=get_playlists_data(cur_user(), data=data),
        ),
    }
