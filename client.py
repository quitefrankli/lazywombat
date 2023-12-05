import requests
import json

from typing import *
from goal import Goal

# this is a temporary hack
from server import Backend
backend = Backend()

class Client:
	URL = 'https://en8k6sia65.execute-api.ap-southeast-2.amazonaws.com/default/helloworld'

	def __init__(self) -> None:
		pass

	def fetch_goals(self) -> List[Goal]:
		payload = {
			'request': 'fetch_goals'
		}
		response = requests.get(self.URL, json=payload, verify=True)
		goals_str_obj = json.loads(response.text)['goals']
		self.goals = [
			Goal(goal['name'], 
				 goal['state'],
				 Goal.construct_metadata(goal['metadata'])) for goal in goals_str_obj]
		return self.goals
	
	def add_goal(self, goal: Goal) -> None:
		backend.add_goal(goal)

	def delete_goal(self, idx: int) -> None:
		backend.delete_goal(idx)

	def toggle_goal_state(self, idx: int) -> None:
		requests.get(self.URL, json={'request': 'toggle_goal_state', 'goal_id': idx}, verify=True)

	def backup_goals(self) -> None:
		backend.backup_goals()