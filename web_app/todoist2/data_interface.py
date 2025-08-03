import json

from pathlib import Path
from datetime import datetime
from typing import * # type: ignore

from web_app.users import User
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.todoist2.app_data import TopLevelData
from web_app.config import ConfigManager


class DataInterface(BaseDataInterface):
    TODOIST2_DATA_DIRECTORY = ConfigManager.PROJECT_LOCAL_SAVE_DIRECTORY / "todoist2"

    def get_data_file(self, user: User) -> Path:
        return self.TODOIST2_DATA_DIRECTORY / user.folder / "data.json"

    def load_data(self, user: User) -> TopLevelData:
        data_path = self.get_data_file(user)
        self.data_syncer.download_file(data_path)
        if not data_path.exists():
            return TopLevelData(goals={}, edited=datetime.now())
        
        with open(data_path, 'r') as file:
            data = json.load(file)

        return TopLevelData(**data)
            
    def save_data(self, data: TopLevelData, user: User) -> None:
        data_file = self.get_data_file(user)
        data_file.parent.mkdir(exist_ok=True, parents=True)
        with open(data_file, 'w', encoding='utf-8') as file:
            file.write(data.model_dump_json(indent=4))
        self.data_syncer.upload_file(data_file)