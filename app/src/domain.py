"""The document written to OpenSearch, and the repository contract.

The declared fields are the ones every tenant's document carries and which the
generic half depends on. Everything a tenant promotes on top of them arrives as
an extra — see `shaping.py` — which is why `extra="allow"` is load-bearing rather
than lax: it is the mechanism by which a tenant adds a field without adding code.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class AppEventModel(BaseModel):
    # Promoted fields from ShapingConfig land here. They are included by
    # model_dump(), so they reach OpenSearch as real top-level fields — which is
    # the whole point: `payload` below is serialised to a JSON *string*, so
    # nothing inside it can be aggregated. A `sum` needs a top-level field.
    model_config = ConfigDict(extra="allow")

    event_id: Optional[str] = None
    source: str
    event: str
    payload: Dict[str, Any]
    environment: str
    application: str
    organization: str
    username: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    @field_serializer("payload")
    def serialize_payload(self, payload: Dict[str, Any], _info) -> str:
        """Stored as a JSON string so OpenSearch never tries to map a wild,
        per-event detail shape into a rigid index mapping."""
        return json.dumps(payload, ensure_ascii=False)

    def to_dict(self) -> Dict[str, Any]:
        """Ready for OpenSearch. model_dump applies the field_serializer."""
        return self.model_dump()

    def to_json(self) -> str:
        return self.model_dump_json()


class AppEventsAnalyticsRepository(Protocol):
    def save_event(self, event: AppEventModel) -> AppEventModel:
        ...

    def set_context(self, *, environment: str, application: str) -> None:
        ...
