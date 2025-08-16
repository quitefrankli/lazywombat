from werkzeug.datastructures import FileStorage
from pathlib import Path

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.config import ConfigManager
from web_app.users import User


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.cheapify_data_directory = ConfigManager().save_data_path / "cheapify"

    def save_file(self, file_storage: FileStorage, user: User):
        user_dir = self._get_user_dir(user)
        user_dir.mkdir(parents=True, exist_ok=True)
        filename = str(file_storage.filename)
        file_path = user_dir / filename

        self.atomic_write(file_path, stream=file_storage.stream, mode="wb")

        return file_path

    def list_files(self, user: User) -> list[str]:
        user_dir = self._get_user_dir(user)
        if not user_dir.exists():
            return []
        
        return [f.name for f in user_dir.iterdir() if f.is_file()]

    def get_file_path(self, filename: str, user: User) -> Path:
        return self._get_user_dir(user) / filename

    def delete_file(self, filename: str, user: User) -> bool:
        file_path = self.get_file_path(filename, user)
        if file_path.exists() and file_path.is_file():
            file_path.unlink() # unlink means to delete as well
            # self.data_syncer.delete_file(file_path)
            return True

        return False

    def _get_user_dir(self, user: User) -> Path:
        return self.cheapify_data_directory / user.folder