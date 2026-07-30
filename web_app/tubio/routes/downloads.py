import logging
import time
import web_app.tubio as tubio_facade

from flask import flash, redirect, request, url_for

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter, parse_request
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import (
    AudioDownloader,
    VideoTooLongError,
    clear_download_progress,
    get_download_progress,
)
from web_app.tubio.data_interface import DataInterface

@tubio_api.route('/youtube_download', methods=['POST'])
def youtube_download():
    req = parse_request(require_login=False, require_admin=False)
    video_id = req.get('video_id')
    title = req.get('title')

    is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
              'application/json' in request.headers.get('Accept', ''))
    if not is_ajax:
        log_event(
            "tubio",
            "tubio.download_rejected",
            level=logging.WARNING,
            reason="non_ajax",
            video_id=video_id,
        )
        flash("Invalid request.", 'error')
        return redirect(url_for('.index') + '#playlists')

    # for rest of function assume we are dealing with AJAX request

    if not video_id or not title:
        log_event(
            "tubio",
            "tubio.download_rejected",
            level=logging.WARNING,
            reason="missing_fields",
            video_id=video_id,
        )
        return {'error': 'No video ID or title provided'}, 400

    if video_id in get_cached_yt_vid_ids(cur_user()):
        log_event(
            "tubio",
            "tubio.download_rejected",
            level=logging.INFO,
            reason="already_in_playlist",
            video_id=video_id,
        )
        return {'error': 'Already in playlist', 'type': 'info'}, 400

    if video_id in get_cached_yt_vid_ids():
        # check if audio is already downloaded on the server but not in user's playlists
        existing_audio_metadata = tubio_facade.DataInterface().get_audio_metadata(yt_video_id=video_id)
        with tubio_facade.DataInterface().edit_metadata() as metadata:
            metadata.get_user(cur_user().id).add_to_playlist(existing_audio_metadata.crc)

        log_event(
            "tubio",
            "tubio.download_completed",
            video_id=video_id,
            crc=existing_audio_metadata.crc,
            source="existing_cache",
        )
        return {
            'success': True,
            'message': f'Added {existing_audio_metadata.title} to playlist',
            'playlists': get_playlists_data(cur_user())
        }

    try:
        audio = tubio_facade.AudioDownloader.download_youtube_audio(video_id, title, cur_user())
        log_event(
            "tubio",
            "tubio.download_completed",
            video_id=video_id,
            crc=getattr(audio, "crc", None),
            source="download",
        )
        return {
            'success': True,
            'message': f'Audio converted for: {title}',
            'playlists': get_playlists_data(cur_user())
        }
    except Exception as error:
        log_event(
            "tubio",
            "tubio.download_failed",
            level=logging.ERROR,
            video_id=video_id,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {'error': 'Error converting audio'}, 500


@tubio_api.route('/download_progress/<video_id>')
def download_progress(video_id: str):
    def generate():
        while True:
            progress = get_download_progress(video_id)
            if progress is None:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                break

            data = {
                'status': progress.status,
                'percent': round(progress.percent, 1),
                'error': progress.error
            }
            yield f"data: {json.dumps(data)}\n\n"

            if progress.status in ('complete', 'error'):
                clear_download_progress(video_id)
                break

            time.sleep(ConfigManager().tubio.download_progress_poll_interval_s)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
