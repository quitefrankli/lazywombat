from datetime import datetime
from typing import * # type: ignore
from pydantic import BaseModel


class DataPoint(BaseModel):
    date: datetime
    value: float

class Metric(BaseModel):
    id: int
    name: str
    data: List[DataPoint]
    unit: str
    description: str = ""
    creation_date: datetime = datetime.now()


class TopLevelData(BaseModel):
    metrics: Dict[int, Metric] = {}
