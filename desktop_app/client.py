import requests
import json
import boto3
import os
import datetime

from datetime import datetime
from typing import *

from desktop_app.goal import Goal
from desktop_app.server import Backend


class Client:
    URL = 'https://bavdvowkx7.execute-api.ap-southeast-2.amazonaws.com/prod/todoist2'
    HEADERS = {'x-api-key': 'jM6bHWXdes1Ih1XecNkD519IJr60dcAK2hL7k30O'}

    def __init__(self) -> None:
        pass

    def fetch_goals(self) -> List[Goal]:
        response = requests.get(self.URL, json={'request': 'fetch_goals'}, headers=self.HEADERS, verify=True)
        if response.status_code != 200:
            raise RuntimeError(f"Error in fetch_goals: {response.text}")
        self.goals = [Goal.from_dict(goal) for goal in json.loads(response.text)['goals']]
        return self.goals
    
    def add_goal(self, goal: Goal) -> None:
        self.goals.append(goal)
        request = {
            'request': 'add_goal',
            'goal': goal.to_dict()
        }
        response = requests.get(self.URL, json=request, headers=self.HEADERS, verify=True)
        if response.status_code != 200:
            raise RuntimeError(f"Error in add_goals: {response.text}")

    def delete_goal(self, idx: int) -> None:
        self.goals.pop(idx)
        request = {'request': 'delete_goal', 'goal_id': idx}
        response = requests.get(self.URL, json=request, headers=self.HEADERS, verify=True)
        if response.status_code != 200:
            raise RuntimeError(f"Error in delete_goal: {response.text}")
        
    def toggle_goal_state(self, idx: int) -> None:
        request = {'request': 'toggle_goal_state', 'goal_id': idx}
        response = requests.get(self.URL, json=request, headers=self.HEADERS, verify=True)
        if response.status_code != 200:
            raise RuntimeError(f"Error in toggle_goal_state: {response.text}")
        
    def backup_goals(self) -> None:
        response = requests.get(self.URL, json={'request': 'backup_goals'}, headers=self.HEADERS, verify=True)
        if response.status_code != 200:
            raise RuntimeError(f"Error in backup_goals: {response.text}")
        
class ClientV2:
    BUCKET_NAME = 'todoist2'

    def __init__(self) -> None:
        ACCESS_KEY = 'AKIAV2SIJY7TVPWJ2TOE'
        SECRET_ACCESS_KEY = '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'

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
    
    def add_goal(self, goal: Goal) -> None:
        self.backend.add_goal(goal)
        self.need_saving = True

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

    def backup_goals(self) -> str:
        backup = self.backend.backup_goals()
        s3_obj_name = f".todoist2_backup/{os.path.basename(backup)}"
        self.s3_client.upload_file(backup, self.BUCKET_NAME, s3_obj_name)
        return s3_obj_name

    def save_goals(self) -> None:
        if self.need_saving:
            self.backend.save_goals()
            save_file_name = os.path.basename(Backend.SAVE_FILE)
            self.s3_client.upload_file(Backend.SAVE_FILE, self.BUCKET_NAME, save_file_name)