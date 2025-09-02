from datetime import datetime, timedelta
from typing import * # type: ignore
from enum import Enum
from pydantic import BaseModel


class GoalState(Enum):
    ACTIVE = 0
    COMPLETED = 1
    FAILED = 2
    BACKLOGGED = 3

class Recurrence(BaseModel):
    start: datetime
    end: datetime
    repeat_period: Optional[timedelta]
    paused: bool

class Goal(BaseModel):
    id: int
    name: str
    state: GoalState
    description: str = ""
    creation_date: datetime = datetime.now()
    completion_date: Optional[datetime] = None
    planned_completion_date: Optional[datetime] = None
    parent: Optional[int] = None
    children: List[int] = []
    recurrence: Optional[Recurrence] = None

class TopLevelData(BaseModel):
    goals: Dict[int, Goal] = {}