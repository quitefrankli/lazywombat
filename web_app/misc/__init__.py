import logging

from werkzeug.datastructures import FileStorage
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required
from pathlib import Path

from web_app.api.data_interface import DataInterface
from web_app.helpers import cur_user
from web_app.users import User


misc_api = Blueprint(
    'misc',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/misc'
)

class MiscDataInterface(DataInterface):
    # Shares same directory as api.data_interface.DataInterface
    def __init__(self) -> None:
        super().__init__()

    def save_file(self, file_storage: FileStorage, user: User) -> None:
        user_dir = self._get_user_dir(user)
        file_path = user_dir / str(file_storage.filename)

        self.atomic_write(file_path, stream=file_storage.stream, mode="wb")

    def get_file_path(self, filename: str, user: User) -> Path:
        return self._get_user_dir(user) / filename

@misc_api.context_processor
def inject_app_name():
    return dict(app_name='Misc')

@misc_api.route('/')
@login_required
def index():
    files = MiscDataInterface().list_files(cur_user()) if cur_user() else []
    return render_template("misc_index.html", files=files)

@misc_api.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('.index'))
    file: FileStorage = request.files['file']
    if not file.filename:
        flash('No selected file', 'error')
        return redirect(url_for('.index'))
    MiscDataInterface().save_file(file, cur_user())
    logging.info(f"user {cur_user().id} uploaded file: {file.filename}")
    flash('File uploaded successfully!', 'success')
    return redirect(url_for('.index'))

@misc_api.route('/download/<filename>')
@login_required
def download_file(filename: str):
    file_path = MiscDataInterface().get_file_path(filename, cur_user())
    return send_file(file_path, as_attachment=True)

@misc_api.route('/files_list')
@login_required
def files_list():
    files = MiscDataInterface().list_files(cur_user())
    return {'files': files}

@misc_api.route('/delete/<filename>', methods=['POST'])
@login_required
def delete_file(filename):
    try:
        MiscDataInterface().delete_data(filename, cur_user())
    except FileNotFoundError:
        flash('File not found or could not be deleted.', 'error')
        return redirect(url_for('.index'))
    
    flash('File deleted successfully!', 'success')

    return redirect(url_for('.index'))