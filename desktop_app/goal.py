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
                 metadata: Optional[Metadata] = None):
        self.id = id
        self.name = name
        self.state = state
        self.parent = parent
        self.backlogged = backlogged
        self.daily = daily
        self.description = description
        self.metadata = metadata if metadata else self.Metadata(datetime.now(), None)
    
        self.children: List[int] = []
        self.collapsed = False

    @classmethod
    def from_dict(cls, data: dict):
        def safe_get(key: str, alt: Any) -> Any:
            return data[key] if key in data else alt
        daily = datetime.fromtimestamp(data['daily']) if 'daily' in data else cls.NULL_DATE
        return cls(data['id'],
                   data['name'], 
                   data['state'], 
                   safe_get('parent', -1),
                   safe_get('backlogged', False),
                   daily,
                   safe_get('description', ''),
                   cls.Metadata.from_dict(data['metadata']))

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
            'metadata': self.metadata.to_dict()
        }
    
    def toggle_daily(self) -> None:
        now = datetime.now()
        self.daily = Goal.NULL_DATE if self.daily.date() == now.date() else now