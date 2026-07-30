import logging
import json
import math
import time
import random
import secrets

from typing import *
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash, Response
from flask_login import login_required

from web_app.tubio.data_interface import DataInterface, AudioMetadata, Playlist
from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError, get_download_progress, clear_download_progress
from web_app.config import ConfigManager
from web_app.helpers import cur_user, parse_request, require_login_blueprint
from web_app.users import User
from web_app.helpers import limiter
from web_app.redis_client import get_redis
from web_app.tubio.surprise import reserve_audio_metadata


tubio_api = Blueprint(
    'tubio',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/tubio'
)


require_login_blueprint(tubio_api)


@tubio_api.context_processor
def inject_app_name():
    cfg = ConfigManager().tubio
    return dict(
        app_name='Tubio',
        tubio_player=dict(
            volume_min_percent=cfg.trackbar_volume_min_percent,
            volume_max_percent=cfg.trackbar_volume_max_percent,
            volume_step_percent=cfg.trackbar_volume_step_percent,
            default_volume_percent=cfg.trackbar_default_volume_percent,
            volume_storage_key=cfg.trackbar_volume_storage_key,
            muted_storage_key=cfg.trackbar_muted_storage_key,
        ),
        tubio_autocomplete=dict(
            debounce_ms=cfg.autocomplete_debounce_ms,
            min_query_len=cfg.autocomplete_min_query_len,
        ),
        tubio_surprise=dict(
            buffer_size=cfg.surprise_buffer_size,
            cache_poll_interval_ms=cfg.surprise_cache_poll_interval_ms,
        ),
    )

def get_cached_yt_vid_ids(user: User|None = None) -> Set[str]:
    metadata = DataInterface().get_metadata()
    if user is None:
        return {audio.yt_video_id for audio in metadata.audios.values()}
    else:
        user_metadata = DataInterface().get_user_metadata(user)
        return {metadata.audios[crc].yt_video_id for crc in user_metadata.get_playlist().audio_crcs}

def _playlist_track_data(
    audio: AudioMetadata,
    user_metadata,
    *,
    is_favourite: bool = False,
) -> dict:
    playback_trim = user_metadata.get_playback_trim(audio.crc)
    has_thumbnail = DataInterface().has_thumbnail(audio.crc)
    thumbnail_url = (
        url_for(".serve_thumbnail", crc=audio.crc)
        if has_thumbnail
        else (
            f"https://i.ytimg.com/vi/{audio.yt_video_id}/mqdefault.jpg"
            if audio.yt_video_id
            else ""
        )
    )
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


def _add_track_occurrences(tracks: list[dict]) -> list[dict]:
    """Give repeated CRCs stable identities within a rendered playlist."""
    occurrences: dict[int, int] = {}
    for track in tracks:
        crc = track["crc"]
        track["occurrence"] = occurrences.get(crc, 0)
        occurrences[crc] = track["occurrence"] + 1
    return tracks


def get_playlists_data(user: User) -> list[tuple[str, list[dict]]]:
    user_metadata = DataInterface().get_user_metadata(user)
    playlists = []
    metadata = DataInterface().get_metadata()
    for playlist in user_metadata.get_playlists():
        playlist_data = []
        for crc in reversed(playlist.audio_crcs):
            if crc in metadata.audios:
                audio = metadata.audios[crc]
                playlist_data.append(_playlist_track_data(audio, user_metadata))
        playlists.append((playlist.name, _add_track_occurrences(playlist_data)))

    return playlists

@tubio_api.route('/')
def index():
    return render_template("index.html", playlists=get_playlists_data(cur_user()))

@tubio_api.route('/search', methods=['GET', 'POST'])
def search():
    query = ''
    if request.method == 'POST':
        query = request.form.get('youtube_query', '')
        if not query:
            flash("No search query provided.", 'error')
            return redirect(url_for('.index') + '#search')

        try:
            page = int(request.form.get('page', 0))
        except ValueError:
            page = 0

        try:
            decorated_query = f"{ConfigManager().tubio.search_prefix}{query}"
            user_favourites = get_cached_yt_vid_ids(cur_user())
            search_data = AudioDownloader.search_youtube(decorated_query, user_favourites, page=page)
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
            return {'error': f'Video exceeds maximum length of {max_mins} minutes', 'query': query}, 400

        except Exception:
            logging.exception("Error searching YouTube")
            flash("Error: Search Failed!", 'error')
            redirect(url_for('.index') + '#search')

    return redirect(url_for('.index') + '#search')

@tubio_api.route('/suggest', methods=['POST'])
def suggest():
    query = request.form.get('youtube_query', '').strip()
    if len(query) < ConfigManager().tubio.autocomplete_min_query_len:
        return {'suggestions': []}
    return {'suggestions': AudioDownloader.suggest_queries(query)}


@tubio_api.route("/client-log", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.client_log_rate_limit)
def client_log():
    max_length = ConfigManager().tubio.client_log_max_length
    scope = request.form.get("scope", "unknown")[:max_length]
    message = request.form.get("message", "")[:max_length]
    stack = request.form.get("stack", "")[:max_length]
    context = request.form.get("context", "")[:max_length]
    logging.error(
        "Tubio client error user_id=%s scope=%s message=%r context=%r stack=%r",
        cur_user().id,
        scope,
        message,
        context,
        stack,
    )
    return {"success": True}


def _surprise_payload(playlist: Playlist) -> dict:
    payload = playlist.model_dump(mode="json")
    metadata = DataInterface().get_metadata()
    user_metadata = DataInterface().get_user_metadata(cur_user())
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
    logging.info(
        "Tubio Surprise payload user_id=%s tracks=%d missing_metadata=%d last_active=%s",
        cur_user().id,
        len(tracks),
        missing_count,
        playlist.last_active,
    )
    if missing_count:
        logging.warning(
            "Tubio Surprise payload omitted missing metadata user_id=%s missing=%d",
            cur_user().id,
            missing_count,
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
    with DataInterface().edit_metadata() as metadata:
        playlist = metadata.get_user(cur_user().id).get_surprise_playlist()
        if playlist is None or _surprise_is_expired(playlist, now):
            return None
        if touch:
            playlist.last_active = now
        return playlist.model_copy(deep=True)


def _pick_surprise_candidates(playlist: Playlist, count: int) -> list[dict]:
    cfg = ConfigManager().tubio
    owned_ids = {vid for vid in get_cached_yt_vid_ids(cur_user()) if vid}
    if not owned_ids:
        logging.info(
            "Tubio Surprise candidate selection empty library user_id=%s",
            cur_user().id,
        )
        return []
    metadata = DataInterface().get_metadata()
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
    seeds = [last_video_id] if last_video_id else []
    remaining = list(owned_ids - set(seeds))
    random.shuffle(remaining)
    seeds.extend(remaining)
    selected = []
    logging.info(
        "Tubio Surprise candidate selection started user_id=%s requested=%d owned=%d seen=%d seeds=%d",
        cur_user().id,
        count,
        len(owned_ids),
        len(seen_video_ids),
        len(seeds),
    )
    for seed in seeds:
        candidates = AudioDownloader.get_mix_related(seed)
        logging.info(
            "Tubio Surprise mix loaded user_id=%s seed=%s candidates=%d",
            cur_user().id,
            seed,
            len(candidates),
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
                logging.info(
                    "Tubio Surprise candidate selection completed user_id=%s selected=%d",
                    cur_user().id,
                    len(selected),
                )
                return selected
    logging.info(
        "Tubio Surprise candidate selection exhausted user_id=%s selected=%d requested=%d",
        cur_user().id,
        len(selected),
        count,
    )
    return selected


def _grow_surprise(playlist: Playlist, count: int | None = None):
    if count is None:
        count = ConfigManager().tubio.surprise_grow_batch_size
    logging.info(
        "Tubio Surprise grow started user_id=%s requested=%d existing=%d",
        cur_user().id,
        count,
        len(playlist.audio_crcs),
    )
    candidates = _pick_surprise_candidates(playlist, count)
    if not candidates:
        empty_reason = "no_library" if not get_cached_yt_vid_ids(cur_user()) else None
        logging.info(
            "Tubio Surprise grow exhausted user_id=%s empty_reason=%s",
            cur_user().id,
            empty_reason or "no_fresh_candidates",
        )
        return {"exhausted": True, "empty_reason": empty_reason}, 200

    try:
        with DataInterface().edit_metadata() as metadata:
            current = metadata.get_user(cur_user().id).get_surprise_playlist()
            if current is None or _surprise_is_expired(current):
                return {"error": "Surprise playlist not found"}, 404
            for candidate in candidates:
                crc = reserve_audio_metadata(metadata, candidate)
                if crc not in current.audio_crcs:
                    current.audio_crcs.append(crc)
            current.last_active = datetime.now(timezone.utc)
            playlist = current.model_copy(deep=True)
    except Exception:
        logging.exception(
            "Tubio Surprise grow failed user_id=%s",
            cur_user().id,
        )
        return {"error": "Surprise playlist changed; reload it and try again."}, 409
    logging.info(
        "Tubio Surprise grow completed user_id=%s added=%d total=%d",
        cur_user().id,
        len(candidates),
        len(playlist.audio_crcs),
    )
    return {"playlist": _surprise_payload(playlist)}, 200


@tubio_api.route("/surprise", methods=["GET"])
def get_surprise_playlist():
    playlist = _active_surprise(touch=True)
    logging.info(
        "Tubio Surprise restore user_id=%s found=%s",
        cur_user().id,
        playlist is not None,
    )
    return {"playlist": _surprise_payload(playlist) if playlist else None}


@tubio_api.route("/surprise", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def create_surprise_playlist():
    cfg = ConfigManager().tubio
    playlist = Playlist(
        name=cfg.surprise_playlist_name,
        last_active=datetime.now(timezone.utc),
    )
    logging.info(
        "Tubio Surprise create started user_id=%s initial_tracks=%d",
        cur_user().id,
        cfg.surprise_buffer_size,
    )
    data = DataInterface()
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
        )
        if not candidates:
            empty_reason = (
                "no_library"
                if not get_cached_yt_vid_ids(cur_user())
                else None
            )
            logging.warning(
                "Tubio Surprise create did not produce playlist user_id=%s empty_reason=%s",
                cur_user().id,
                empty_reason or "no_fresh_candidates",
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
        logging.info(
            "Tubio Surprise create completed user_id=%s tracks=%d",
            cur_user().id,
            len(playlist.audio_crcs),
        )
        return {"playlist": _surprise_payload(playlist)}, 200
    finally:
        try:
            data.cleanup_unused_resources()
        except Exception:
            logging.exception(
                "Tubio cleanup failed after Surprise generation user_id=%s",
                cur_user().id,
            )


@tubio_api.route("/surprise/grow", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def grow_surprise_playlist():
    playlist = _active_surprise(touch=True)
    if playlist is None:
        return {"error": "Surprise playlist not found"}, 404
    return _grow_surprise(playlist)


@tubio_api.route("/surprise/tracks/<int:crc>/favourite", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def favourite_surprise_track(crc: int):
    logging.info(
        "Tubio Surprise favourite requested user_id=%s crc=%d",
        cur_user().id,
        crc,
    )
    with DataInterface().edit_metadata() as metadata:
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
    logging.info(
        "Tubio Surprise favourite completed user_id=%s crc=%d",
        cur_user().id,
        crc,
    )
    return {"success": True, "crc": crc, "playlist": _surprise_payload(playlist)}


@tubio_api.route("/surprise/save", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def save_surprise_playlist():
    playlist = _active_surprise(touch=True)
    if playlist is None:
        return {"error": "Surprise playlist not found"}, 404
    playlist_name = request.form.get("playlist_name", "").strip()
    logging.info(
        "Tubio Surprise save requested user_id=%s name=%r tracks=%d",
        cur_user().id,
        playlist_name,
        len(playlist.audio_crcs),
    )
    if not playlist_name:
        return {"error": "Playlist name cannot be empty"}, 400
    with DataInterface().edit_metadata() as metadata:
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
    logging.info(
        "Tubio Surprise save completed user_id=%s name=%r tracks=%d",
        cur_user().id,
        playlist_name,
        saved_count,
    )
    return {
        "success": True,
        "message": f'Saved playlist "{playlist_name}"',
        "playlist_name": playlist_name,
        "saved_count": saved_count,
        "skipped": [],
        "playlists": get_playlists_data(cur_user()),
    }


@tubio_api.route('/youtube_download', methods=['POST'])
def youtube_download():
    req = parse_request(require_login=False, require_admin=False)
    video_id = req.get('video_id')
    title = req.get('title')
    
    is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 
              'application/json' in request.headers.get('Accept', ''))
    if not is_ajax:
        logging.error("Non-AJAX request to /youtube_download")
        flash("Invalid request.", 'error')
        return redirect(url_for('.index') + '#playlists')

    # for rest of function assume we are dealing with AJAX request
    
    if not video_id or not title:
        return {'error': 'No video ID or title provided'}, 400

    if video_id in get_cached_yt_vid_ids(cur_user()):
        return {'error': 'Already in playlist', 'type': 'info'}, 400

    if video_id in get_cached_yt_vid_ids():
        # check if audio is already downloaded on the server but not in user's playlists
        existing_audio_metadata = DataInterface().get_audio_metadata(yt_video_id=video_id)
        with DataInterface().edit_metadata() as metadata:
            metadata.get_user(cur_user().id).add_to_playlist(existing_audio_metadata.crc)

        return {
            'success': True,
            'message': f'Added {existing_audio_metadata.title} to playlist',
            'playlists': get_playlists_data(cur_user())
        }
        
    try:
        AudioDownloader.download_youtube_audio(video_id, title, cur_user())
        return {
            'success': True,
            'message': f'Audio converted for: {title}',
            'playlists': get_playlists_data(cur_user())
        }
    except Exception:
        logging.exception("Error downloading audio")
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


@tubio_api.route('/upload', methods=['POST'])
@limiter.limit("20 per minute")
def upload_audio():
    try:
        # Check if file is in the request
        if 'audio_file' not in request.files:
            flash('No file provided.', 'error')
            return redirect(url_for('.index'))
        
        file = request.files['audio_file']
        
        if file.filename == '':
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
            flash(f"Unsupported file format: {file_ext}. Allowed: {', '.join(allowed_extensions)}", "error")
            return redirect(url_for('.index'))
        
        crc = DataInterface().save_audio(title, file.read(), file_ext)
        audio_metadata = DataInterface().get_audio_metadata(crc=crc)
        with DataInterface().edit_metadata() as metadata:
            metadata.get_user(cur_user().id).add_to_playlist(audio_metadata.crc)
        
        flash(f'Successfully uploaded: {title}', 'success')
        
    except Exception as e:
        logging.exception("Error uploading audio", exc_info=e)
        flash('Error uploading audio. Please try again.', 'error')
    
    return redirect(url_for('.index'))

def redownload_audio(audio_metadata: AudioMetadata) -> None:
    # TODO: redownload might take sometime so it could be jarring for end user
    # make it more obvious what is going on in the background

    file_path = DataInterface().get_audio_path(audio_metadata.crc)

    if file_path.exists():
        logging.warning(f"Audio file {file_path} exists but metadata indicates not cached. Updating metadata.")
        audio_metadata.is_cached = True
        DataInterface().save_audio_metadata(audio_metadata)
        return
    
    if not audio_metadata.yt_video_id:
        logging.error(f"Cannot redownload audio with crc {audio_metadata.crc} as it has no associated YouTube video ID.")
        raise ValueError("No YouTube video ID associated with this audio.")

    logging.info(f"Redownloading audio for YT video ID: {audio_metadata.yt_video_id}")
    AudioDownloader.cache_youtube_audio(audio_metadata)
    
    logging.info(f"Redownloaded audio for YT video ID: {audio_metadata.yt_video_id}")


@tubio_api.route("/audio/<int:crc>/cache", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def cache_audio(crc: int):
    logging.info("Tubio audio cache requested user_id=%s crc=%d", cur_user().id, crc)
    data = DataInterface()
    with data.edit_metadata() as metadata:
        user_metadata = metadata.get_user(cur_user().id)
        surprise = user_metadata.get_surprise_playlist()
        if surprise is not None and _surprise_is_expired(surprise):
            surprise = None
        can_access = any(
            crc in playlist.audio_crcs
            for playlist in user_metadata.playlists.values()
            if playlist.last_active is None
        )
        if surprise is not None and crc in surprise.audio_crcs:
            surprise.last_active = datetime.now(timezone.utc)
            can_access = True
        audio = metadata.audios.get(crc)
        if audio is not None:
            audio = audio.model_copy(deep=True)
    if not can_access:
        logging.warning(
            "Tubio audio cache denied user_id=%s crc=%d",
            cur_user().id,
            crc,
        )
        return {"error": "Audio not found"}, 404
    if audio is None:
        return {"error": "Audio metadata not found"}, 404

    file_path = data.app_audio_dir / f"{crc}.m4a"
    if audio.is_cached and file_path.exists():
        logging.info(
            "Tubio audio cache hit user_id=%s crc=%d video_id=%s",
            cur_user().id,
            crc,
            audio.yt_video_id,
        )
        return {"success": True, "is_cached": True, "video_id": audio.yt_video_id}

    cfg = ConfigManager().tubio
    key = cfg.surprise_cache_redis_prefix + str(crc)
    token = secrets.token_urlsafe(cfg.surprise_cache_claim_token_bytes).encode()
    redis = get_redis()
    if not redis.set(key, token, nx=True, ex=cfg.surprise_cache_claim_ttl_s):
        logging.info(
            "Tubio audio cache already in progress user_id=%s crc=%d video_id=%s",
            cur_user().id,
            crc,
            audio.yt_video_id,
        )
        return {
            "success": False,
            "is_cached": False,
            "status": "in_progress",
            "video_id": audio.yt_video_id,
        }, 202

    try:
        logging.info(
            "Tubio audio cache materialization started user_id=%s crc=%d video_id=%s",
            cur_user().id,
            crc,
            audio.yt_video_id,
        )
        AudioDownloader.cache_youtube_audio(audio)
        logging.info(
            "Tubio audio cache materialization completed user_id=%s crc=%d video_id=%s",
            cur_user().id,
            crc,
            audio.yt_video_id,
        )
        return {"success": True, "is_cached": True, "video_id": audio.yt_video_id}
    except Exception:
        logging.exception("Could not cache audio %s", crc)
        return {"error": "Could not convert this track"}, 500
    finally:
        if redis.get(key) == token:
            redis.delete(key)

@limiter.limit("100 per second") # TODO: only 1 should be loaded at a time temporary fix
@tubio_api.route('/audio/<int:crc>')
def serve_audio(crc: int):
    try:
        metadata = DataInterface().get_audio_metadata(crc=crc)
    except ValueError:
        flash(f'Error: no such audio: {crc: int}', 'error')
        logging.exception("Error serving audio")
        return redirect(url_for('.index'))

    if not metadata.is_cached:
        redownload_audio(metadata)

    file_path = DataInterface().get_audio_path(crc)
    return _range_response(file_path, etag=str(crc), download_name=f"{crc}.m4a")


def _range_response(file_path: Path, etag: str, download_name: str) -> Response:
    """Serve an m4a file with Werkzeug's RFC-compliant conditional ranges."""
    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range", None)
    logging.info(
        "Serving audio %s (%d bytes), Range header: %s",
        file_path,
        file_size,
        range_header,
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
        metadata = DataInterface().get_audio_metadata(crc=crc)
    except ValueError:
        flash(f'Error: no such audio: {crc}', 'error')
        return redirect(url_for('.index'))

    if not metadata.is_cached:
        redownload_audio(metadata)

    file_path = DataInterface().get_audio_path(crc)
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
    data_interface = DataInterface()
    try:
        with data_interface.edit_metadata() as metadata:
            if crc not in metadata.audios:
                return {'error': 'Audio not found'}, 404
            audio_metadata = metadata.audios[crc]
            user_metadata = metadata.get_user(cur_user().id)
            if not any(crc in playlist.audio_crcs for playlist in user_metadata.playlists.values()):
                return {'error': 'Audio not found in your playlists'}, 404
            user_metadata.set_playback_trim(crc, trim_start_s, trim_end_s)
    except Exception:
        logging.exception("Error saving audio playback boundaries")
        return {'error': 'Could not save playback boundaries'}, 500

    return {
        'success': True,
        'trim_start_s': trim_start_s,
        'trim_end_s': trim_end_s,
        'message': f'Updated playback range: {audio_metadata.title}',
    }


@tubio_api.route('/thumbnail/<int:crc>')
def serve_thumbnail(crc: int):
    thumbnail_path = DataInterface().get_thumbnail_path(crc)
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
        metadata = DataInterface().get_audio_metadata(crc=crc)
    except ValueError:
        return {'error': 'Audio not found'}, 404

    if not metadata.yt_video_id:
        return {'error': 'Track was not converted from YouTube'}, 400

    try:
        # Delete existing file to force redownload
        file_path = DataInterface().get_audio_path(crc)
        if file_path.exists():
            file_path.unlink()
        metadata.is_cached = False
        DataInterface().save_audio_metadata(metadata)

        AudioDownloader.download_youtube_audio(
            metadata.yt_video_id, metadata.title, cur_user(), crc=metadata.crc
        )
        return {'success': True, 'message': f'Resynced: {metadata.title}'}
    except Exception:
        logging.exception("Error resyncing audio")
        return {'error': 'Error resyncing audio'}, 500


@tubio_api.route('/delete_audio/<int:crc>', methods=['POST'])
def delete_audio(crc: int):
    try:
        user = cur_user()
        data = DataInterface()
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
            
    except Exception as e:
        logging.exception("Error deleting audio")
        flash('Error deleting audio.', 'error')
    
    return redirect(url_for('.index'))

@tubio_api.route('/create_playlist', methods=['POST'])
@limiter.limit("10 per minute")
def create_playlist():
    try:
        playlist_name = request.form.get('playlist_name', '').strip()
        
        if not playlist_name:
            flash('Playlist name cannot be empty.', 'error')
            return redirect(url_for('.index'))
        
        user = cur_user()
        with DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            # Check if playlist already exists
            if playlist_name in user_metadata.playlists:
                flash(f'Playlist "{playlist_name}" already exists.', 'warning')
                return redirect(url_for('.index'))

            # Create new playlist
            user_metadata.get_playlist(playlist_name)

        flash(f'Playlist "{playlist_name}" created successfully!', 'success')
        
    except Exception as e:
        logging.exception("Error creating playlist")
        flash('Error creating playlist.', 'error')
    
    return redirect(url_for('.index'))

@tubio_api.route('/move_tracks_to_playlist', methods=['POST'])
@limiter.limit("20 per minute")
def move_tracks_to_playlist():
    try:
        target_playlist = request.form.get('target_playlist', '').strip()
        song_crcs_str = request.form.get('song_crcs', '')
        
        if not target_playlist:
            flash('Please select a target playlist.', 'error')
            return redirect(url_for('.index'))
        
        if not song_crcs_str:
            flash('No songs selected.', 'warning')
            return redirect(url_for('.index'))
        
        # Parse CRCs
        song_crcs = [int(crc) for crc in song_crcs_str.split(',') if crc.strip()]
        if not song_crcs:
            flash('No valid songs selected.', 'warning')
            return redirect(url_for('.index'))
        
        user = cur_user()
        with DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            for crc in song_crcs:
                user_metadata.remove_from_all_playlists(crc)
                user_metadata.add_to_playlist(crc, target_playlist)
        
    except Exception as e:
        logging.exception("Error moving songs to playlist", exc_info=e)
        flash('Error moving songs to playlist.', 'error')
    
    return redirect(url_for('.index'))


@tubio_api.route('/delete_selected_songs', methods=['POST'])
@limiter.limit("10 per minute")
def delete_selected_songs():
    try:
        song_crcs_str = request.form.get('song_crcs', '')
        
        if not song_crcs_str:
            flash('No songs selected.', 'warning')
            return redirect(url_for('.index'))
        
        song_crcs = [int(crc) for crc in song_crcs_str.split(',') if crc.strip()]
        user = cur_user()
        with DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            for crc in song_crcs:
                user_metadata.remove_from_all_playlists(crc)

        DataInterface().cleanup_unused_resources()
    except Exception as e:
        logging.exception("Error deleting selected songs", exc_info=e)
        flash('Error deleting songs.', 'error')
    
    return redirect(url_for('.index'))

@tubio_api.route('/delete_playlist', methods=['POST'])
@limiter.limit("10 per minute")
def delete_playlist():
    try:
        playlist_name = request.form.get('playlist_name', '').strip()
        
        if not playlist_name:
            flash('Playlist name cannot be empty.', 'error')
            return redirect(url_for('.index'))
        
        # Prevent deletion of Favourites playlist
        if playlist_name == ConfigManager().tubio.default_playlist_name:
            flash('Cannot delete the Favourites playlist.', 'error')
            return redirect(url_for('.index'))
        
        user = cur_user()
        with DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            # Check if playlist exists
            if playlist_name not in user_metadata.playlists:
                flash(f'Playlist "{playlist_name}" does not exist.', 'warning')
                return redirect(url_for('.index'))

            # Delete the playlist
            del user_metadata.playlists[playlist_name]

        DataInterface().cleanup_unused_resources()
        flash(f'Playlist "{playlist_name}" deleted successfully!', 'success')
        
    except Exception as e:
        logging.exception("Error deleting playlist")
        flash('Error deleting playlist.', 'error')
    
    return redirect(url_for('.index'))
