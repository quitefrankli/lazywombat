from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required
from werkzeug.datastructures import FileStorage

from web_app.cheapify.data_interface import DataInterface as CheapifyDataInterface
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
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('.index'))
    file: FileStorage = request.files['file']
    if not file.filename:
        flash('No selected file', 'error')
        return redirect(url_for('.index'))
    CheapifyDataInterface().save_file(file, cur_user())
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
    success = CheapifyDataInterface().delete_file(filename, cur_user())
    if success:
        flash('File deleted successfully!', 'success')
    else:
        flash('File not found or could not be deleted.', 'error')
    return redirect(url_for('.index'))
