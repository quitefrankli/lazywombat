import boto3
import os

from typing import *

from desktop_app.goal import Goal
from desktop_app.server import Backend


class ClientV2:
    BUCKET_NAME = 'todoist2'

    def __init__(self, debug: bool) -> None:
        ACCESS_KEY = 'AKIAV2SIJY7TVPWJ2TOE'
        SECRET_ACCESS_KEY = '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'
        self.debug = debug
        if not debug:
            self.s3_client = boto3.client('s3', 
                                        aws_access_key_id=ACCESS_KEY, 
                                        aws_secret_access_key=SECRET_ACCESS_KEY)
            try:
                save_file_name = os.path.basename(Backend.SAVE_FILE)
                self.s3_client.download_file(self.BUCKET_NAME, save_file_name, Backend.SAVE_FILE)
            except boto3.ClientError as e:
                print(f"could not download save file from s3: {e}")

        self.backend = Backend()
        self.need_saving = False

    def fetch_goals(self) -> List[Goal]:
        return self.backend.get_goals()
    
    def fetch_goal(self, goal_id: int) -> Goal:
        return self.backend.get_goal(goal_id)
    
    def add_goal(self, goal: Goal) -> int:
        goal_id = self.backend.add_goal(goal)
        self.need_saving = True
    
        return goal_id

    def delete_goal(self, idx: int) -> None:
        self.backend.delete_goal(idx)
        self.need_saving = True
        
    def toggle_goal_state(self, idx: int) -> None:
        self.backend.toggle_goal_state(idx)
        self.need_saving = True
        
    def toggle_goal_daily(self, goal_id: int) -> None:
        goal = self.fetch_goal(goal_id)
        goal.toggle_daily()
        self.need_saving = True

    def unparent_goal(self, goal_id: int) -> None:
        self.backend.unparent_goal(goal_id)
        self.need_saving = True

    def remove_goal_from_hierarchy(self, goal_id: int) -> None:
        self.backend.remove_goal_from_hierarchy(goal_id)
        self.need_saving = True

    def setup_parent(self, child_id: int, parent_id: int) -> None:
        self.backend.setup_parent(child_id, parent_id)
        self.need_saving = True

    def backup_goals(self) -> str:
        backup = self.backend.backup_goals()
        s3_obj_name = f".todoist2_backup/{os.path.basename(backup)}"
        if not self.debug:
             self.s3_client.upload_file(backup, self.BUCKET_NAME, s3_obj_name)
        return s3_obj_name
    
    def get_failed_goals(self) -> List[Goal]:
        return self.backend.get_failed_goals()

    def save_goals(self) -> None:
        if self.need_saving:
            self.backend.save_goals()
            if not self.debug:
                save_file_name = os.path.basename(Backend.SAVE_FILE)
                self.s3_client.upload_file(Backend.SAVE_FILE, self.BUCKET_NAME, save_file_name)