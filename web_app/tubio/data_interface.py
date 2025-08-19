from werkzeug.datastructures import FileStorage
from pathlib import Path

from web_app.api.data_interface import DataInterface as BaseDataInterface
from web_app.users import User


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.data_sub_dirname = "tubio"

    def save_file(self, file_storage: FileStorage, user: User) -> None:
        user_dir = self._get_user_dir(user)
        file_path = user_dir / str(file_storage.filename)

        self.atomic_write(file_path, stream=file_storage.stream, mode="wb")

    def get_file_path(self, filename: str, user: User) -> Path:
        return self._get_user_dir(user) / filename