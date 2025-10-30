import json
import shutil

from pathlib import Path
from datetime import datetime
from typing import * # type: ignore

from web_app.users import User
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.todoist2.app_data import Goals
from web_app.config import ConfigManager


class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.todoist2_data_directory = ConfigManager().save_data_path / "todoist2"

    def load_data(self, user: User) -> Goals:
        data_path = self._get_data_file(user)
        self.data_syncer.download_file(data_path)
        if not data_path.exists():
            return Goals(goals={})
        
        with open(data_path, 'r') as file:
            data = json.load(file)

        # TODO: delete this code once all users have last_modified populated
        goals = Goals(**data)
        for goal in goals.goals.values():
            if goal.last_modified is None:
                goal.last_modified = goal.creation_date

        return goals
            
    def save_data(self, data: Goals, user: User) -> None:
        data_file = self._get_data_file(user)
        self.atomic_write(data_file, 
                          data=data.model_dump_json(indent=4, exclude_none=True), 
                          mode="w", 
                          encoding='utf-8')
        
    def backup_data(self, backup_dir: Path) -> None:
        shutil.copytree(self.todoist2_data_directory, backup_dir / "todoist2")

    def _get_data_file(self, user: User) -> Path:
        return self.todoist2_data_directory / user.folder / "data.json"