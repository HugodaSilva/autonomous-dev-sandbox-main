import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

class Status(Enum):
    DRAFT = "Draft"
    RELEASED = "Released"
    DEPRECATED = "Deprecated"

@dataclass
class ApplicationComponent:
    id: str
    type: str  # 'Component' or 'Infra'
    version: str
    spec: dict[str, Any]
    status: Status = Status.DRAFT
    is_deleted: bool = False

    def to_json(self) -> str:
        data = asdict(self)
        data['status'] = self.status.value
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, json_str: str):
        data = json.loads(json_str)
        data['status'] = Status(data['status'])
        return cls(**data)
