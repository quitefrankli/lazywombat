import logging
import mimetypes
import queue
import stat
import threading
import zipfile
from pathlib import PurePosixPath

from werkzeug.datastructures import FileStorage
from flask import Blueprint, Response, render_template, request, send_file, redirect, stream_with_context, url_for, flash
import flask_login

from web_app.helpers import cur_user, register_app_name, require_login_blueprint
from web_app.helpers import limiter
from web_app.config import ConfigManager
from web_app.file_store.data_interface import DataInterface, format_file_size
from web_app.logging_utils import log_event


file_store_api = Blueprint(
    'file_store',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/file_store'
)


require_login_blueprint(file_store_api)
register_app_name(file_store_api, 'File Store')


class _ZipQueue:
    def __init__(self, output: queue.Queue) -> None:
        self.output = output
        self.position = 0

    def write(self, data: bytes) -> int:
        if data:
            self.output.put(data)
            self.position += len(data)
        return len(data)

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        pass


def _file_size(file: FileStorage) -> int:
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    return size


@file_store_api.route('/')
def index():
    user = cur_user()
    data_interface = DataInterface()
    base_path = request.form.get('base_path', '').strip('/')
    mode = request.args.get('mode', 'list')
    if mode not in ('list', 'grid'):
        mode = 'list'
    path = request.args.get('path', '')
    directory = (
        data_interface.list_directory(path, user, recent=mode == 'list')
        if user else {'folders': [], 'files': []}
    )

    # Calculate storage info for all users
    storage_info = None
    if user:
        total_used = data_interface.get_total_storage_size(user)
        fs_cfg = ConfigManager().file_store
        max_storage = fs_cfg.admin_quota_bytes if user.has_elevated_access() else fs_cfg.non_admin_quota_bytes
        usage_percent = (total_used / max_storage) * 100 if max_storage > 0 else 0
        storage_info = {
            'used': total_used,
            'used_formatted': format_file_size(total_used),
            'max': max_storage,
            'max_formatted': format_file_size(max_storage),
            'usage_percent': min(usage_percent, 100),  # Cap at 100%
            'remaining': max_storage - total_used,
            'remaining_formatted': format_file_size(max(0, max_storage - total_used))
        }

    return render_template(
        "file_store_index.html", directory=directory, current_path=path,
        storage_info=storage_info, mode=mode, thumbnail_config=ConfigManager().file_store,
    )


@file_store_api.route('/upload', methods=['POST'])
@limiter.limit(
    "10/second",
    key_func=lambda: flask_login.current_user.id,
    exempt_when=lambda: flask_login.current_user.has_elevated_access(),
)
def upload_file():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    user = cur_user()
    base_path = request.form.get('base_path', '').strip('/')
    files = request.files.getlist('file')
    archive = request.files.get('folder_archive')
    if not files and not archive:
        log_event(
            "file_store", "file_store.upload_rejected",
            level=logging.WARNING, user=user, reason="no_file",
        )
        if is_ajax:
            return {'error': 'No file part'}, 400
        flash('No file part', 'error')
        return redirect(url_for('.index'))
    if files and any(not file.filename for file in files):
        log_event(
            "file_store", "file_store.upload_rejected",
            level=logging.WARNING, user=user, reason="empty_filename",
        )
        if is_ajax:
            return {'error': 'No selected file'}, 400
        flash('No selected file', 'error')
        return redirect(url_for('.index'))
    
    data_interface = DataInterface()
    source = 'archive' if archive else 'files'
    folder_count = 0

    try:
        if archive:
            if files:
                raise ValueError('Upload either files or one folder archive')
            with zipfile.ZipFile(archive.stream) as zip_file:
                entries = zip_file.infolist()
                if len(entries) > ConfigManager().file_store.folder_upload_max_entries:
                    raise ValueError('Too many files in folder archive')
                folders, file_entries = [], []
                for entry in entries:
                    entry_path = entry.filename.rstrip('/')
                    path = data_interface._normalise_path(
                        f'{base_path}/{entry_path}' if base_path else entry_path,
                    )
                    if stat.S_ISLNK(entry.external_attr >> 16):
                        raise ValueError('Folder archive cannot contain links')
                    if entry.is_dir():
                        folders.append(path)
                    else:
                        file_entries.append((entry, path))
                paths = [path for _, path in file_entries]
                total_bytes = sum(entry.file_size for entry, _ in file_entries)
                folder_count = len(folders)
                data_interface.validate_batch_quota(paths, total_bytes, user)
                uploads = [
                    (
                        FileStorage(
                            stream=zip_file.open(entry), filename=entry.filename,
                            content_type=mimetypes.guess_type(entry.filename)[0] or 'application/octet-stream',
                        ),
                        path,
                    )
                    for entry, path in file_entries
                ]
                data_interface.save_files(uploads, folders, user)
        else:
            paths = [file.filename for file in files]
            total_bytes = sum(_file_size(file) for file in files)
            data_interface.validate_batch_quota(paths, total_bytes, user)
            data_interface.save_files([(file, file.filename) for file in files], [], user)
    except (ValueError, zipfile.BadZipFile) as error:
        log_event(
            "file_store", "file_store.upload_rejected",
            level=logging.WARNING, user=user, reason="invalid_upload",
            source=source, error_type=type(error).__name__,
        )
        if is_ajax:
            return {'error': str(error)}, 413
        flash(f'Upload failed: {error}', 'error')
        return redirect(url_for('.index'))
    except Exception:
        log_event(
            "file_store", "file_store.upload_failed",
            level=logging.ERROR, user=user, source=source, exc_info=True,
        )
        raise

    log_event(
        "file_store", "file_store.upload", user=user, base_path=base_path or '/', bytes=total_bytes,
        files=len(paths), folders=folder_count, source=source,
    )

    if is_ajax:
        return {'ok': True}, 200

    flash('File uploaded successfully!', 'success')
    return redirect(url_for('.index'))


@file_store_api.route('/download/<path:filename>')
def download_file(filename: str):
    user = cur_user()
    file_path = DataInterface().get_file_path(filename, user)
    log_event(
        "file_store", "file_store.download",
        user=user, bytes=file_path.stat().st_size, path=filename,
    )
    response = send_file(file_path, as_attachment=True, download_name=PurePosixPath(filename).name)

    response.cache_control.private = True
    response.cache_control.no_store = True

    return response


@file_store_api.route('/download-folder/<path:folder_path>')
def download_folder(folder_path: str):
    user = cur_user()
    files = DataInterface().get_folder_files(folder_path, user)
    log_event(
        "file_store", "file_store.download_folder",
        user=user, bytes=sum(file_path.stat().st_size for _, file_path in files),
        files=len(files), path=folder_path,
    )
    output: queue.Queue[bytes | None] = queue.Queue(
        maxsize=ConfigManager().file_store.archive_stream_queue_chunks,
    )

    def stream_zip():
        def write_zip() -> None:
            try:
                with zipfile.ZipFile(_ZipQueue(output), 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for archive_path, file_path in files:
                        archive.write(file_path, archive_path)
            finally:
                output.put(None)

        threading.Thread(target=write_zip, daemon=True).start()
        while (chunk := output.get()) is not None:
            yield chunk

    filename = f'{PurePosixPath(folder_path).name}.zip'
    response = Response(stream_with_context(stream_zip()), mimetype='application/zip')
    response.headers.set('Content-Disposition', 'attachment', filename=filename)
    response.cache_control.private = True
    response.cache_control.no_store = True
    return response


@file_store_api.route('/thumbnail/<path:filename>')
@limiter.limit("30/second", key_func=lambda: flask_login.current_user.id)
def thumbnail(filename: str):
    """Serve a thumbnail for an image file."""
    data_interface = DataInterface()
    thumbnail_path = data_interface.get_thumbnail_for_file(filename, cur_user())

    if not thumbnail_path or not thumbnail_path.exists():
        flash('Thumbnail not available', 'error')
        return redirect(url_for('.index'))
    
    response = send_file(thumbnail_path, mimetype='image/jpeg')

    response.cache_control.private = True
    response.cache_control.no_store = True

    return response


@file_store_api.route('/files_list')
def files_list():
    files = DataInterface().list_files(cur_user())
    return {'files': files}


@file_store_api.route('/delete/<path:filename>', methods=['POST'])
def delete_file(filename):
    user = cur_user()
    try:
        DataInterface().delete_file(filename, user)
    except FileNotFoundError:
        log_event(
            "file_store", "file_store.delete_missing",
            level=logging.WARNING, user=user, path=filename,
        )
        flash('File not found or could not be deleted.', 'error')
        return redirect(url_for('.index'))
    log_event("file_store", "file_store.delete", user=user, path=filename)
    flash('File deleted successfully!', 'success')

    return redirect(url_for('.index'))


@file_store_api.route('/folder', methods=['POST'])
def create_folder():
    user = cur_user()
    parent = request.form.get('parent', '').strip('/')
    name = request.form.get('path', '').strip('/')
    path = f'{parent}/{name}' if parent else name
    try:
        DataInterface().create_folder(path, user)
    except ValueError as error:
        log_event(
            "file_store", "file_store.folder_create_rejected",
            level=logging.WARNING, user=user, path=path,
            reason="invalid_path", error_type=type(error).__name__,
        )
        flash(str(error), 'error')
    else:
        log_event("file_store", "file_store.folder_create", user=user, path=path)
    return redirect(url_for('.index', path=request.form.get('parent', '')))


@file_store_api.route('/move', methods=['POST'])
def move_path():
    user = cur_user()
    source = request.form.get('source', '')
    destination = request.form.get('destination', '')
    try:
        DataInterface().move_path(source, destination, user)
    except (ValueError, FileNotFoundError) as error:
        log_event(
            "file_store", "file_store.move_rejected",
            level=logging.WARNING, user=user, destination=destination,
            reason="invalid_move", source=source,
            error_type=type(error).__name__,
        )
        flash(str(error), 'error')
    else:
        log_event(
            "file_store", "file_store.move",
            user=user, destination=destination, source=source,
        )
    return redirect(url_for('.index', path=request.form.get('parent', '')))


@file_store_api.route('/move-selected', methods=['POST'])
def move_selected():
    user = cur_user()
    paths = request.form.getlist('paths')
    destination = request.form.get('destination', '')
    try:
        DataInterface().move_paths(paths, destination, user)
    except (ValueError, FileNotFoundError) as error:
        log_event(
            "file_store", "file_store.bulk_move_rejected",
            level=logging.WARNING, user=user, destination=destination,
            files=len(paths), reason="invalid_move",
            error_type=type(error).__name__,
        )
        flash(str(error), 'error')
    else:
        log_event(
            "file_store", "file_store.bulk_move",
            user=user, destination=destination or '/', files=len(paths),
        )
        flash(f'Moved {len(paths)} item(s) successfully!', 'success')
    return redirect(url_for('.index', path=request.form.get('parent', ''), mode='list'))


@file_store_api.route('/delete-selected', methods=['POST'])
def delete_selected():
    user = cur_user()
    paths = request.form.getlist('paths')
    try:
        DataInterface().delete_paths(paths, user)
    except (ValueError, FileNotFoundError) as error:
        log_event(
            "file_store", "file_store.bulk_delete_rejected",
            level=logging.WARNING, user=user, files=len(paths),
            reason="invalid_delete", error_type=type(error).__name__,
        )
        flash(str(error), 'error')
    else:
        log_event(
            "file_store", "file_store.bulk_delete",
            user=user, files=len(paths),
        )
        flash(f'Deleted {len(paths)} item(s) successfully!', 'success')
    return redirect(url_for('.index', path=request.form.get('parent', ''), mode='list'))


@file_store_api.route('/delete_all', methods=['POST'])
def delete_all_files():
    user = cur_user()
    data_interface = DataInterface()
    # One transaction: clear the user's entries in a single locked read-modify-
    # write rather than N racy cycles.
    with data_interface.edit_metadata() as metadata:
        user_metadata = metadata.users.get(user.id)
        file_count = len(user_metadata.files) if user_metadata else 0
        if user_metadata:
            user_metadata.files = []
            user_metadata.folders = []
            data_interface._cleanup_unreferenced(metadata)

    log_event(
        "file_store", "file_store.delete_all",
        user=user, files=file_count,
    )
    flash('All files deleted successfully!', 'success')
    return redirect(url_for('.index'))
