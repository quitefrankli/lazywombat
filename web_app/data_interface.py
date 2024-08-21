import boto3
import logging
import json
import random
import string

from botocore.exceptions import ClientError
from pathlib import Path
from datetime import datetime
from typing import *

from web_app.users import User
from web_app.app_data import TopLevelData


LOCAL_SAVE_DIRECTORY = Path.home() / ".todoist2"
USERS_FILE = LOCAL_SAVE_DIRECTORY / "users.json"

def _get_data_file(user: User) -> Path:
    return LOCAL_SAVE_DIRECTORY / user.folder / "data.json"

def _get_backup_dir(user: User) -> Path:
    return LOCAL_SAVE_DIRECTORY / user.folder / "backups"

def _generate_random_string() -> str:
    letters = string.ascii_lowercase
    result_str = ''.join(random.choice(letters) for _ in range(10))
    return result_str

class _S3Client:
    BUCKET_NAME = 'todoist2'
    
    def __init__(self) -> None:
        ACCESS_KEY = 'AKIAV2SIJY7TVPWJ2TOE'
        SECRET_ACCESS_KEY = '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'
        self.s3_client = boto3.client('s3', 
                                      aws_access_key_id=ACCESS_KEY, 
                                      aws_secret_access_key=SECRET_ACCESS_KEY)

    @staticmethod
    def _get_s3_path(file: Path) -> str:
        return str(file.relative_to(LOCAL_SAVE_DIRECTORY).as_posix())

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

class _DebugS3Client(_S3Client):
    def __init__(self) -> None:
        self.s3_client = None

    def download_file(self, file: Path) -> None:
        pass

    def upload_file(self, file: Path) -> None:
        pass

class DataInterface:
    @classmethod
    def create_instance(cls, debug: bool) -> 'DataInterface':
        cls._instance = DataInterface(debug)
    
    @classmethod
    def instance(cls) -> 'DataInterface':
        return cls._instance

    def __init__(self, debug: bool) -> None:
        self.s3_client = _DebugS3Client() if debug else _S3Client()

    def load_data(self, user: User) -> TopLevelData:
        data_file = _get_data_file(user)
        self.s3_client.download_file(data_file)
        if not data_file.exists():
            return TopLevelData(goals={}, edited=datetime.now())
        
        with open(data_file, 'r') as file:
            data = json.load(file)

        return TopLevelData(**data)
    
    def load_users(self) -> Dict[str, User]:
        self.s3_client.download_file(USERS_FILE)

        if not USERS_FILE.exists():
            return {}

        with open(USERS_FILE, 'r') as file:
            users_data: list = json.load(file)
            users = [User.from_dict(user) for user in users_data]

        return {user.id: user for user in users}

    def generate_new_user(self, username: str, password: str) -> User:
        users = self.load_users()
        used_folders = {user.folder for user in users.values()}
        for _ in range(100):
            folder = _generate_random_string()
            if folder not in used_folders:
                return User(username, password, folder)

        raise RuntimeError("Could not generate unique folder")

    def backup_data(self, data: TopLevelData, user: User) -> None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_file = _get_backup_dir(user) / f"{timestamp}.json"
        backup_file.parent.mkdir(exist_ok=True, parents=True)
        with open(backup_file, 'w', encoding='utf-8') as file:
            file.write(data.model_dump_json(indent=4))
        self.s3_client.upload_file(backup_file)
        
    def save_data(self, data: TopLevelData, user: User) -> None:
        data_file = _get_data_file(user)
        data_file.parent.mkdir(exist_ok=True, parents=True)
        with open(data_file, 'w', encoding='utf-8') as file:
            file.write(data.model_dump_json(indent=4))
        self.s3_client.upload_file(data_file)

    def save_users(self, users: List[User]) -> None:
        USERS_FILE.parent.mkdir(exist_ok=True, parents=True)
        with open(USERS_FILE, 'w', encoding='utf-8') as file:
            json.dump([user.to_dict() for user in users], file, indent=4)
        self.s3_client.upload_file(USERS_FILE)