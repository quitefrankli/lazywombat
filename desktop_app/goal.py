from dataclasses import dataclass
from datetime import datetime
from typing import *


class Goal:
    NULL_DATE = datetime(year=2000, month=1, day=1)

    @dataclass
    class Metadata:
        creation_date: datetime
        completion_date: Optional[datetime]

        @classmethod
        def from_dict(cls, data: dict):
            def foo(key: str):
                return datetime.fromtimestamp(data[key]) if data[key] else None
            return cls(foo('creation_date'), foo('completion_date'))

        def to_dict(self) -> dict:
            return {
                'creation_date': int(self.creation_date.timestamp()) if self.creation_date else 0,
                'completion_date': int(self.completion_date.timestamp()) if self.completion_date else 0
            }

    def __init__(self, 
                 id: int,
                 name: str, 
                 state: bool, 
                 parent: int = -1,
                 backlogged: bool = False,
                 daily: datetime = NULL_DATE,
                 description: str = '',
                 collapsed: bool = False,
                 metadata: Optional[Metadata] = None):
        self.id = id
        self.name = name
        self.state = state
        self.parent = parent
        self.backlogged = backlogged
        self.daily = daily
        self.description = description
        self.collapsed = collapsed
        self.metadata = metadata if metadata else self.Metadata(datetime.now(), None)

        self.children: List[int] = []
        self.repeat: int = 0 # in days
        self.paused = False # only for repeat parent goals
        self.repeat_end_date = self.NULL_DATE

    @classmethod
    def from_dict(cls, data: dict):
        def safe_get(key: str, alt: Any) -> Any:
            return data[key] if key in data else alt
        daily = datetime.fromtimestamp(data['daily']) if 'daily' in data else cls.NULL_DATE
        goal = cls(data['id'],
                   data['name'], 
                   data['state'], 
                   safe_get('parent', -1),
                   safe_get('backlogged', False),
                   daily,
                   safe_get('description', ''),
                   safe_get('collapsed', False),
                   cls.Metadata.from_dict(data['metadata']))
        goal.repeat = safe_get('repeat', 0)
        goal.paused = safe_get('paused', False)
        goal.repeat_end_date = datetime.fromtimestamp(data['repeat_end_date']) if 'repeat_end_date' in data else cls.NULL_DATE

        return goal

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'state': self.state,
            'parent': self.parent,
            # 'children': self.children, note that this is intentionally omitted
            'backlogged': self.backlogged,
            'daily': int(self.daily.timestamp()),
            'description': self.description,
            'collapsed': self.collapsed,
            'repeat': self.repeat,
            'metadata': self.metadata.to_dict(),
            'paused': self.paused,
            'repeat_end_date': int(self.repeat_end_date.timestamp())
        }
    
    def toggle_daily(self) -> None:
        now = datetime.now()
        self.daily = Goal.NULL_DATE if self.daily.date() == now.date() else now