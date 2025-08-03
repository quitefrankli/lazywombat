import boto3
import logging
import json
import random
import string
import os
import shutil

from botocore.exceptions import ClientError
from pathlib import Path
from datetime import datetime
from typing import * # type: ignore

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
        return str(file.relative_to(ConfigManager.PROJECT_LOCAL_SAVE_DIRECTORY).as_posix())

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
    BACKUPS_DIRECTORY = ConfigManager.PROJECT_LOCAL_SAVE_DIRECTORY.parent / "backups"
    USERS_FILE = ConfigManager.PROJECT_LOCAL_SAVE_DIRECTORY / "users.json"

    def __init__(self) -> None:
        self.data_syncer = DataSyncer.instance()
    
    def load_users(self) -> Dict[str, User]:
        self.data_syncer.download_file(self.USERS_FILE)

        if not self.USERS_FILE.exists():
            return {}

        with open(self.USERS_FILE, 'r') as file:
            users_data: list = json.load(file)
            users = [User.from_dict(user) for user in users_data]

        return {user.id: user for user in users}

    def save_users(self, users: List[User]) -> None:
        self.USERS_FILE.parent.mkdir(exist_ok=True, parents=True)
        with open(self.USERS_FILE, 'w', encoding='utf-8') as file:
            json.dump([user.to_dict() for user in users], file, indent=4)
        self.data_syncer.upload_file(self.USERS_FILE)

    def generate_new_user(self, username: str, password: str) -> User:
        users = self.load_users()
        used_folders = {user.folder for user in users.values()}
        def _generate_random_string() -> str:
            letters = string.ascii_lowercase
            result_str = ''.join(random.choice(letters) for _ in range(10))
            return result_str
        for _ in range(100):
            folder = _generate_random_string()
            if folder not in used_folders:
                return User(username, password, folder)
        raise RuntimeError("Could not generate unique folder")
    
    def backup_data(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        new_backup = self.BACKUPS_DIRECTORY / timestamp
        shutil.copytree(ConfigManager.PROJECT_LOCAL_SAVE_DIRECTORY, new_backup)
        # TODO: zip the backup and upload to s3
        # self.data_syncer.upload_file(new_backup)