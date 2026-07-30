import logging
import web_app.tubio as tubio_facade

from flask import flash, redirect, render_template, request, url_for

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter, parse_request
from web_app.logging_utils import log_event
from web_app.tubio import tubio_api
from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError
from web_app.tubio.services.playlists import get_cached_yt_vid_ids, get_playlists_data
from web_app.tubio.services.search import search_youtube, suggest_queries

@tubio_api.route('/')
def index():
    return render_template("index.html", playlists=tubio_facade.get_playlists_data(cur_user()))

@tubio_api.route('/search', methods=['GET', 'POST'])
def search():
    query = ''
    if request.method == 'POST':
        query = request.form.get('youtube_query', '')
        if not query:
            log_event(
                "tubio",
                "tubio.search_rejected",
                level=logging.WARNING,
                reason="empty_query",
            )
            flash("No search query provided.", 'error')
            return redirect(url_for('.index') + '#search')

        try:
            page = int(request.form.get('page', 0))
        except ValueError:
            page = 0

        try:
            search_data = search_youtube(query, cur_user(), page)
            log_event(
                "tubio",
                "tubio.search_completed",
                page=search_data["page"],
                results=len(search_data["results"]),
                filtered_too_long=search_data.get("filtered_too_long", 0),
            )
            # assume AJAX POST request
            return {
                'results': search_data['results'],
                'page': search_data['page'],
                'total_pages': search_data['total_pages'],
                'filtered_too_long': search_data.get('filtered_too_long', 0),
                'max_video_length_minutes': search_data.get('max_video_length_minutes', 0),
                'query': query,
            }

        except VideoTooLongError as e:
            max_mins = int(e.max_duration.total_seconds() // 60)
            log_event(
                "tubio",
                "tubio.search_rejected",
                level=logging.WARNING,
                reason="video_too_long",
                max_minutes=max_mins,
            )
            return {'error': f'Video exceeds maximum length of {max_mins} minutes', 'query': query}, 400

        except Exception as error:
            log_event(
                "tubio",
                "tubio.search_failed",
                level=logging.ERROR,
                exc_info=error,
                error_type=type(error).__name__,
            )
            flash("Error: Search Failed!", 'error')
            redirect(url_for('.index') + '#search')

    return redirect(url_for('.index') + '#search')

@tubio_api.route('/suggest', methods=['POST'])
def suggest():
    query = request.form.get('youtube_query', '').strip()
    if len(query) < ConfigManager().tubio.autocomplete_min_query_len:
        return {'suggestions': []}
    suggestions = suggest_queries(query)
    log_event("tubio", "tubio.suggestions_completed", suggestions=len(suggestions))
    return {'suggestions': suggestions}


@tubio_api.route("/client-log", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.client_log_rate_limit)
def client_log():
    max_length = ConfigManager().tubio.client_log_max_length
    scope = request.form.get("scope", "unknown")[:max_length]
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
