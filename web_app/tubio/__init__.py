import logging
import yt_dlp
from pathlib import Path

from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required
from werkzeug.datastructures import FileStorage

from web_app.tubio.data_interface import DataInterface as TubioDataInterface
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.helpers import cur_user


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

@tubio_api.route('/')
@login_required
def index():
    files = TubioDataInterface().list_files(cur_user()) if cur_user() else []
    return render_template("tubio_index.html", files=files)

@tubio_api.route('/youtube_search', methods=['GET', 'POST'])
@login_required
def youtube_search():
    results = []
    query = ''
    if request.method == 'POST':
        query = request.form.get('youtube_query', '')
        if query:
            results = AudioDownloader.search_youtube(query)
    return render_template('tubio_index.html', 
                           files=TubioDataInterface().list_files(cur_user()), 
                           youtube_results=results, 
                           youtube_query=query)

@tubio_api.route('/youtube_download', methods=['POST'])
@login_required
def youtube_download():
    video_id = request.form.get('video_id')
    title = request.form.get('title')
    if not video_id:
        flash('No video ID provided.', 'error')
        return redirect(url_for('.index'))
    user = cur_user()
    user_dir = TubioDataInterface()._get_user_dir(user)
    try:
        filename = AudioDownloader.download_youtube_audio(video_id, title, user_dir)
        flash(f'Audio downloaded for: {filename}', 'success')
    except Exception as e:
        flash(f'Error downloading audio: {e}', 'error')
    return redirect(url_for('.index'))

@tubio_api.route('/audio/<filename>')
@login_required
def serve_audio(filename):
    user = cur_user()
    file_path = TubioDataInterface().get_file_path(filename, user)
    if not file_path.exists():
        flash('Audio file not found.', 'error')
        return redirect(url_for('.index'))
    return send_file(file_path, mimetype='audio/mp4')