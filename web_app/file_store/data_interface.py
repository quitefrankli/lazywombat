import binascii
import logging
import os
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import List, Optional

from PIL import Image
from pydantic import BaseModel
from werkzeug.datastructures import FileStorage

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager
from web_app.users import User
from web_app.logging_utils import log_event


def format_file_size(size_bytes: int) -> str:
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


class FileMetadata(BaseModel):
    crc: int
    original_name: str  # First uploaded name (for reference)
    size: int
    upload_date: str  # ISO format datetime string
    mime_type: str = 'application/octet-stream'


class UserFileEntry(BaseModel):
    crc: int
    original_name: str  # User's name for this file
    path: str = ""
    uploaded_at: str = ""


class UserMetadata(BaseModel):
    user_id: str
    files: list[UserFileEntry] = []
    folders: list[str] = []


class Metadata(BaseModel):
    users: dict[str, UserMetadata] = {}
    files: dict[int, FileMetadata] = {}


class DataInterface(BaseDataInterface):
    """Data interface for file storage operations."""

    def __init__(self) -> None:
        super().__init__()
        self.data_sub_dirname = "file_store"
        self.file_store_dir = ConfigManager().save_data_path / self.data_sub_dirname
        self.files_dir = self.file_store_dir / "files"
        self.thumbnails_dir = self.file_store_dir / "thumbnails"
        self.metadata_file = self.file_store_dir / "metadata.json"

    def get_metadata(self) -> Metadata:
        """Read-only load. For mutations use edit_metadata() so the write is locked."""
        return self.load_model(self.metadata_file, Metadata, sync=False) or Metadata()

    def _save_metadata(self, metadata: Metadata) -> None:
        """Internal whole-model write used only for non-RMW initialization."""
        self._save_model(self.metadata_file, metadata)

    def edit_metadata(self):
        """Transactional edit of the shared file_store metadata blob.

        `with di.edit_metadata() as metadata: ...` — locks the file, loads fresh,
        saves on clean exit (only if changed). The blob is global (all users),
        so this is a global lock. Do NOT stream large uploads inside the block;
        stream to a temp file first (see _stream_upload), then edit_metadata().
        """
        return self.edit_model(self.metadata_file, Metadata)

    @staticmethod
    def _normalise_path(path: str, *, allow_root: bool = False) -> str:
        path = path.replace('\\', '/')
        if allow_root and path in ('', '.'):
            return ''
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or not path or any(part in ('', '.', '..') for part in candidate.parts):
            raise ValueError('Invalid file path')
        return candidate.as_posix()

    @staticmethod
    def _entry_path(entry: UserFileEntry) -> str:
        return entry.path or entry.original_name

    def _user_metadata(self, metadata: Metadata, user: User) -> UserMetadata:
        return metadata.users.setdefault(user.id, UserMetadata(user_id=user.id))

    @staticmethod
    def _parent_folders(path: str) -> list[str]:
        parents = []
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath('.'):
            parents.append(parent.as_posix())
            parent = parent.parent
        return list(reversed(parents))

    def _ensure_parent_folders(self, user_metadata: UserMetadata, path: str) -> None:
        for folder in self._parent_folders(path):
            if folder not in user_metadata.folders:
                user_metadata.folders.append(folder)

    def _folder_paths(self, user_metadata: UserMetadata) -> set[str]:
        folders = set(user_metadata.folders)
        for entry in user_metadata.files:
            folders.update(self._parent_folders(self._entry_path(entry)))
        return folders

    def _normalise_batch_paths(self, paths: list[str]) -> list[str]:
        normalised = list(dict.fromkeys(self._normalise_path(path) for path in paths if path.strip()))
        if not normalised:
            raise ValueError('Select at least one item')
        return [
            path for path in normalised
            if not any(path.startswith(f'{parent}/') for parent in normalised if parent != path)
        ]

    def _cleanup_unreferenced(self, metadata: Metadata) -> None:
        referenced = {
            entry.crc
            for user_metadata in metadata.users.values()
            for entry in user_metadata.files
        }
        for crc in set(metadata.files) - referenced:
            self.atomic_delete(self.files_dir / str(crc))
            self.atomic_delete(self.get_thumbnail_path(crc))
            metadata.files.pop(crc, None)

    def get_user_metadata(self, user: User) -> UserMetadata:
        """Get user metadata, creates new if doesn't exist."""
        metadata = self.get_metadata()
        if user.id not in metadata.users:
            metadata.users[user.id] = UserMetadata(user_id=user.id)
        return metadata.users[user.id]

    def _stream_upload(self, file_storage: FileStorage) -> tuple[Path, int, int]:
        """Spool an upload to a temp file, returning (temp_path, crc, size).

        Kept OUTSIDE the metadata lock: streaming a large file can take longer
        than the lock's auto-expiry timeout, and it touches only a unique temp
        file, so it needs no serialization.
        """
        self.files_dir.mkdir(parents=True, exist_ok=True)
        chunk_size = ConfigManager().file_store.upload_stream_chunk_bytes
        crc = 0
        file_size = 0
        with tempfile.NamedTemporaryFile(dir=self.files_dir, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            try:
                while chunk := file_storage.stream.read(chunk_size):
                    temp_file.write(chunk)
                    crc = binascii.crc32(chunk, crc)
                    file_size += len(chunk)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
        return temp_path, crc, file_size

    def save_file(
        self,
        file_storage: FileStorage,
        user: User,
        relative_path: str | None = None,
        metadata: Metadata | None = None,
    ) -> int:
        """Save a file and return its CRC."""
        temp_path, crc, file_size = self._stream_upload(file_storage)

        # Standalone call: the metadata read->mutate->save must be atomic across
        # workers. Streaming above already happened outside the lock and the
        # os.replace below is a fast rename, so the lock is held only briefly.
        # When called from save_files (metadata passed in), the caller holds it.
        if metadata is None:
            with self.edit_metadata() as metadata:
                result = self._save_file_metadata(file_storage, user, relative_path, temp_path, crc, file_size, metadata)
                self._cleanup_unreferenced(metadata)
            return result
        return self._save_file_metadata(file_storage, user, relative_path, temp_path, crc, file_size, metadata)

    def _save_file_metadata(self, file_storage, user, relative_path, temp_path, crc, file_size, metadata):
        user_metadata = self._user_metadata(metadata, user)
        stored_path = self._normalise_path(relative_path or file_storage.filename)
        existing_entry = next(
            (entry for entry in user_metadata.files if self._entry_path(entry) == stored_path),
            None,
        )

        # Ignore duplicate uploads for the same user when content matches.
        if (existing_entry and existing_entry.crc == crc) or (
            relative_path is None and any(entry.crc == crc for entry in user_metadata.files)
        ):
            temp_path.unlink(missing_ok=True)
            return crc

        # Check if file content already exists on disk
        if crc not in metadata.files:
            file_path = self.files_dir / str(crc)
            os.replace(temp_path, file_path)
            file_path.chmod(0o644)

            # Create file metadata (use first uploaded name as reference)
            file_metadata = FileMetadata(
                crc=crc,
                original_name=file_storage.filename,
                size=file_size,
                upload_date=datetime.now().isoformat(),
                mime_type=file_storage.content_type or 'application/octet-stream'
            )
            metadata.files[crc] = file_metadata
        else:
            temp_path.unlink(missing_ok=True)

        if existing_entry:
            user_metadata.files.remove(existing_entry)

        # Add to user's file list for new user content entry.
        user_file_entry = UserFileEntry(
            crc=crc,
            original_name=PurePosixPath(stored_path).name,
            path=stored_path,
            uploaded_at=datetime.now().isoformat(),
        )
        user_metadata.files.append(user_file_entry)
        self._ensure_parent_folders(user_metadata, stored_path)
        return crc

    def save_files(
        self,
        uploads: list[tuple[FileStorage, str]],
        folders: list[str],
        user: User,
    ) -> None:
        """Atomically save a validated file/folder batch."""
        if len(uploads) > ConfigManager().file_store.folder_upload_max_entries:
            raise ValueError('Too many files in folder upload')
        paths = [self._normalise_path(path) for _, path in uploads]
        if len(paths) != len(set(paths)):
            raise ValueError('Folder upload contains duplicate paths')

        # Stream every upload to a temp file OUTSIDE the metadata lock (slow,
        # and can exceed the lock timeout). Then hold the lock only for the
        # fast validation + metadata mutation + renames.
        streamed = [(*self._stream_upload(file_storage), file_storage, path) for file_storage, path in uploads]
        try:
            with self.edit_metadata() as metadata:
                original_crcs = set(metadata.files)
                user_metadata = self._user_metadata(metadata, user)
                existing_paths = {self._entry_path(entry) for entry in user_metadata.files}
                required_folders = {
                    parent
                    for path in paths
                    for parent in self._parent_folders(path)
                }
                required_folders.update(self._normalise_path(folder) for folder in folders)
                if required_folders & set(paths) or (required_folders & existing_paths) - set(paths):
                    raise ValueError('A file conflicts with a folder path')
                if any(
                    path in user_metadata.folders or any(folder.startswith(f'{path}/') for folder in user_metadata.folders)
                    for path in paths
                ):
                    raise ValueError('A folder conflicts with a file path')
                for folder in folders:
                    self._ensure_parent_folders(user_metadata, self._normalise_path(folder))
                    normalised = self._normalise_path(folder)
                    if normalised not in user_metadata.folders:
                        user_metadata.folders.append(normalised)
                try:
                    for temp_path, crc, file_size, file_storage, path in streamed:
                        self._save_file_metadata(file_storage, user, path, temp_path, crc, file_size, metadata)
                    self._cleanup_unreferenced(metadata)
                except Exception:
                    # Roll back files written to disk this call; edit_metadata
                    # discards the in-memory metadata changes on the raise.
                    for crc in set(metadata.files) - original_crcs:
                        self.atomic_delete(self.files_dir / str(crc))
                        self.atomic_delete(self.get_thumbnail_path(crc))
                    raise
        finally:
            # Any temp files not consumed by os.replace (duplicate content) are
            # cleaned by _save_file_metadata; sweep leftovers on error paths.
            for temp_path, *_ in streamed:
                Path(temp_path).unlink(missing_ok=True)

    def get_folder_files(self, path: str, user: User) -> list[tuple[str, Path]]:
        folder_path = self._normalise_path(path)
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)
        if not user_metadata:
            raise FileNotFoundError(folder_path)
        prefix = f'{folder_path}/'
        files = [
            (entry_path, self.files_dir / str(entry.crc))
            for entry in user_metadata.files
            if (entry_path := self._entry_path(entry)).startswith(prefix)
        ]
        if not files and folder_path not in user_metadata.folders:
            raise FileNotFoundError(folder_path)
        return files

    def get_file_path(self, filename: str, user: User) -> Path:
        """Get the full path to a file by its original name."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            raise FileNotFoundError(f"User {user.id} has no files")

        target_path = self._normalise_path(filename)
        for user_file in user_metadata.files:
            if self._entry_path(user_file) == target_path:
                return self.files_dir / str(user_file.crc)

        raise FileNotFoundError(f"File {filename} not found for user {user.id}")

    def delete_file(self, filename: str, user: User) -> None:
        """Delete a file from the user's storage."""
        self.delete_path(filename, user)

    def delete_path(self, path: str, user: User) -> None:
        """Delete one file or a folder and all of its contents."""
        target_path = self._normalise_path(path)
        with self.edit_metadata() as metadata:
            user_metadata = metadata.users.get(user.id)

            if not user_metadata:
                raise FileNotFoundError(f"File: {path} not found for user: {user.id}")

            is_folder = target_path in user_metadata.folders
            removed = [
                entry for entry in user_metadata.files
                if self._entry_path(entry) == target_path or self._entry_path(entry).startswith(f'{target_path}/')
            ]
            if not removed and not is_folder:
                raise FileNotFoundError(f"File: {path} not found for user: {user.id}")

            user_metadata.files = [entry for entry in user_metadata.files if entry not in removed]
            user_metadata.folders = [
                folder for folder in user_metadata.folders
                if folder != target_path and not folder.startswith(f'{target_path}/')
            ]

            self._cleanup_unreferenced(metadata)

    def delete_paths(self, paths: list[str], user: User) -> None:
        """Delete multiple files or folders in one metadata update."""
        target_paths = self._normalise_batch_paths(paths)
        with self.edit_metadata() as metadata:
            user_metadata = metadata.users.get(user.id)
            if not user_metadata:
                raise FileNotFoundError('Selected items were not found')

            folder_paths = self._folder_paths(user_metadata)
            for target_path in target_paths:
                exists = target_path in folder_paths or any(
                    self._entry_path(entry) == target_path
                    or self._entry_path(entry).startswith(f'{target_path}/')
                    for entry in user_metadata.files
                )
                if not exists:
                    raise FileNotFoundError(f'{target_path} not found')

            user_metadata.files = [
                entry for entry in user_metadata.files
                if not any(
                    self._entry_path(entry) == target_path
                    or self._entry_path(entry).startswith(f'{target_path}/')
                    for target_path in target_paths
                )
            ]
            user_metadata.folders = [
                folder for folder in user_metadata.folders
                if not any(folder == target_path or folder.startswith(f'{target_path}/') for target_path in target_paths)
            ]
            self._cleanup_unreferenced(metadata)

    def create_folder(self, path: str, user: User) -> None:
        folder_path = self._normalise_path(path)
        with self.edit_metadata() as metadata:
            user_metadata = self._user_metadata(metadata, user)
            if any(self._entry_path(entry) == folder_path for entry in user_metadata.files):
                raise ValueError('A file already exists at this path')
            self._ensure_parent_folders(user_metadata, folder_path)
            if folder_path not in user_metadata.folders:
                user_metadata.folders.append(folder_path)

    def list_directory(
        self, path: str, user: User, *, recent: bool = False
    ) -> dict[str, list[dict]]:
        directory = self._normalise_path(path, allow_root=True)
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)
        if not user_metadata:
            return {'folders': [], 'files': []}
        prefix = f'{directory}/' if directory else ''
        folders = set(user_metadata.folders)
        for entry in user_metadata.files:
            entry_path = self._entry_path(entry)
            if '/' in entry_path:
                folders.update(self._parent_folders(entry_path))
        direct_folders = sorted(
            [
                {'name': folder[len(prefix):], 'path': folder}
                for folder in folders
                if folder.startswith(prefix) and '/' not in folder[len(prefix):]
            ],
            key=lambda item: item['name'].lower(),
        )
        files = []
        for entry in user_metadata.files:
            entry_path = self._entry_path(entry)
            if not entry_path.startswith(prefix) or '/' in entry_path[len(prefix):]:
                continue
            file_meta = metadata.files.get(entry.crc)
            if file_meta:
                files.append({
                    'name': entry_path[len(prefix):], 'path': entry_path, 'size': file_meta.size,
                    'size_formatted': format_file_size(file_meta.size), 'mime_type': file_meta.mime_type,
                })
        result = {
            'folders': direct_folders,
            'files': sorted(files, key=lambda item: item['name'].lower()),
        }
        if not recent:
            return result

        upload_times = {
            self._entry_path(entry): datetime.fromisoformat(
                entry.uploaded_at or metadata.files[entry.crc].upload_date
            ).timestamp()
            for entry in user_metadata.files
            if entry.crc in metadata.files
        }
        items = []
        for folder in direct_folders:
            folder_prefix = f"{folder['path']}/"
            modified = max(
                (uploaded for entry_path, uploaded in upload_times.items() if entry_path.startswith(folder_prefix)),
                default=float('-inf'),
            )
            items.append({**folder, 'kind': 'folder', '_modified': modified})
        for file in result['files']:
            items.append({
                **file,
                'kind': 'file',
                '_modified': upload_times.get(file['path'], float('-inf')),
            })
        result['items'] = sorted(
            items,
            key=lambda item: (-item['_modified'], item['name'].lower()),
        )
        return result

    def move_path(self, source: str, destination: str, user: User) -> None:
        source_path = self._normalise_path(source)
        destination_path = self._normalise_path(destination)
        if destination_path == source_path or destination_path.startswith(f'{source_path}/'):
            raise ValueError('Invalid destination')
        with self.edit_metadata() as metadata:
            self._move_path_locked(metadata, source_path, destination_path, user)

    def _move_path_locked(self, metadata: Metadata, source_path: str, destination_path: str, user: User) -> None:
        user_metadata = self._user_metadata(metadata, user)
        source_files = [entry for entry in user_metadata.files if self._entry_path(entry) == source_path]
        is_folder = source_path in user_metadata.folders or any(
            self._entry_path(entry).startswith(f'{source_path}/') for entry in user_metadata.files
        )
        if not source_files and not is_folder:
            raise FileNotFoundError(source_path)
        affected_paths = {
            self._entry_path(entry) for entry in user_metadata.files
            if self._entry_path(entry) == source_path or self._entry_path(entry).startswith(f'{source_path}/')
        }
        destinations = {
            destination_path + entry_path[len(source_path):]
            for entry_path in affected_paths
        }
        if any(self._entry_path(entry) in destinations for entry in user_metadata.files if self._entry_path(entry) not in affected_paths):
            raise ValueError('Destination already exists')
        for entry in user_metadata.files:
            current = self._entry_path(entry)
            if current in affected_paths:
                entry.path = destination_path + current[len(source_path):]
                entry.original_name = PurePosixPath(entry.path).name
        old_folders = list(user_metadata.folders)
        user_metadata.folders = [folder for folder in user_metadata.folders if not (folder == source_path or folder.startswith(f'{source_path}/'))]
        for folder in old_folders:
            if folder == source_path or folder.startswith(f'{source_path}/'):
                user_metadata.folders.append(destination_path + folder[len(source_path):])
        self._ensure_parent_folders(user_metadata, destination_path)
        user_metadata.folders = list(dict.fromkeys(user_metadata.folders))

    def move_paths(self, paths: list[str], destination: str, user: User) -> None:
        """Move multiple files or folders into one destination folder atomically."""
        source_paths = self._normalise_batch_paths(paths)
        destination_path = self._normalise_path(destination, allow_root=True)
        with self.edit_metadata() as metadata:
            self._move_paths_locked(metadata, source_paths, destination_path, user)

    def _move_paths_locked(self, metadata: Metadata, source_paths: list[str], destination_path: str, user: User) -> None:
        user_metadata = metadata.users.get(user.id)
        if not user_metadata:
            raise FileNotFoundError('Selected items were not found')

        file_paths = {self._entry_path(entry) for entry in user_metadata.files}
        folder_paths = self._folder_paths(user_metadata)
        source_is_folder = {}
        for source_path in source_paths:
            has_files = any(
                file_path == source_path or file_path.startswith(f'{source_path}/')
                for file_path in file_paths
            )
            source_is_folder[source_path] = source_path in folder_paths or any(
                file_path.startswith(f'{source_path}/') for file_path in file_paths
            )
            if not has_files and not source_is_folder[source_path]:
                raise FileNotFoundError(f'{source_path} not found')
            if source_is_folder[source_path] and (
                destination_path == source_path or destination_path.startswith(f'{source_path}/')
            ):
                raise ValueError('Invalid destination')

        if destination_path and (
            destination_path in file_paths
            or any(destination_path.startswith(f'{file_path}/') for file_path in file_paths)
        ):
            raise ValueError('Destination must be a folder')

        destinations = {
            source_path: (
                f'{destination_path}/{PurePosixPath(source_path).name}'
                if destination_path else PurePosixPath(source_path).name
            )
            for source_path in source_paths
        }
        if len(set(destinations.values())) != len(destinations):
            raise ValueError('Destination already exists')
        if any(destinations[source_path] == source_path for source_path in source_paths):
            raise ValueError('Invalid destination')

        affected_files = {
            file_path for file_path in file_paths
            if any(file_path == source_path or file_path.startswith(f'{source_path}/') for source_path in source_paths)
        }
        affected_folders = {
            folder_path for folder_path in folder_paths
            if any(folder_path == source_path or folder_path.startswith(f'{source_path}/') for source_path in source_paths)
        }
        unaffected_paths = (file_paths | folder_paths) - affected_files - affected_folders
        projected_paths = set()
        for source_path, target_path in destinations.items():
            for file_path in affected_files | affected_folders:
                if file_path == source_path or file_path.startswith(f'{source_path}/'):
                    projected_path = target_path + file_path[len(source_path):]
                    if projected_path in unaffected_paths or projected_path in projected_paths:
                        raise ValueError('Destination already exists')
                    projected_paths.add(projected_path)

        for entry in user_metadata.files:
            current_path = self._entry_path(entry)
            for source_path, target_path in destinations.items():
                if current_path == source_path or current_path.startswith(f'{source_path}/'):
                    entry.path = target_path + current_path[len(source_path):]
                    entry.original_name = PurePosixPath(entry.path).name
                    break

        updated_folders = [folder for folder in user_metadata.folders if folder not in affected_folders]
        updated_folders.extend(
            destinations[source_path] + folder_path[len(source_path):]
            for source_path, target_path in destinations.items()
            for folder_path in affected_folders
            if folder_path == source_path or folder_path.startswith(f'{source_path}/')
        )
        if destination_path:
            updated_folders.append(destination_path)
        user_metadata.folders = list(dict.fromkeys(updated_folders))
        self._ensure_parent_folders(user_metadata, destination_path)

    def list_files(self, user: User) -> List[str]:
        """Get list of filenames for a user."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            return []

        return [self._entry_path(user_file) for user_file in user_metadata.files]

    def list_files_with_metadata(self, user: User) -> List[dict]:
        """Get list of files with their metadata (size, upload date)."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            return []

        files = []
        for user_file in user_metadata.files:
            file_meta = metadata.files.get(user_file.crc)
            if file_meta:
                upload_date = datetime.fromisoformat(user_file.uploaded_at or file_meta.upload_date)
                files.append({
                    'name': self._entry_path(user_file),
                    'size': file_meta.size,
                    'size_formatted': format_file_size(file_meta.size),
                    'modified': upload_date,
                    'modified_formatted': upload_date.strftime('%Y-%m-%d %H:%M'),
                    'crc': file_meta.crc,
                    'mime_type': file_meta.mime_type
                })

        # Sort by upload date descending (newest first)
        files.sort(key=lambda x: x['modified'], reverse=True)
        return files

    def get_total_storage_size(self, user: User) -> int:
        """Get total storage size used by a user in bytes."""
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)

        if not user_metadata:
            return 0

        total_size = 0
        for user_file in user_metadata.files:
            file_meta = metadata.files.get(user_file.crc)
            if file_meta:
                total_size += file_meta.size

        return total_size

    def validate_batch_quota(self, paths: list[str], incoming_size: int, user: User) -> None:
        metadata = self.get_metadata()
        user_metadata = metadata.users.get(user.id)
        replaced_size = 0
        if user_metadata:
            replacing = {self._normalise_path(path) for path in paths}
            for entry in user_metadata.files:
                if self._entry_path(entry) in replacing and (file_meta := metadata.files.get(entry.crc)):
                    replaced_size += file_meta.size
        max_storage = (
            ConfigManager().file_store.admin_quota_bytes
            if user.has_elevated_access()
            else ConfigManager().file_store.non_admin_quota_bytes
        )
        final_size = self.get_total_storage_size(user) - replaced_size + incoming_size
        if final_size > max_storage:
            raise ValueError(f'Upload exceeds the {format_file_size(max_storage)} storage limit')

    def get_thumbnail_path(self, crc: int) -> Path:
        """Get the path to a thumbnail file."""
        return self.thumbnails_dir / f"{crc}.jpg"

    def has_thumbnail(self, crc: int) -> bool:
        """Check if a thumbnail exists for a file."""
        return self.get_thumbnail_path(crc).exists()

    def create_thumbnail(self, crc: int, max_size: tuple = (300, 300)) -> Optional[Path]:
        """Create a thumbnail for an image file."""
        file_path = self.files_dir / str(crc)
        if not file_path.exists():
            return None

        thumbnail_path = self.get_thumbnail_path(crc)
        if thumbnail_path.exists():
            return thumbnail_path

        try:
            # Open the image
            with Image.open(file_path) as img:
                # Convert RGBA to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')

                # Create thumbnail
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

                # Save thumbnail
                self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
                img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)

                return thumbnail_path
        except Exception as e:
            log_event(
                "file_store", "file_store.thumbnail_create_failed",
                level=logging.ERROR, crc=crc, exc_info=e,
                error_type=type(e).__name__,
            )
            return None

    def get_thumbnail_for_file(self, filename: str, user: User) -> Optional[Path]:
        """Get or create thumbnail for a user's file."""
        try:
            # Get the file path to find the CRC
            file_path = self.get_file_path(filename, user)
            crc = int(file_path.name)

            # Check if thumbnail exists, create if not
            if not self.has_thumbnail(crc):
                return self.create_thumbnail(crc)

            return self.get_thumbnail_path(crc)
        except Exception as e:
            log_event(
                "file_store", "file_store.thumbnail_lookup_failed",
                level=logging.ERROR, filename=filename, exc_info=e,
                error_type=type(e).__name__,
            )
            return None

    def backup_data(self, backup_dir: Path) -> None:
        """Backup file store data to the backup directory."""
        self._backup_subtree(self.file_store_dir, backup_dir, self.data_sub_dirname)

    def delete_user_data(self, user: User) -> None:
        with self.edit_metadata() as metadata:
            user_metadata = metadata.users.pop(user.id, None)
            if user_metadata is None:
                return

            user_crcs = {user_file.crc for user_file in user_metadata.files}
            referenced_crcs = {
                user_file.crc
                for other_user_metadata in metadata.users.values()
                for user_file in other_user_metadata.files
            }

            for crc in user_crcs - referenced_crcs:
                self.atomic_delete(self.files_dir / str(crc))
                self.atomic_delete(self.get_thumbnail_path(crc))
                metadata.files.pop(crc, None)
