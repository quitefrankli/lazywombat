import logging
import yt_dlp
from pathlib import Path

from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required
from werkzeug.datastructures import FileStorage

from web_app.cheapify.data_interface import DataInterface as CheapifyDataInterface
from web_app.cheapify.audio_downloader import AudioDownloader
from web_app.helpers import cur_user


cheapify_api = Blueprint(
    'cheapify',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/cheapify'
)

@cheapify_api.context_processor
def inject_app_name():
    return dict(app_name='Cheapify')

@cheapify_api.route('/')
@login_required
def index():
    files = CheapifyDataInterface().list_files(cur_user()) if cur_user() else []
    return render_template("cheapify_index.html", files=files)

@cheapify_api.route('/youtube_search', methods=['GET', 'POST'])
@login_required
def youtube_search():
    results = []
    query = ''
    if request.method == 'POST':
        query = request.form.get('youtube_query', '')
        if query:
            results = AudioDownloader.search_youtube(query)
    return render_template('cheapify_index.html', 
                           files=CheapifyDataInterface().list_files(cur_user()), 
                           youtube_results=results, 
                           youtube_query=query)

@cheapify_api.route('/youtube_download', methods=['POST'])
@login_required
def youtube_download():
    video_id = request.form.get('video_id')
    title = request.form.get('title')
    if not video_id:
        flash('No video ID provided.', 'error')
        return redirect(url_for('.index'))
    user = cur_user()
    user_dir = CheapifyDataInterface()._get_user_dir(user)
    try:
        filename = AudioDownloader.download_youtube_audio(video_id, title, user_dir)
        flash(f'Audio downloaded for: {filename}', 'success')
    except Exception as e:
        flash(f'Error downloading audio: {e}', 'error')
    return redirect(url_for('.index'))

@cheapify_api.route('/audio/<filename>')
@login_required
def serve_audio(filename):
    user = cur_user()
    file_path = CheapifyDataInterface().get_file_path(filename, user)
    if not file_path.exists():
        flash('Audio file not found.', 'error')
        return redirect(url_for('.index'))
    return send_file(file_path, mimetype='audio/mp4')