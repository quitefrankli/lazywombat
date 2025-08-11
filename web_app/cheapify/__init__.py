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

@cheapify_api.route('/upload', methods=['POST'])
@login_required
def upload_file():
    print(request.files)
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('.index'))
    file: FileStorage = request.files['file']
    if not file.filename:
        flash('No selected file', 'error')
        return redirect(url_for('.index'))
    CheapifyDataInterface().save_file(file, cur_user())
    logging.info(f"user {cur_user().id} uploaded file: {file.filename}")
    flash('File uploaded successfully!', 'success')
    return redirect(url_for('.index'))

@cheapify_api.route('/download/<filename>')
@login_required
def download_file(filename: str):
    file_path = CheapifyDataInterface().get_file_path(filename, cur_user())
    return send_file(file_path, as_attachment=True)

@cheapify_api.route('/files_list')
@login_required
def files_list():
    files = CheapifyDataInterface().list_files(cur_user())
    return {'files': files}

@cheapify_api.route('/delete/<filename>', methods=['POST'])
@login_required
def delete_file(filename):
    try:
        CheapifyDataInterface().delete_data(filename, cur_user())
    except FileNotFoundError:
        flash('File not found or could not be deleted.', 'error')
        return redirect(url_for('.index'))
    
    flash('File deleted successfully!', 'success')

    return redirect(url_for('.index'))

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
    user_dir = CheapifyDataInterface().get_user_dir(user)
    try:
        filename = AudioDownloader.download_youtube_audio(video_id, title, user_dir)
        flash(f'Audio downloaded for: {filename}', 'success')
    except Exception as e:
        flash(f'Error downloading audio: {e}', 'error')
    return redirect(url_for('.index'))

AudioDownloader.download_youtube_audio("CGj85pVzRJs", "Sample Video", Path("resources"))