import logging
import math
import secrets

from datetime import datetime, timezone
from pathlib import Path
from flask import Response, flash, redirect, render_template, request, send_file, url_for

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.tubio.data_interface import AudioMetadata, DataInterface
from web_app.tubio.routes.playlists import get_playlists_data
from web_app.tubio.routes.surprise import _surprise_is_expired


def _library_html(data: DataInterface) -> str:
    return render_template(
        'playlists.html',
        playlists=get_playlists_data(cur_user(), data=data),
    )


def _redownload_audio(data: DataInterface, audio: AudioMetadata) -> None:
    file_path = data.get_audio_path(audio.crc)
    if file_path.exists():
        log_event(
            "tubio",
            "tubio.cache_metadata_repaired",
            level=logging.WARNING,
            crc=audio.crc,
        )
        audio.is_cached = True
        data.upsert_audio_metadata(audio)
        return
    if not audio.yt_video_id:
        log_event(
            "tubio",
            "tubio.redownload_rejected",
            level=logging.ERROR,
            crc=audio.crc,
            reason="missing_video_id",
        )
        raise ValueError("No YouTube video ID associated with this audio.")

    log_event(
        "tubio",
        "tubio.redownload_started",
        crc=audio.crc,
        video_id=audio.yt_video_id,
    )
    AudioDownloader.cache_youtube_audio(audio)
    log_event(
        "tubio",
        "tubio.redownload_completed",
        crc=audio.crc,
        video_id=audio.yt_video_id,
    )


@tubio_api.route('/upload', methods=['POST'])
@limiter.limit(lambda: ConfigManager().tubio.upload_rate_limit)
def upload_audio():
    uploaded_file = request.files.get('audio_file')
    if uploaded_file is None or not uploaded_file.filename:
        reason = "no_file" if uploaded_file is None else "empty_filename"
        log_event(
            "tubio",
            "tubio.upload_rejected",
            level=logging.WARNING,
            reason=reason,
        )
        flash('No file selected.', 'error')
        return redirect(url_for('.index'))

    file_ext = Path(uploaded_file.filename).suffix.lower()[1:]
    allowed_extensions = set(ConfigManager().tubio.upload_allowed_extensions)
    if file_ext not in allowed_extensions:
        log_event(
            "tubio",
            "tubio.upload_rejected",
            level=logging.WARNING,
            reason="unsupported_format",
            file_ext=file_ext,
        )
        flash(
            f"Unsupported file format: {file_ext}. Allowed: {', '.join(allowed_extensions)}",
            "error",
        )
        return redirect(url_for('.index'))

    title = request.form.get('audio_title', '').strip() or Path(
        uploaded_file.filename
    ).stem
    try:
        data = DataInterface()
        crc = data.save_audio(title, uploaded_file.read(), file_ext)
        with data.edit_metadata() as metadata:
            metadata.get_user(cur_user().id).add_to_playlist(crc)
    except Exception as error:
        log_event(
            "tubio",
            "tubio.upload_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        flash('Error uploading audio. Please try again.', 'error')
        return redirect(url_for('.index'))

    log_event("tubio", "tubio.upload_completed", crc=crc, file_ext=file_ext)
    flash(f'Successfully uploaded: {title}', 'success')
    return redirect(url_for('.index'))


@tubio_api.route('/audio/<int:crc>')
@limiter.limit(lambda: ConfigManager().tubio.audio_serve_rate_limit)
def serve_audio(crc: int):
    data = DataInterface()
    try:
        audio = data.get_audio_metadata(crc=crc)
    except ValueError as error:
        log_event(
            "tubio",
            "tubio.audio_serve_failed",
            level=logging.WARNING,
            crc=crc,
            reason="not_found",
            exc_info=error,
        )
        return {'error': 'Audio not found'}, 404

    if not audio.is_cached:
        _redownload_audio(data, audio)
    return _range_response(
        data.get_audio_path(crc),
        etag=str(crc),
        download_name=f"{crc}.m4a",
    )


def _range_response(file_path: Path, etag: str, download_name: str) -> Response:
    file_size = file_path.stat().st_size
    log_event(
        "tubio",
        "tubio.audio_served",
        path=file_path.name,
        bytes=file_size,
        range_requested=request.headers.get("Range") is not None,
    )
    response = send_file(
        file_path,
        mimetype="audio/mp4",
        as_attachment=False,
        download_name=download_name,
        conditional=True,
        etag=etag,
    )
    response.headers["Accept-Ranges"] = "bytes"
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True
    return response


@tubio_api.route('/audio/<int:crc>/download')
def download_audio(crc: int):
    data = DataInterface()
    try:
        audio = data.get_audio_metadata(crc=crc)
    except ValueError:
        flash(f'Error: no such audio: {crc}', 'error')
        return redirect(url_for('.index'))
    if not audio.is_cached:
        _redownload_audio(data, audio)

    safe_title = "".join(
        char for char in audio.title if char.isalnum() or char in " _-"
    ).strip() or str(crc)
    return send_file(
        data.get_audio_path(crc),
        mimetype='audio/mp4',
        as_attachment=True,
        download_name=f"{safe_title}.m4a",
    )


@tubio_api.route('/audio/<int:crc>/trim', methods=['POST'])
def trim_audio(crc: int):
    try:
        trim_start_s = float(request.form.get('trim_start_s', 0))
        trim_end_s = float(request.form.get('trim_end_s', 0))
    except (TypeError, ValueError):
        log_event(
            "tubio", "tubio.trim_rejected", level=logging.WARNING,
            crc=crc, reason="invalid_values",
        )
        return {'error': 'Trim values must be valid numbers'}, 400
    if not math.isfinite(trim_start_s) or not math.isfinite(trim_end_s):
        log_event(
            "tubio", "tubio.trim_rejected", level=logging.WARNING,
            crc=crc, reason="non_finite_values",
        )
        return {'error': 'Trim values must be finite numbers'}, 400
    if trim_start_s < 0 or trim_end_s < 0:
        log_event(
            "tubio", "tubio.trim_rejected", level=logging.WARNING,
            crc=crc, reason="negative_values",
        )
        return {'error': 'Trim values cannot be negative'}, 400

    data = DataInterface()
    try:
        with data.edit_metadata() as metadata:
            audio = metadata.audios.get(crc)
            if audio is None:
                log_event(
                    "tubio", "tubio.trim_rejected", level=logging.WARNING,
                    crc=crc, reason="audio_not_found",
                )
                return {'error': 'Audio not found'}, 404
            user_metadata = metadata.users.get(cur_user().id)
            if user_metadata is None or not any(
                crc in playlist.audio_crcs
                for playlist in user_metadata.playlists.values()
            ):
                log_event(
                    "tubio", "tubio.trim_rejected", level=logging.WARNING,
                    crc=crc, reason="not_owned",
                )
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
        'message': f'Updated playback range: {audio.title}',
        'library_html': _library_html(data),
    }


@tubio_api.route('/thumbnail/<int:crc>')
def serve_thumbnail(crc: int):
    thumbnail_path = DataInterface().get_thumbnail_path(crc)
    if not thumbnail_path.exists():
        return '', 404
    response = send_file(thumbnail_path, mimetype='image/jpeg')
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True
    response.set_etag(str(crc))
    return response


@tubio_api.route('/resync/<int:crc>', methods=['POST'])
@limiter.limit(lambda: ConfigManager().tubio.resync_rate_limit)
def resync_audio(crc: int):
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )
    if not is_ajax:
        log_event(
            "tubio", "tubio.resync_rejected", level=logging.WARNING,
            crc=crc, reason="non_ajax",
        )
        flash("Invalid request.", 'error')
        return redirect(url_for('.index'))

    data = DataInterface()
    try:
        audio = data.get_audio_metadata(crc=crc)
    except ValueError:
        log_event(
            "tubio", "tubio.resync_rejected", level=logging.WARNING,
            crc=crc, reason="audio_not_found",
        )
        return {'error': 'Audio not found'}, 404
    if not audio.yt_video_id:
        log_event(
            "tubio", "tubio.resync_rejected", level=logging.WARNING,
            crc=crc, reason="not_youtube",
        )
        return {'error': 'Track was not converted from YouTube'}, 400

    try:
        file_path = data.get_audio_path(crc)
        if file_path.exists():
            file_path.unlink()
        audio.is_cached = False
        data.upsert_audio_metadata(audio)
        AudioDownloader.download_youtube_audio(
            audio.yt_video_id,
            audio.title,
            cur_user(),
            crc=audio.crc,
        )
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

    log_event("tubio", "tubio.resync_completed", crc=crc)
    return {
        'success': True,
        'message': f'Resynced: {audio.title}',
        'library_html': _library_html(data),
    }


@tubio_api.route('/delete_audio/<int:crc>', methods=['POST'])
def delete_audio(crc: int):
    data = DataInterface()
    try:
        with data.edit_metadata() as metadata:
            user_metadata = metadata.users.get(cur_user().id)
            regular_playlists = (
                user_metadata.get_playlists()
                if user_metadata is not None
                else []
            )
            if not any(crc in playlist.audio_crcs for playlist in regular_playlists):
                log_event(
                    "tubio",
                    "tubio.audio_delete_rejected",
                    level=logging.WARNING,
                    crc=crc,
                    reason="not_owned",
                )
                return {'error': 'Audio not found in your playlists'}, 404

            user_metadata.remove_from_regular_playlists(crc)
            user_metadata.playback_trims.pop(crc, None)
            resource_retained = any(
                crc in playlist.audio_crcs
                for other_user in metadata.users.values()
                for playlist in other_user.playlists.values()
            )
        if not resource_retained:
            data.cleanup_unused_resources()
    except Exception as error:
        log_event(
            "tubio",
            "tubio.audio_delete_failed",
            level=logging.ERROR,
            crc=crc,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {'error': 'Error deleting audio'}, 500

    log_event(
        "tubio",
        "tubio.audio_deleted",
        crc=crc,
        resource_retained=resource_retained,
    )
    return {'success': True, 'library_html': _library_html(data)}


@tubio_api.route("/audio/<int:crc>/cache", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def cache_audio(crc: int):
    data = DataInterface()
    with data.edit_metadata() as metadata:
        user_metadata = metadata.users.get(cur_user().id)
        if user_metadata is None:
            surprise = None
            can_access = False
        else:
            surprise = user_metadata.get_surprise_playlist()
            if surprise is not None and _surprise_is_expired(surprise):
                surprise = None
            can_access = any(
                crc in playlist.audio_crcs
                for playlist in user_metadata.get_playlists()
            )
        if surprise is not None and crc in surprise.audio_crcs:
            surprise.last_active = datetime.now(timezone.utc)
            can_access = True
        audio = metadata.audios.get(crc)
        if audio is not None:
            audio = audio.model_copy(deep=True)

    if not can_access:
        log_event(
            "tubio", "tubio.cache_rejected", level=logging.WARNING,
            crc=crc, reason="access_denied",
        )
        return {"error": "Audio not found"}, 404
    if audio is None:
        log_event(
            "tubio", "tubio.cache_rejected", level=logging.WARNING,
            crc=crc, reason="metadata_not_found",
        )
        return {"error": "Audio metadata not found"}, 404

    file_path = data.app_audio_dir / f"{crc}.m4a"
    if audio.is_cached and file_path.exists():
        log_event(
            "tubio", "tubio.cache_completed", crc=crc,
            video_id=audio.yt_video_id, source="cache_hit",
        )
        return {"success": True, "is_cached": True, "video_id": audio.yt_video_id}

    cfg = ConfigManager().tubio
    key = cfg.surprise_cache_redis_prefix + str(crc)
    token = secrets.token_urlsafe(cfg.surprise_cache_claim_token_bytes).encode()
    redis = get_redis()
    if not redis.set(key, token, nx=True, ex=cfg.surprise_cache_claim_ttl_s):
        log_event(
            "tubio", "tubio.cache_in_progress",
            crc=crc, video_id=audio.yt_video_id,
        )
        return {
            "success": False,
            "is_cached": False,
            "status": "in_progress",
            "video_id": audio.yt_video_id,
        }, 202

    try:
        log_event(
            "tubio", "tubio.cache_materialization_started",
            crc=crc, video_id=audio.yt_video_id,
        )
        AudioDownloader.cache_youtube_audio(audio)
    except Exception as error:
        log_event(
            "tubio", "tubio.cache_failed", level=logging.ERROR,
            crc=crc, exc_info=error, error_type=type(error).__name__,
        )
        return {"error": "Could not convert this track"}, 500
    finally:
        if redis.get(key) == token:
            redis.delete(key)

    log_event(
        "tubio", "tubio.cache_completed", crc=crc,
        video_id=audio.yt_video_id, source="materialized",
    )
    return {"success": True, "is_cached": True, "video_id": audio.yt_video_id}
