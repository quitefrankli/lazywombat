import logging
import math
import time
import json
import web_app.tubio as tubio_facade
from pathlib import Path

from flask import Response, flash, redirect, request, send_file, url_for
from flask_login import login_required

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter, parse_request
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError
from web_app.tubio.data_interface import AudioMetadata, DataInterface, Playlist
from web_app.tubio.services.playlists import get_playlists_data
from web_app.tubio.services.surprise import _surprise_is_expired
from web_app.tubio.services.media import redownload_audio

@tubio_api.route('/upload', methods=['POST'])
@limiter.limit("20 per minute")
def upload_audio():
    try:
        # Check if file is in the request
        if 'audio_file' not in request.files:
            log_event(
                "tubio",
                "tubio.upload_rejected",
                level=logging.WARNING,
                reason="no_file",
            )
            flash('No file provided.', 'error')
            return redirect(url_for('.index'))

        file = request.files['audio_file']

        if file.filename == '':
            log_event(
                "tubio",
                "tubio.upload_rejected",
                level=logging.WARNING,
                reason="empty_filename",
            )
            flash('No file selected.', 'error')
            return redirect(url_for('.index'))

        # Get the custom title or use filename
        title = request.form.get('audio_title', '').strip()
        if not title:
            # Use filename without extension as title
            title = Path(file.filename).stem

        # Validate file extension
        allowed_extensions = set(ConfigManager().tubio.upload_allowed_extensions)
        file_ext = Path(file.filename).suffix.lower()[1:]

        if file_ext not in allowed_extensions:
            log_event(
                "tubio",
                "tubio.upload_rejected",
                level=logging.WARNING,
                reason="unsupported_format",
                file_ext=file_ext,
            )
            flash(f"Unsupported file format: {file_ext}. Allowed: {', '.join(allowed_extensions)}", "error")
            return redirect(url_for('.index'))

        crc = tubio_facade.DataInterface().save_audio(title, file.read(), file_ext)
        audio_metadata = tubio_facade.DataInterface().get_audio_metadata(crc=crc)
        with tubio_facade.DataInterface().edit_metadata() as metadata:
            metadata.get_user(cur_user().id).add_to_playlist(audio_metadata.crc)
        log_event(
            "tubio",
            "tubio.upload_completed",
            crc=crc,
            file_ext=file_ext,
        )
        flash(f'Successfully uploaded: {title}', 'success')

    except Exception as e:
        log_event(
            "tubio",
            "tubio.upload_failed",
            level=logging.ERROR,
            exc_info=e,
            error_type=type(e).__name__,
        )
        flash('Error uploading audio. Please try again.', 'error')

    return redirect(url_for('.index'))

@tubio_api.route('/audio/<int:crc>')
@limiter.limit("100 per second") # TODO: only 1 should be loaded at a time temporary fix
def serve_audio(crc: int):
    try:
        metadata = tubio_facade.DataInterface().get_audio_metadata(crc=crc)
    except ValueError as error:
        flash(f'Error: no such audio: {crc: int}', 'error')
        log_event(
            "tubio",
            "tubio.audio_serve_failed",
            level=logging.WARNING,
            crc=crc,
            reason="not_found",
            exc_info=error,
        )
        return redirect(url_for('.index'))

    if not metadata.is_cached:
        redownload_audio(metadata)

    file_path = tubio_facade.DataInterface().get_audio_path(crc)
    return _range_response(file_path, etag=str(crc), download_name=f"{crc}.m4a")


def _range_response(file_path: Path, etag: str, download_name: str) -> Response:
    """Serve an m4a file with Werkzeug's RFC-compliant conditional ranges."""
    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range", None)
    log_event(
        "tubio",
        "tubio.audio_served",
        path=file_path.name,
        bytes=file_size,
        range_requested=range_header is not None,
    )

    # conditional=True delegates parsing, clamping, If-Range handling, HEAD
    # requests, and 416 responses to Werkzeug instead of maintaining a second
    # partial implementation of HTTP byte ranges here.
    response = send_file(
        file_path,
        mimetype="audio/mp4",
        as_attachment=False,
        download_name=download_name,
        conditional=True,
        etag=etag,
    )
    response.headers["Accept-Ranges"] = "bytes"

    # Cache audio ranges
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True

    return response


@tubio_api.route('/audio/<int:crc>/download')
@login_required
def download_audio(crc: int):
    try:
        metadata = tubio_facade.DataInterface().get_audio_metadata(crc=crc)
    except ValueError:
        flash(f'Error: no such audio: {crc}', 'error')
        return redirect(url_for('.index'))

    if not metadata.is_cached:
        redownload_audio(metadata)

    file_path = tubio_facade.DataInterface().get_audio_path(crc)
    safe_title = "".join(c for c in metadata.title if c.isalnum() or c in " _-").strip() or str(crc)
    return send_file(file_path, mimetype='audio/mp4', as_attachment=True, download_name=f"{safe_title}.m4a")


@tubio_api.route('/audio/<int:crc>/trim', methods=['POST'])
def trim_audio(crc: int):
    try:
        trim_start_s = float(request.form.get('trim_start_s', 0))
        trim_end_s = float(request.form.get('trim_end_s', 0))
    except (TypeError, ValueError):
        return {'error': 'Trim values must be valid numbers'}, 400

    if not math.isfinite(trim_start_s) or not math.isfinite(trim_end_s):
        return {'error': 'Trim values must be finite numbers'}, 400
    if trim_start_s < 0 or trim_end_s < 0:
        return {'error': 'Trim values cannot be negative'}, 400
    data_interface = tubio_facade.DataInterface()
    try:
        with data_interface.edit_metadata() as metadata:
            if crc not in metadata.audios:
                return {'error': 'Audio not found'}, 404
            audio_metadata = metadata.audios[crc]
            user_metadata = metadata.get_user(cur_user().id)
            if not any(crc in playlist.audio_crcs for playlist in user_metadata.playlists.values()):
                return {'error': 'Audio not found in your playlists'}, 404
            user_metadata.set_playback_trim(crc, trim_start_s, trim_end_s)
    except Exception as error:
        log_event(
            "tubio",
            "tubio.trim_failed",
            level=logging.ERROR,
            crc=crc,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {'error': 'Could not save playback boundaries'}, 500

    log_event("tubio", "tubio.trim_completed", crc=crc)
    return {
        'success': True,
        'trim_start_s': trim_start_s,
        'trim_end_s': trim_end_s,
        'message': f'Updated playback range: {audio_metadata.title}',
    }


@tubio_api.route('/thumbnail/<int:crc>')
def serve_thumbnail(crc: int):
    thumbnail_path = tubio_facade.DataInterface().get_thumbnail_path(crc)
    if not thumbnail_path.exists():
        # Return a placeholder or 404
        return '', 404

    response = send_file(thumbnail_path, mimetype='image/jpeg')

    # Cache thumbnails
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True
    response.set_etag(str(crc))

    return response


@tubio_api.route('/resync/<int:crc>', methods=['POST'])
@limiter.limit("5 per minute")
def resync_audio(crc: int):
    is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
               'application/json' in request.headers.get('Accept', ''))
    if not is_ajax:
        flash("Invalid request.", 'error')
        return redirect(url_for('.index'))

    try:
        metadata = tubio_facade.DataInterface().get_audio_metadata(crc=crc)
    except ValueError:
        return {'error': 'Audio not found'}, 404

    if not metadata.yt_video_id:
        return {'error': 'Track was not converted from YouTube'}, 400

    try:
        # Delete existing file to force redownload
        file_path = tubio_facade.DataInterface().get_audio_path(crc)
        if file_path.exists():
            file_path.unlink()
        metadata.is_cached = False
        tubio_facade.DataInterface().upsert_audio_metadata(metadata)

        tubio_facade.AudioDownloader.download_youtube_audio(
            metadata.yt_video_id, metadata.title, cur_user(), crc=metadata.crc
        )
        log_event("tubio", "tubio.resync_completed", crc=crc)
        return {'success': True, 'message': f'Resynced: {metadata.title}'}
    except Exception as error:
        log_event(
            "tubio",
            "tubio.resync_failed",
            level=logging.ERROR,
            crc=crc,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {'error': 'Error resyncing audio'}, 500


@tubio_api.route('/delete_audio/<int:crc>', methods=['POST'])
def delete_audio(crc: int):
    try:
        user = cur_user()
        data = tubio_facade.DataInterface()
        with data.edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            # Check if user has this audio in their playlists
            if crc not in user_metadata.get_playlist().audio_crcs:
                flash('Audio not found in your playlists.', 'error')
                return redirect(url_for('.index'))

            # Remove from user's playlists
            user_metadata.remove_from_playlist(crc)

            # Preserve metadata while any durable or temporary playlist still
            # references the track.
            audio_is_still_used = any(
                crc in playlist.audio_crcs
                for other in metadata.users.values()
                for playlist in other.playlists.values()
            )

            if audio_is_still_used:
                flash('Audio removed from your playlists.', 'info')
            else:
                flash('Audio deleted successfully.', 'success')
        data.cleanup_unused_resources()
        log_event(
            "tubio",
            "tubio.audio_deleted",
            crc=crc,
            resource_retained=audio_is_still_used,
        )

    except Exception as e:
        log_event(
            "tubio",
            "tubio.audio_delete_failed",
            level=logging.ERROR,
            crc=crc,
            exc_info=e,
            error_type=type(e).__name__,
        )
        flash('Error deleting audio.', 'error')

    return redirect(url_for('.index'))
