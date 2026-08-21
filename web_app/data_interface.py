import json
import logging
import os
import random
import shutil
import string
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from git import Repo
from nabicat_app_sdk import (
    DataInterface as SdkDataInterface,
)
from nabicat_app_sdk import (
    DataRoot,
)
from nabicat_app_sdk import (
    DataSyncer as SdkDataSyncer,
)

from web_app.config import ConfigManager
from web_app.logging_utils import log_event
from web_app.users import User, UsersFile


class _S3Client:
    BUCKET_NAME = 'todoist'
    
    def __init__(self) -> None:
        ACCESS_KEY = os.environ["AWS_ACCESS_KEY_ID"]
        SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
        self.s3_client = boto3.client('s3', 
                                      aws_access_key_id=ACCESS_KEY, 
                                      aws_secret_access_key=SECRET_ACCESS_KEY)

    @staticmethod
    def _get_s3_path(file: Path) -> str:
        return str(file.relative_to(ConfigManager().save_data_path).as_posix())

    def download_file(self, file: Path) -> None:
        log_event(
            "storage", "storage.s3_download_started",
            source=self._get_s3_path(file), destination=str(file),
        )
        if not file.parent.exists():
            file.parent.mkdir(exist_ok=True, parents=True)
        try:
            self.s3_client.download_file(self.BUCKET_NAME, self._get_s3_path(file), str(file))
        except ClientError as e:
            if e.response['Error']['Code'] == "404":
                log_event(
                    "storage", "storage.s3_file_missing",
                    level=logging.WARNING, path=str(file),
                )
            else:
                raise

    def upload_file(self, file: Path) -> None:
        log_event(
            "storage", "storage.s3_upload_started",
            source=str(file), destination=self._get_s3_path(file),
        )
        self.s3_client.upload_file(str(file), self.BUCKET_NAME, self._get_s3_path(file))

class _OfflineClient:
    def download_file(self, file: Path) -> None:
        pass

    def upload_file(self, file: Path) -> None:
        pass

class DataSyncer(SdkDataSyncer):
    _instance: Optional['DataSyncer'] = None

    @classmethod
    def instance(cls) -> 'DataSyncer':
        if cls._instance is None:
            config = ConfigManager()
            if config.use_offline_syncer:
                cls._instance = DataSyncer(_OfflineClient())
            else:
                cls._instance = DataSyncer(_S3Client())

        return cls._instance
    
    def __init__(self, client: _S3Client | _OfflineClient) -> None:
        super().__init__(client)


class DataInterface(SdkDataInterface):
    def __init__(self) -> None:
        from web_app.redis_client import rmw_lock

        config = ConfigManager()
        syncer = DataSyncer.instance()
        super().__init__(
            DataRoot(root=config.save_data_path.parent),
            syncer=syncer,
            lock_factory=rmw_lock,
        )
        self.backups_directory = config.save_data_path.parent / "backups"
        self.users_file = config.save_data_path / "users.json"
        self.metadata_filename = "metadata.json"
    
    def delete_user_data(self, user: User) -> None:
        raise NotImplementedError("Method not overriden")
    
    def backup_data(self, backup_dir: Path) -> None:
        if type(self) != DataInterface:
            raise NotImplementedError("Meothd not overriden")
        self.generate_metadata_file(backup_dir)
        shutil.copy2(self.users_file, backup_dir / "users.json")

    def _backup_subtree(self, src_dir: Path, backup_dir: Path, name: str) -> None:
        """Copy a subapp's data subtree into the backup, no-op if it doesn't exist.

        Uses ``dirs_exist_ok=True`` so a re-run into an existing backup dir
        merges rather than raising.
        """
        if src_dir.exists():
            shutil.copytree(src_dir, backup_dir / name, dirs_exist_ok=True)

    def load_users(self) -> dict[str, User]:
        """Read-only load. For mutations use edit_users() so the write is locked."""
        self.data_syncer.download_file(self.users_file)
        return self.load_users_local()

    def load_users_local(self) -> dict[str, User]:
        """Read the atomic local snapshot without external synchronization."""
        users_file = self.load_model(self.users_file, UsersFile, sync=False) or UsersFile()
        return users_file.as_dict()

    def _save_users(self, users: list[User]) -> None:
        self._save_model(self.users_file, UsersFile(root=list(users)))

    def edit_users(self):
        """Transactional edit of users.json.

        `with di.edit_users() as users: users.add(...)` — locks the file, loads
        fresh, saves on clean exit (only if changed). `users` is a UsersFile
        with dict-style helpers (get/contains/add/remove).
        """
        return self.edit_model(self.users_file, UsersFile)

    @staticmethod
    def generate_random_string(length: int = 10) -> str:
        letters = string.ascii_lowercase
        result_str = ''.join(random.choice(letters) for _ in range(length))

        return result_str

    def generate_new_user(self, username: str, password: str) -> User:
        users = self.load_users()
        used_folders = {user.folder for user in users.values()}
        for _ in range(100):
            folder = self.generate_random_string()
            if folder not in used_folders:
                return User.create(username, password, folder)
        raise RuntimeError("Could not generate unique folder")
    
    def generate_metadata_file(self, backup_dir: Path) -> None:
        repo = Repo(".")
        commit_hash = repo.head.commit.hexsha
        data = {
            "commit_hash": commit_hash,
        }
        self.atomic_write(backup_dir / self.metadata_filename, 
                          data=json.dumps(data, indent=4), 
                          mode='w', 
                          encoding='utf-8')

    def generate_backup_dir(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        new_backup = self.backups_directory / timestamp
        new_backup.mkdir(parents=True, exist_ok=True)
        self._prune_backups()
        return new_backup

    def _prune_backups(self) -> None:
        max_count = ConfigManager().backup_max_count
        backups = sorted(p for p in self.backups_directory.iterdir() if p.is_dir())
        for old in backups[:-max_count]:
            shutil.rmtree(old)

    def find_avail_temp_file_path(self, ext: str = "") -> Path:
        dir = ConfigManager().temp_dir
        ext = ext if ext.startswith('.') else f".{ext}"
        for _ in range(100):
            temp_file = dir / f"{self.generate_random_string(10)}{ext}"
            if not temp_file.exists():
                return temp_file
        raise RuntimeError("Could not find available temporary file path")
    
    def create_temp_file(self, ext: str = "") -> Path:
        temp_file = self.find_avail_temp_file_path(ext)
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.touch(exist_ok=True)

        return temp_file
    
    @contextmanager
    def temp_file_ctx(self, ext: str = ""):
        """
        Context manager for creating and cleaning up a temp file.
        Usage:
            with self.temp_file_ctx('.txt') as temp_path:
                # use temp_path
        """
        temp_path = self.create_temp_file(ext)
        try:
            yield temp_path
        finally:
            if temp_path.exists():
                temp_path.unlink()
