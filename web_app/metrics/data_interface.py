import json

from pathlib import Path
from datetime import datetime
from typing import * # type: ignore

from web_app.users import User
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.metrics.app_data import TopLevelData
from web_app.config import ConfigManager


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.metrics_data_directory = ConfigManager().save_data_path / "metrics"

    def load_data(self, user: User) -> TopLevelData:
        data_path = self._get_data_file(user)
        self.data_syncer.download_file(data_path)
        if not data_path.exists():
            return TopLevelData(metrics={})
        
        with open(data_path, 'r') as file:
            data = json.load(file)

        return TopLevelData(**data)
            
    def save_data(self, data: TopLevelData, user: User) -> None:
        data_file = self._get_data_file(user)
        self.atomic_write(data_file, data=data.model_dump_json(indent=4), mode="w", encoding='utf-8')

    def _get_data_file(self, user: User) -> Path:
        return self.metrics_data_directory / user.folder / "data.json"
