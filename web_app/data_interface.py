import boto3
import logging
import json
import random
import string
import os
import shutil

from atomicwrites import atomic_write as _atomic_write
from botocore.exceptions import ClientError
from pathlib import Path
from datetime import datetime
from typing import * # type: ignore
from contextlib import contextmanager

from web_app.users import User
from web_app.config import ConfigManager


class _S3Client:
    BUCKET_NAME = 'todoist2'
    
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
        logging.info(f"Downloading {self._get_s3_path(file)} from s3 to {file}")
        if not file.parent.exists():
            file.parent.mkdir(exist_ok=True, parents=True)
        try:
            self.s3_client.download_file(self.BUCKET_NAME, self._get_s3_path(file), str(file))
        except ClientError as e:
            if e.response['Error']['Code'] == "404":
                logging.warning(f"File {file} not found in s3")
            else:
                raise

    def upload_file(self, file: Path) -> None:
        logging.info(f"Uploading {self._get_s3_path(file)} to s3 from {file}")
        self.s3_client.upload_file(str(file), self.BUCKET_NAME, self._get_s3_path(file))

class _OfflineClient:
    def download_file(self, file: Path) -> None:
        pass

    def upload_file(self, file: Path) -> None:
        pass

class DataSyncer:
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
    
    def __init__(self, client: Union[_S3Client, _OfflineClient]) -> None:
        self.client = client

    def download_file(self, file: Path) -> None:
        self.client.download_file(file)

    def upload_file(self, file: Path) -> None:
        self.client.upload_file(file)


class DataInterface:
    def __init__(self) -> None:
        self.data_syncer = DataSyncer.instance()
        self.backups_directory = ConfigManager().save_data_path.parent / "backups"
        self.users_file = ConfigManager().save_data_path / "users.json"
    
    def load_users(self) -> Dict[str, User]:
        self.data_syncer.download_file(self.users_file)

        if not self.users_file.exists():
            return {}

        with open(self.users_file, 'r') as file:
            users_data: list = json.load(file)
            users = [User.from_dict(user) for user in users_data]

        return {user.id: user for user in users}

    def save_users(self, users: List[User]) -> None:
        json_str = json.dumps([user.to_dict() for user in users], indent=4)
        self.atomic_write(self.users_file, data=json_str, mode='w', encoding='utf-8')

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
                return User(username, password, folder)
        raise RuntimeError("Could not generate unique folder")
    
    def backup_data(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        new_backup = self.backups_directory / timestamp
        shutil.copytree(ConfigManager().save_data_path, new_backup)
        # TODO: zip the backup and upload to s3
        # self.data_syncer.upload_file(new_backup)

    def atomic_write(self, file_path: Path, data: bytes|str|None=None, stream: IO|None=None, **kwargs) -> None:
        if stream is None and data is None:
            raise ValueError("Either 'data' or 'stream' must be provided")
        file_path.parent.mkdir(exist_ok=True, parents=True)
        with _atomic_write(file_path, overwrite=True, **kwargs) as f:
            if data is not None:
                f.write(data)
            if stream is not None:
                CHUNK_SIZE = 1024 * 1024  # 1 MB
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
        # self.data_syncer.upload_file(file_path)

    def atomic_delete(self, file_path: Path) -> None:
        if file_path.exists():
            file_path.unlink()
        # self.data_syncer.upload_file(file_path)  # Not needed for deletion

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
