import yaml
import datetime
import os
import json

from datetime import datetime
from pathlib import Path
from typing import *
from goal import Goal

SAVE_DIRECTORY = Path.home() if os.name == 'nt' else '/tmp'

class Backend:
    SAVE_FILE = f"{SAVE_DIRECTORY}/.todoist2_goals.yaml"
    BACKUP_DIR = f"{SAVE_DIRECTORY}/.todoist2_backup"

    def __init__(self) -> None:
        self.goals: List[Goal] = self.load_goals()

    def add_goal(self, goal: Goal) -> None:
        self.goals.append(goal)
        self.save_goals()

    def delete_goal(self, idx: int) -> None:
        self.goals.pop(idx)
        self.save_goals()

    def _goals_to_obj(self):
        data = {
            'goals': [goal.to_dict() for goal in self.goals],
            'edited': int(self.get_datetime_str())
        }
        return data

    def save_goals(self, file_path: str = SAVE_FILE) -> None:
        Path(file_path).parent.mkdir(exist_ok=True, parents=True)
        with open(file_path, 'w') as file:
            yaml.dump(self._goals_to_obj(), file)

    def load_goals(self) -> List[Goal]:
        try:
            with open(self.SAVE_FILE, 'r') as file:
                data = yaml.safe_load(file)
                return [Goal.from_dict(goal) for goal in data['goals']]
        except FileNotFoundError:
            return []
        
    def backup_goals(self) -> str:
        backup = f"{self.BACKUP_DIR}/{self.get_datetime_str()}.yaml"
        self.save_goals(backup)
        return backup

    def get_datetime_str(self) -> str:
        return datetime.now().strftime("%Y%m%d%H%M%S")

    def toggle_goal_state(self, idx: int) -> None:
        goal = self.goals[idx]
        goal.state = not goal.state
        if goal.state:
            goal.metadata.completion_date = datetime.now()
        else:
            goal.metadata.completion_date = None
        self.save_goals()
    
    def get_goals(self) -> List[Goal]:
        return self.goals
    
    def get_goals_json(self) -> str:
        return json.dumps(self._goals_to_obj())

class Server:
    def __init__(self) -> None:
        self.backend = Backend()

    # add the usual server-client API like 'process request'


