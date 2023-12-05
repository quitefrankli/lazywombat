import yaml
import datetime

from datetime import datetime
from pathlib import Path
from typing import *
from goal import Goal


class Backend:
    SAVE_FILE = f"{Path.home()}/.todoist2_goals.yaml"
    BACKUP_PREFIX = f"{Path.home()}/.todoist2_backup"

    def __init__(self) -> None:
        self.goals: List[Goal] = self.load_goals()

    def add_goal(self, goal: Goal) -> None:
        self.goals.append(goal)
        self.save_goals()

    def delete_goal(self, idx: int) -> None:
        self.goals.pop(idx)
        self.save_goals()

    def save_goals(self, file_path: str = SAVE_FILE) -> None:
        Path(file_path).parent.mkdir(exist_ok=True, parents=True)
        goals = [{
            'name': goal.name,
            'state': goal.state,
            'metadata': goal.metadata.to_dict()
        } for goal in self.goals]
        data = {
            'goals': goals,
            'edited': int(self.get_datetime_str())
        }
        with open(file_path, 'w') as file:
            yaml.dump(data, file)

    def load_goals(self) -> List[Goal]:
        try:
            with open(self.SAVE_FILE, 'r') as file:
                data = yaml.safe_load(file)
                goals = data['goals']
                return [
                    Goal(goal['name'], 
                         goal['state'],
                         Goal.construct_metadata(goal['metadata'])) for goal in goals]
        except FileNotFoundError:
            return []

    def backup_goals(self) -> None:
        self.save_goals(f"{self.BACKUP_PREFIX}/{self.get_datetime_str()}.yaml")

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

class Server:
    def __init__(self) -> None:
        self.backend = Backend()

    # add the usual server-client API like 'process request'


