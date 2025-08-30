import logging

from typing import *
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash, Response
from flask_login import login_required

from web_app.tubio.data_interface import DataInterface
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.config import ConfigManager
from web_app.helpers import cur_user, parse_request
from web_app.users import User
from web_app.helpers import limiter


tubio_api = Blueprint(
    'tubio',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/tubio'
)

@tubio_api.context_processor
def inject_app_name():
    return dict(app_name='Tubio')

def get_cached_yt_vid_ids(user: User|None = None) -> Set[str]:
    metadata = DataInterface().get_metadata()
    if user is None:
        return {audio.yt_video_id for audio in metadata.audios.values()}
    else:
        user_metadata = DataInterface().get_user_metadata(user)
        return {metadata.audios[crc].yt_video_id for crc in user_metadata.get_playlist().audio_crcs}

def get_playlists_data(user: User) -> list[tuple[str, list[tuple[int, str]]]]:
    user_metadata = DataInterface().get_user_metadata(user)
    playlists = []
    metadata = DataInterface().get_metadata()
    for playlist in user_metadata.get_playlists():
        playlist_data = []
        for crc in playlist.audio_crcs:
            if crc in metadata.audios:
                title = metadata.audios[crc].title
                playlist_data.append((crc, title))
        playlists.append((playlist.name, playlist_data))
    
    return playlists

@tubio_api.route('/')
@login_required
def index():
    return render_template("index.html", playlists=get_playlists_data(cur_user()))

@tubio_api.route('/search', methods=['GET', 'POST'])
@login_required
def search():
    results = []
    query = ''
    if request.method == 'POST':
        query = request.form.get('youtube_query', '')
        if not query:
            flash("No search query provided.", 'error')
            return redirect(url_for('.index') + '#search')

        try:
            decorated_query = f"{ConfigManager().tudio_search_prefix}{query}"
            user_favourites = get_cached_yt_vid_ids(cur_user())
            results = AudioDownloader.search_youtube(decorated_query, user_favourites)
            # assume AJAX POST request
            return {'results': results, 'query': query}
        
        except Exception:
            logging.exception("Error searching YouTube")
            flash("Error: Search Failed!", 'error')
            redirect(url_for('.index') + '#search')
    
    return redirect(url_for('.index') + '#search')

@tubio_api.route('/youtube_download', methods=['POST'])
@login_required
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
        user_metadata = DataInterface().get_user_metadata(cur_user())
        user_metadata.add_to_playlist(existing_audio_metadata.crc)
        DataInterface().save_user_metadata(cur_user(), user_metadata)
        
        return {
            'success': True,
            'message': f'Added {existing_audio_metadata.title} to playlist',
            'playlists': get_playlists_data(cur_user())
        }
        
    try:
        AudioDownloader.download_youtube_audio(video_id, title, cur_user())
        return {
            'success': True,
            'message': f'Audio downloaded for: {title}',
            'playlists': get_playlists_data(cur_user())
        }
    except Exception:
        logging.exception("Error downloading audio")
        return {'error': 'Error downloading audio'}, 500

@tubio_api.route('/audio/<int:crc>')
@limiter.limit("100 per second") # TODO: only 1 should be loaded at a time temporary fix
@login_required
def serve_audio(crc: int):
    try:
        file_path = DataInterface().get_audio_path(crc)
    except ValueError:
        flash(f'Error: no such audio file: {crc: int}', 'error')
        logging.exception("Error serving audio file")
        return redirect(url_for('.index'))
    
    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range", None)
    logging.info(f"Serving audio file {file_path} with size {file_size} bytes, Range header: {range_header}")

    if not range_header:
        # only support range requests
        raise ValueError("Range header not provided, only range requests are supported")

    # Example: "Range: bytes=12345-"
    range_header = range_header.strip()[len("bytes="):]
    splitted = range_header.split("-")
    if len(splitted) > 2:
        logging.error(f"Invalid Range header format: {range_header}")
        raise ValueError("Invalid Range header format")
    if range_header[0] == '-':
        # Example: "Range: bytes=-12345"
        byte1 = 0
        byte2 = file_size
    elif range_header[-1] == '-':
        # Example: "Range: bytes=12345-"
        byte1 = int(splitted[0])
        byte2 = file_size
    else:
        # Example: "Range: bytes=12345-67890"
        byte1 = int(splitted[0])
        byte2 = int(splitted[1]) + 1

    length = byte2 - byte1
    with open(file_path, "rb") as f:
        f.seek(byte1)
        data = f.read(length)

    response = Response(
        data,
        206,
        mimetype="audio/mp4",
        content_type="audio/mp4",
        direct_passthrough=True,
        headers={
            "Content-Type": "audio/mp4",
            "Content-Range": f"bytes {byte1}-{byte2-1}/{file_size}",
            "Accept-Ranges": "bytes",
        }
    )
    response.headers.set("Content-Length", str(length))

    return response

@tubio_api.route('/delete_audio/<int:crc>', methods=['POST'])
@login_required
def delete_audio(crc: int):
    try:
        user = cur_user()
        user_metadata = DataInterface().get_user_metadata(user)
        
        # Check if user has this audio in their playlists
        if crc not in user_metadata.get_playlist().audio_crcs:
            flash('Audio not found in your playlists.', 'error')
            return redirect(url_for('.index'))
        
        # Remove from user's playlists
        user_metadata.remove_from_playlist(crc)
        DataInterface().save_user_metadata(user, user_metadata)
        
        # Check if any other users have this audio in their playlists
        metadata = DataInterface().get_metadata()
        other_users_have_audio = any(
            crc in user_metadata.get_playlist().audio_crcs 
            for user_metadata in metadata.users.values() 
        )
        
        # If no other users have this audio, delete it completely
        if not other_users_have_audio:
            DataInterface().delete_audio(crc)
            flash('Audio deleted successfully.', 'success')
        else:
            flash('Audio removed from your playlists.', 'info')
            
    except Exception as e:
        logging.exception("Error deleting audio")
        flash('Error deleting audio.', 'error')
    
    return redirect(url_for('.index'))
