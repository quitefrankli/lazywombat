import logging

from typing import *
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required

from web_app.tubio.data_interface import DataInterface
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.config import ConfigManager
from web_app.helpers import cur_user, parse_request


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

def get_cached_yt_vid_ids() -> Set[str]:
    metadata = DataInterface().get_metadata()
    return {audio.yt_video_id for audio in metadata.audios.values()}

@tubio_api.route('/')
@login_required
def favourites():
    user_id = cur_user().id
    metadata = DataInterface().get_metadata()
    if user_id not in metadata.users:
        return render_template("favourites.html", favourites=[])
    
    user_data = metadata.users[user_id]
    crcs = user_data.favourites
    titles = [metadata.audios[crc].title for crc in crcs]

    return render_template("favourites.html", favourites=zip(crcs, titles))

@tubio_api.route('/search', methods=['GET', 'POST'])
@login_required
def search():
    results = []
    query = ''
    if request.method == 'POST':
        query = request.form.get('youtube_query', '')
        if query:
            try:
                decorated_query = f"{ConfigManager().tudio_search_prefix}{query}"
                results = AudioDownloader.search_youtube(decorated_query, get_cached_yt_vid_ids())
            except Exception as e:
                logging.error(f"Error searching YouTube: {e}")
                flash("Error: Search Failed!")

    return render_template('search.html', 
                           youtube_results=results, 
                           youtube_query=query)

@tubio_api.route('/youtube_download', methods=['POST'])
@login_required
def youtube_download():
    req = parse_request(require_login=False, require_admin=False)
    video_id = req.get('video_id')
    title = req.get('title')
    if not video_id or not title:
        flash('No video ID provided.', 'error')
        return redirect(url_for('.favourites'))
    if video_id in get_cached_yt_vid_ids():
        flash('Video already downloaded.', 'info')
        return redirect(url_for('.favourites'))

    try:
        AudioDownloader.download_youtube_audio(video_id, title, cur_user())
        flash(f'Audio downloaded for: {title}', 'success')
    except Exception as e:
        logging.error(f"Error downloading audio: {e}")
        flash("Error downloading audio")

    return redirect(url_for('.favourites'))

@tubio_api.route('/audio/<int:crc>')
@login_required
def serve_audio(crc: int):
    try:
        file_path = DataInterface().get_audio_path(crc)
    except ValueError as e:
        flash(f'Error: no such audio file: {crc: int}', 'error')
        logging.error(f"Error serving audio file: {e}")
        return redirect(url_for('.favourites'))
    
    return send_file(file_path, mimetype='audio/mp4')