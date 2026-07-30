import logging
import random
import web_app.tubio as tubio_facade
from datetime import datetime, timedelta, timezone
from flask import render_template

from web_app.config import ConfigManager
from web_app.helpers import cur_user
from web_app.logging_utils import log_event
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.tubio.data_interface import AudioMetadata, DataInterface, Playlist
from web_app.tubio.services.playlists import (
    _add_track_occurrences,
    _playlist_track_data,
    get_cached_yt_vid_ids,
)
from web_app.tubio.surprise import reserve_audio_metadata

def _surprise_payload(playlist: Playlist) -> dict:
    payload = playlist.model_dump(mode="json")
    metadata = tubio_facade.DataInterface().get_metadata()
    user_metadata = tubio_facade.DataInterface().get_user_metadata(cur_user())
    favourites = set(user_metadata.get_playlist().audio_crcs)
    tracks = _add_track_occurrences([
        _playlist_track_data(
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
    payload["html"] = render_template(
        "surprise_playlist.html",
        playlist_name=playlist.name,
        playlist_data=tracks,
    )
    return payload


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


def _active_surprise(*, touch: bool = False) -> Playlist | None:
    now = datetime.now(timezone.utc)
    with tubio_facade.DataInterface().edit_metadata() as metadata:
        playlist = metadata.get_user(cur_user().id).get_surprise_playlist()
        if playlist is None or _surprise_is_expired(playlist, now):
            return None
        if touch:
            playlist.last_active = now
        return playlist.model_copy(deep=True)


def _pick_surprise_candidates(
    playlist: Playlist,
    count: int,
    *,
    seed_video_id: str | None = None,
) -> list[dict]:
    cfg = ConfigManager().tubio
    owned_ids = {vid for vid in tubio_facade.get_cached_yt_vid_ids(cur_user()) if vid}
    if not owned_ids and seed_video_id is None:
        log_event("tubio", "tubio.surprise_candidates_empty_library")
        return []
    metadata = tubio_facade.DataInterface().get_metadata()
    seen_video_ids = {
        metadata.audios[crc].yt_video_id
        for crc in playlist.audio_crcs
        if crc in metadata.audios and metadata.audios[crc].yt_video_id
    }
    last_video_id = next(
        (
            metadata.audios[crc].yt_video_id
            for crc in reversed(playlist.audio_crcs)
            if crc in metadata.audios and metadata.audios[crc].yt_video_id
        ),
        "",
    )
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
        candidates = tubio_facade.AudioDownloader.get_mix_related(seed)
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


def _resolve_surprise_seed(
    seed_crc: int,
) -> tuple[str | None, str | None, int | None]:
    metadata = tubio_facade.DataInterface().get_metadata()
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
        search_data = tubio_facade.AudioDownloader.search_youtube(query, set(), page=0)
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
    match = next(
        (
            result.get("video_id")
            for result in search_data.get("results", [])
            if result.get("video_id")
        ),
        None,
    )
    if match is None:
        return None, "Could not match this uploaded track on YouTube", 422
    return match, None, None


def _grow_surprise(playlist: Playlist, count: int | None = None):
    if count is None:
        count = ConfigManager().tubio.surprise_grow_batch_size
    log_event(
        "tubio",
        "tubio.surprise_grow_started",
        requested=count,
        existing=len(playlist.audio_crcs),
    )
    candidates = _pick_surprise_candidates(playlist, count)
    if not candidates:
        empty_reason = "no_library" if not tubio_facade.get_cached_yt_vid_ids(cur_user()) else None
        log_event(
            "tubio",
            "tubio.surprise_grow_exhausted",
            empty_reason=empty_reason or "no_fresh_candidates",
        )
        return {"exhausted": True, "empty_reason": empty_reason}, 200

    try:
        with tubio_facade.DataInterface().edit_metadata() as metadata:
            current = metadata.get_user(cur_user().id).get_surprise_playlist()
            if current is None or _surprise_is_expired(current):
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
    return {"playlist": _surprise_payload(playlist)}, 200
