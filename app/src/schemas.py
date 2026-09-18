"""Request and response models for the search API.

Separate from `domain.AppEventModel`, deliberately. That model is the *write*
shape -- what the forwarder indexes, with `payload` serialised to a JSON string
so OpenSearch never tries to map a per-event detail. These are the *read* shapes,
where `payload` is a real object again because an API client wants JSON, not a
string containing JSON.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_EXAMPLE = {
    "event_id": "139a6J4BXdfoGByyjitg",
    "source": "billing.invoice",
    "event": "InvoiceIssued",
    "organization": "acme",
    "username": "jdoe",
    "environment": "prod",
    "application": "billing",
    "timestamp": "2026-06-21T04:05:09.167571Z",
    "payload": {"invoice_id": "INV-9", "total": 1200},
}


class EventRecord(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": _EXAMPLE}, extra="allow")

    event_id: str | None = None
    source: str | None = None
    event: str | None = None
    organization: str | None = None
    username: str | None = None
    environment: str | None = None
    application: str | None = None
    timestamp: str | None = None
    payload: dict[str, Any] | None = None


class EventSearchResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"items": [_EXAMPLE], "total": 1}})

    items: list[EventRecord] = Field(default_factory=list)
    total: int = 0


class EventFacet(BaseModel):
    source: str | None = None
    detail_type: str | None = None
    count: int = 0


class EventFacetsResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"items": [{"source": "billing.invoice", "detail_type": "InvoiceIssued", "count": 42}]}
        }
    )

    items: list[EventFacet] = Field(default_factory=list)


class EmitRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source": "billing.invoice",
                "detail_type": "InvoiceIssued",
                "detail": {"invoice_id": "INV-9", "organization": "acme", "username": "jdoe"},
            }
        }
    )

    source: str
    detail_type: str
    detail: dict[str, Any] = Field(default_factory=dict)


class EmitResponse(BaseModel):
    published: bool = True
    event_id: str | None = None


class ErrorResponse(BaseModel):
    message: str
