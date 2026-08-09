import logging

from flask import flash, redirect, request, url_for

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter
from web_app.logging_utils import log_event
from web_app.tubio import tubio_api
from web_app.tubio.data_interface import AudioMetadata, DataInterface, UserMetadata
from web_app.users import User


def get_cached_yt_vid_ids(
    user: User | None = None,
    *,
    data: DataInterface | None = None,
) -> set[str]:
    metadata = (data or DataInterface()).get_metadata()
    if user is None:
        return {
            audio.yt_video_id
            for audio in metadata.audios.values()
            if audio.yt_video_id
        }

    user_metadata = metadata.users.get(user.id)
    if user_metadata is None:
        return set()

    owned_crcs = {
        crc
        for playlist in user_metadata.get_playlists()
        for crc in playlist.audio_crcs
    }
    return {
        metadata.audios[crc].yt_video_id
        for crc in owned_crcs
        if crc in metadata.audios and metadata.audios[crc].yt_video_id
    }


def _track_data(
    data: DataInterface,
    audio: AudioMetadata,
    user_metadata: UserMetadata,
    *,
    is_favourite: bool = False,
) -> dict:
    playback_trim = user_metadata.get_playback_trim(audio.crc)
    if data.has_thumbnail(audio.crc):
        thumbnail_url = url_for(".serve_thumbnail", crc=audio.crc)
    elif audio.yt_video_id:
        thumbnail_url = ConfigManager().tubio.youtube_thumbnail_url_template.format(
            video_id=audio.yt_video_id
        )
    else:
        thumbnail_url = ""
    return {
        "crc": audio.crc,
        "title": audio.title,
        "thumbnail_url": thumbnail_url,
        "source_url": audio.source_url,
        "video_id": audio.yt_video_id,
        "trim_start_s": playback_trim.start_s,
        "trim_end_s": playback_trim.end_s,
        "is_cached": audio.is_cached,
        "is_favourite": is_favourite,
    }


def add_track_occurrences(tracks: list[dict]) -> list[dict]:
    """Give repeated tracks stable identities within a rendered playlist."""
    occurrences: dict[int, int] = {}
    for track in tracks:
        crc = track["crc"]
        track["occurrence"] = occurrences.get(crc, 0)
        occurrences[crc] = track["occurrence"] + 1
    return tracks


def get_playlists_data(
    user: User,
    *,
    data: DataInterface | None = None,
) -> list[tuple[str, list[dict]]]:
    data = data or DataInterface()
    metadata = data.get_metadata()
    user_metadata = metadata.users.get(user.id)
    if user_metadata is None:
        return []

    playlists = []
    for playlist in user_metadata.get_playlists():
        tracks = [
            _track_data(data, metadata.audios[crc], user_metadata)
            for crc in reversed(playlist.audio_crcs)
            if crc in metadata.audios
        ]
        playlists.append((playlist.name, add_track_occurrences(tracks)))
    return playlists


def _parse_track_crcs(raw_crcs: str) -> list[int]:
    parsed = []
    seen = set()
    for raw_crc in raw_crcs.split(','):
        raw_crc = raw_crc.strip()
        if not raw_crc:
            continue
        crc = int(raw_crc)
        if crc not in seen:
            parsed.append(crc)
            seen.add(crc)
    return parsed


@tubio_api.route('/create_playlist', methods=['POST'])
@limiter.limit(lambda: ConfigManager().tubio.playlist_create_rate_limit)
def create_playlist():
    playlist_name = request.form.get('playlist_name', '').strip()
    if not playlist_name:
        log_event(
            "tubio",
            "tubio.playlist_create_rejected",
            level=logging.WARNING,
            reason="empty_name",
        )
        flash('Playlist name cannot be empty.', 'error')
        return redirect(url_for('.index'))

    try:
        with DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(cur_user().id)
            if playlist_name in user_metadata.playlists:
                log_event(
                    "tubio",
                    "tubio.playlist_create_rejected",
                    level=logging.WARNING,
                    reason="already_exists",
                )
                flash(f'Playlist "{playlist_name}" already exists.', 'warning')
                return redirect(url_for('.index'))
            user_metadata.get_playlist(playlist_name)
            playlist_count = len(user_metadata.get_playlists())
    except Exception as error:
        log_event(
            "tubio",
            "tubio.playlist_create_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        flash('Error creating playlist.', 'error')
        return redirect(url_for('.index'))

    log_event(
        "tubio",
        "tubio.playlist_created",
        playlist_count=playlist_count,
    )
    flash(f'Playlist "{playlist_name}" created successfully!', 'success')
    return redirect(url_for('.index'))


@tubio_api.route('/move_tracks_to_playlist', methods=['POST'])
@limiter.limit(lambda: ConfigManager().tubio.playlist_move_rate_limit)
def move_tracks_to_playlist():
    target_playlist = request.form.get('target_playlist', '').strip()
    raw_crcs = request.form.get('song_crcs', '')
    if not target_playlist or not raw_crcs:
        reason = "missing_target" if not target_playlist else "missing_tracks"
        log_event(
            "tubio",
            "tubio.tracks_move_rejected",
            level=logging.WARNING,
            reason=reason,
        )
        flash(
            'Please select a target playlist.' if not target_playlist else 'No songs selected.',
            'error' if not target_playlist else 'warning',
        )
        return redirect(url_for('.index'))

    try:
        song_crcs = _parse_track_crcs(raw_crcs)
    except ValueError:
        song_crcs = []
    if not song_crcs:
        log_event(
            "tubio",
            "tubio.tracks_move_rejected",
            level=logging.WARNING,
            reason="invalid_tracks",
        )
        flash('No valid songs selected.', 'warning')
        return redirect(url_for('.index'))

    try:
        with DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.users.get(cur_user().id)
            if user_metadata is None:
                log_event(
                    "tubio",
                    "tubio.tracks_move_rejected",
                    level=logging.WARNING,
                    reason="not_owned",
                    tracks=len(song_crcs),
                    rejected=len(song_crcs),
                )
                flash('One or more songs are not in your playlists.', 'error')
                return redirect(url_for('.index'))
            target = user_metadata.playlists.get(target_playlist)
            if target is not None and target.last_active is not None:
                log_event(
                    "tubio",
                    "tubio.tracks_move_rejected",
                    level=logging.WARNING,
                    reason="temporary_target",
                    tracks=len(song_crcs),
                )
                flash('Please select a regular playlist.', 'error')
                return redirect(url_for('.index'))

            owned_crcs = {
                crc
                for playlist in user_metadata.get_playlists()
                for crc in playlist.audio_crcs
            }
            rejected = sum(crc not in owned_crcs for crc in song_crcs)
            if rejected:
                log_event(
                    "tubio",
                    "tubio.tracks_move_rejected",
                    level=logging.WARNING,
                    reason="not_owned",
                    tracks=len(song_crcs),
                    rejected=rejected,
                )
                flash('One or more songs are not in your playlists.', 'error')
                return redirect(url_for('.index'))

            for crc in song_crcs:
                user_metadata.remove_from_regular_playlists(crc)
                user_metadata.add_to_playlist(crc, target_playlist)
    except Exception as error:
        log_event(
            "tubio",
            "tubio.tracks_move_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        flash('Error moving songs to playlist.', 'error')
        return redirect(url_for('.index'))

    log_event("tubio", "tubio.tracks_moved", tracks=len(song_crcs))
    return redirect(url_for('.index'))


@tubio_api.route('/delete_playlist', methods=['POST'])
@limiter.limit(lambda: ConfigManager().tubio.playlist_delete_rate_limit)
def delete_playlist():
    playlist_name = request.form.get('playlist_name', '').strip()
    default_playlist = ConfigManager().tubio.default_playlist_name
    if not playlist_name or playlist_name == default_playlist:
        reason = "empty_name" if not playlist_name else "default_playlist"
        log_event(
            "tubio",
            "tubio.playlist_delete_rejected",
            level=logging.WARNING,
            reason=reason,
        )
        flash(
            'Playlist name cannot be empty.' if not playlist_name else 'Cannot delete the Favourites playlist.',
            'error',
        )
        return redirect(url_for('.index'))

    try:
        with DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.users.get(cur_user().id)
            playlist = (
                user_metadata.playlists.get(playlist_name)
                if user_metadata is not None
                else None
            )
            if playlist is None or playlist.last_active is not None:
                log_event(
                    "tubio",
                    "tubio.playlist_delete_rejected",
                    level=logging.WARNING,
                    reason="not_found",
                )
                flash(f'Playlist "{playlist_name}" does not exist.', 'warning')
                return redirect(url_for('.index'))

            moved_count = len(playlist.audio_crcs)
            for crc in playlist.audio_crcs:
                user_metadata.add_to_playlist(crc, default_playlist)
            del user_metadata.playlists[playlist_name]
    except Exception as error:
        log_event(
            "tubio",
            "tubio.playlist_delete_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        flash('Error deleting playlist.', 'error')
        return redirect(url_for('.index'))

    log_event("tubio", "tubio.playlist_deleted", tracks_moved=moved_count)
    flash(f'Playlist "{playlist_name}" deleted successfully!', 'success')
    return redirect(url_for('.index'))
