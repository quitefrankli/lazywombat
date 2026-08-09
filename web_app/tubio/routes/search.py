import logging

from flask import redirect, render_template, request, url_for

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter
from web_app.logging_utils import log_event
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError
from web_app.tubio.routes.playlists import (
    get_cached_yt_vid_ids,
    get_playlists_data,
)


@tubio_api.route('/')
def index():
    return render_template("index.html", playlists=get_playlists_data(cur_user()))


@tubio_api.route('/search', methods=['GET', 'POST'])
def search():
    if request.method != 'POST':
        return redirect(url_for('.index') + '#search')

    query = request.form.get('youtube_query', '').strip()
    if not query:
        log_event(
            "tubio",
            "tubio.search_rejected",
            level=logging.WARNING,
            reason="empty_query",
        )
        return {'error': 'No search query provided'}, 400

    try:
        page = max(0, int(request.form.get('page', 0)))
    except (TypeError, ValueError):
        page = 0

    try:
        search_data = AudioDownloader.search_youtube(
            f"{ConfigManager().tubio.search_prefix}{query}",
            get_cached_yt_vid_ids(cur_user()),
            page=page,
        )
    except VideoTooLongError as error:
        max_minutes = int(error.max_duration.total_seconds() // 60)
        log_event(
            "tubio",
            "tubio.search_rejected",
            level=logging.WARNING,
            reason="video_too_long",
            max_minutes=max_minutes,
        )
        return {
            'error': f'Video exceeds maximum length of {max_minutes} minutes',
        }, 400
    except Exception as error:
        log_event(
            "tubio",
            "tubio.search_failed",
            level=logging.ERROR,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return {'error': 'Search failed'}, 500

    log_event(
        "tubio",
        "tubio.search_completed",
        page=search_data["page"],
        results=len(search_data["results"]),
        filtered_too_long=search_data.get("filtered_too_long", 0),
    )
    return {
        'results_html': render_template(
            'search_results.html',
            results=search_data['results'],
            page=search_data['page'],
            total_pages=search_data['total_pages'],
            filtered_too_long=search_data.get('filtered_too_long', 0),
            max_video_length_minutes=search_data.get(
                'max_video_length_minutes',
                0,
            ),
        ),
        'page': search_data['page'],
        'total_pages': search_data['total_pages'],
    }


@tubio_api.route('/suggest', methods=['POST'])
def suggest():
    query = request.form.get('youtube_query', '').strip()
    if len(query) < ConfigManager().tubio.autocomplete_min_query_len:
        return {'suggestions': []}
    suggestions = AudioDownloader.suggest_queries(query)
    log_event("tubio", "tubio.suggestions_completed", suggestions=len(suggestions))
    return {'suggestions': suggestions}


@tubio_api.route("/client-log", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.client_log_rate_limit)
def client_log():
    config = ConfigManager().tubio
    max_length = config.client_log_max_length
    requested_scope = request.form.get("scope", "")
    scope = requested_scope if requested_scope in config.client_log_scopes else "unknown"
    message = request.form.get("message", "")[:max_length]
    stack = request.form.get("stack", "")[:max_length]
    context = request.form.get("context", "")[:max_length]
    log_event(
        "tubio",
        "tubio.client_error",
        level=logging.ERROR,
        scope=scope,
        message_length=len(message),
        context_present=bool(context),
        stack_present=bool(stack),
    )
    return {"success": True}
