import json
from datetime import datetime
from typing import Any, Dict, Optional, Protocol

from pydantic import BaseModel, Field, field_serializer


class AppEventModel(BaseModel):
    """
    Concrete event for OpenSearch.
    """
    event_id: Optional[str] = None
    source: str
    event: str
    payload: Dict[str, Any]
    environment: str
    application: str
    organization: str
    username: str
    organization_user_context: Dict[str, Any] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    @field_serializer('payload')
    def serialize_payload(self, payload: Dict[str, Any], _info) -> str:
        """Serialize payload to JSON string for OpenSearch."""
        return json.dumps(payload, ensure_ascii=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary ready for OpenSearch (applies field_serializer)."""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert the event to a JSON string for OpenSearch."""
        return self.model_dump_json()


class AppEventsAnalyticsRepository(Protocol):
    def save_event(self, event: AppEventModel) -> AppEventModel:
        ...

    def set_context(self, *, environment: str, application: str) -> None:
        ...
