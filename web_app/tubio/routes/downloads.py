import json
import logging
import time

from flask import Response, flash, redirect, render_template, request, url_for

from web_app.config import ConfigManager
from web_app.helpers import cur_user, parse_request
from web_app.logging_utils import log_event
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import (
    AudioDownloader,
    clear_download_progress,
    get_download_progress,
)
from web_app.tubio.data_interface import DataInterface
from web_app.tubio.routes.playlists import (
    get_cached_yt_vid_ids,
    get_playlists_data,
)


def _library_response(message: str) -> dict:
    return {
        'success': True,
        'message': message,
        'library_html': render_template(
            'playlists.html',
            playlists=get_playlists_data(cur_user()),
        ),
    }


@tubio_api.route('/youtube_download', methods=['POST'])
def youtube_download():
    values = parse_request(require_login=False, require_admin=False)
    video_id = values.get('video_id')
    title = values.get('title')
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )
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
    if not video_id or not title:
        log_event(
            "tubio",
            "tubio.download_rejected",
            level=logging.WARNING,
            reason="missing_fields",
            video_id=video_id,
        )
        return {'error': 'No video ID or title provided'}, 400

    user = cur_user()
    if video_id in get_cached_yt_vid_ids(user):
        log_event(
            "tubio",
            "tubio.download_rejected",
            level=logging.INFO,
            reason="already_in_library",
            video_id=video_id,
        )
        return {'error': 'Already in playlist', 'type': 'info'}, 400

    data = DataInterface()
    if video_id in get_cached_yt_vid_ids(data=data):
        existing = data.get_audio_metadata(yt_video_id=video_id)
        with data.edit_metadata() as metadata:
            metadata.get_user(user.id).add_to_playlist(existing.crc)
        log_event(
            "tubio",
            "tubio.download_completed",
            video_id=video_id,
            crc=existing.crc,
            source="existing_cache",
        )
        return _library_response(f'Added {existing.title} to playlist')

    try:
        audio = AudioDownloader.download_youtube_audio(video_id, title, user)
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

    log_event(
        "tubio",
        "tubio.download_completed",
        video_id=video_id,
        crc=getattr(audio, "crc", None),
        source="download",
    )
    return _library_response(f'Audio converted for: {title}')


@tubio_api.route('/download_progress/<video_id>')
def download_progress(video_id: str):
    def generate():
        while True:
            progress = get_download_progress(video_id)
            if progress is None:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                break

            yield f"data: {json.dumps({
                'status': progress.status,
                'percent': round(progress.percent, 1),
                'error': progress.error,
            })}\n\n"
            if progress.status in ('complete', 'error'):
                clear_download_progress(video_id)
                break
            time.sleep(ConfigManager().tubio.download_progress_poll_interval_s)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
