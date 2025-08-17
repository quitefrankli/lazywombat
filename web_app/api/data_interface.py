from pathlib import Path

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager
from web_app.users import User


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.data_sub_dirname = "api_data"

    def write_data(self, filename: str, data: bytes, user: User) -> None:
        user_dir = self._get_user_dir(user)
        data_file = user_dir / filename

        self.atomic_write(data_file, data=data, mode="wb")

    def read_data(self, filename: str, user: User) -> bytes:
        user_dir = self._get_user_dir(user)
        data_file = user_dir / filename

        if not data_file.exists():
            raise FileNotFoundError(f"data: {filename} not found for user: {user.id}")

        with open(data_file, 'rb') as file:
            return file.read()

    def delete_data(self, filename: str, user: User) -> None:
        user_dir = self._get_user_dir(user)
        data_file = user_dir / filename

        if not data_file.exists() or not data_file.is_file():
            raise FileNotFoundError(f"data: {filename} not found for user: {user.id}")
        
        data_file.unlink()
        # self.data_syncer.delete_file(data_file)

    def list_files(self, user: User) -> list[str]:
        user_dir = self._get_user_dir(user)
        if not user_dir.exists():
            return []
        return [f.name for f in user_dir.iterdir() if f.is_file()]

    def _get_user_dir(self, user: User) -> Path:
        return ConfigManager().save_data_path / self.data_sub_dirname / user.folder