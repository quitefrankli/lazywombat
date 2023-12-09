from dataclasses import dataclass
from datetime import datetime


class Goal:
    @dataclass
    class Metadata:
        creation_date: datetime
        completion_date: datetime

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
                 metadata: Metadata = None, 
                 backlogged: bool = False):
        self.id = id
        self.name = name
        self.state = state
        self.metadata = metadata if metadata else self.Metadata(datetime.now(), None)
        self.backlogged = backlogged
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(data['id'],
                   data['name'], 
                   data['state'], 
                   cls.Metadata.from_dict(data['metadata']))

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'state': self.state,
            'metadata': self.metadata.to_dict()
        }