from typing import *
from goal import Goal

# this is a temporary hack
from server import Backend
backend = Backend()

class Client:
	def __init__(self) -> None:
		pass

	def fetch_goals(self) -> List[Goal]:
		return backend.get_goals()
	
	def add_goal(self, goal: Goal) -> None:
		backend.add_goal(goal)

	def delete_goal(self, idx: int) -> None:
		backend.delete_goal(idx)

	def toggle_goal_state(self, idx: int) -> None:
		backend.toggle_goal_state(idx)

	def backup_goals(self) -> None:
		backend.backup_goals()