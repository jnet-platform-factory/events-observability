"""The search API over the event archive. Optional — CreateSearchApi=true.

The forwarder writes; this reads. They share a repository and an index and
nothing else, and the API is entirely absent from a stack that does not ask for
it, so a tenant who only wants forwarding pays nothing for it.

Routes, relative to whatever base path the gateway is mapped at:

    GET  /search        search the archive (filters are all optional)
    GET  /facets        the distinct (source, detail-type) pairs present
    GET  /{event_id}    one archived event
    GET  /openapi.json  this service's generated spec
    POST /emit          publish an event to the bus  (EnableEmitEndpoint=true)

`/search` rather than the collection root `/` on purpose: an HTTP API mapped at a
base path only exposes its root route at `/<base>/`, with the trailing slash, so
a root route is reachable by a URL most clients will not construct.

`/emit` is off by default and is the one route that writes. It exists for
clients that cannot reach EventBridge directly; if yours can, leave it off
rather than putting an authenticated event-injection endpoint on the internet.
"""
from __future__ import annotations

import json
import os
from typing import Annotated

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, CORSConfig, Response
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    InternalServerError,
    NotFoundError,
)
from aws_lambda_powertools.event_handler.openapi.params import Path, Query

from .factories import ServiceFactory
from .schemas import (
    EmitRequest,
    EmitResponse,
    EventFacetsResponse,
    EventRecord,
    EventSearchResponse,
)

logger = Logger(service="events-observability-api")

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

app = APIGatewayHttpResolver(cors=CORSConfig(allow_origin="*", allow_headers=["*"]), enable_validation=True)


def _repository():
    return ServiceFactory.create_repository()


def _json(payload) -> Response:
    return Response(status_code=200, content_type="application/json",
                    body=json.dumps(payload, default=str))


@app.get("/openapi.json", include_in_schema=False)
def serve_openapi_spec() -> Response:
    """The spec, generated from the route signatures rather than hand-maintained."""
    return Response(status_code=200, content_type="application/json",
                    body=app.get_openapi_json_schema(
                        title="Events Observability API",
                        version=os.environ.get("APP_VERSION", "1.0.0"),
                        description="Search the application-event archive.",
                    ))


@app.get("/facets", summary="List distinct event types", tags=["Events"])
def event_facets() -> EventFacetsResponse:
    """Every (source, detail-type) pair in the archive — what an alert can key on."""
    try:
        return EventFacetsResponse(items=_repository().facets())
    except Exception as exc:  # noqa: BLE001
        logger.exception("facets failed")
        raise InternalServerError("Could not read event facets") from exc


@app.get("/search", summary="Search events", tags=["Events"])
def search_events(
    source: Annotated[str | None, Query(description="Exact event source, e.g. billing.invoice")] = None,
    event: Annotated[str | None, Query(description="Exact detail-type, e.g. InvoiceIssued")] = None,
    organization: Annotated[str | None, Query(description="Exact organization id")] = None,
    q: Annotated[str | None, Query(description="Free text, searched across the payload")] = None,
    date_from: Annotated[str | None, Query(alias="from", description="ISO8601 lower bound, inclusive")] = None,
    date_to: Annotated[str | None, Query(alias="to", description="ISO8601 upper bound, inclusive")] = None,
    size: Annotated[int, Query(description=f"Page size, max {MAX_PAGE_SIZE}")] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(description="Offset for paging")] = 0,
) -> EventSearchResponse:
    """Newest first. Every filter is optional; none of them means match all."""
    size = min(max(int(size or DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
    offset = max(int(offset or 0), 0)
    try:
        result = _repository().search(
            source=source, event=event, organization=organization, text=q,
            date_from=date_from, date_to=date_to, size=size, offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("search failed")
        raise InternalServerError("Could not search events") from exc
    return EventSearchResponse(**result)


@app.get("/<event_id>", summary="Get one event", tags=["Events"])
def get_event(event_id: Annotated[str, Path(description="OpenSearch document id")]) -> EventRecord:
    record = _repository().get(event_id)
    if record is None:
        raise NotFoundError(f"Event {event_id} was not found")
    return EventRecord(**record)


@app.post("/emit", summary="Publish an event to the bus", tags=["Events"])
def emit_event() -> EmitResponse:
    """Only routed when EnableEmitEndpoint=true — see the module docstring."""
    if os.environ.get("ENABLE_EMIT_ENDPOINT", "false").lower() != "true":
        raise NotFoundError("Not found")

    bus = os.environ.get("EVENT_BUS_NAME", "")
    if not bus:
        raise InternalServerError("EVENT_BUS_NAME is not configured")

    try:
        request = EmitRequest.model_validate(app.current_event.json_body or {})
    except Exception as exc:  # noqa: BLE001
        raise BadRequestError(f"Invalid request body: {exc}") from exc

    import boto3

    client = boto3.client("events")
    response = client.put_events(Entries=[{
        "EventBusName": bus,
        "Source": request.source,
        "DetailType": request.detail_type,
        "Detail": json.dumps(request.detail or {}, default=str),
    }])
    entry = (response.get("Entries") or [{}])[0]
    if entry.get("ErrorCode"):
        raise InternalServerError(f"EventBridge rejected the event: {entry.get('ErrorCode')}")
    return EmitResponse(published=True, event_id=entry.get("EventId"))


@logger.inject_lambda_context
def lambda_handler(event, context):
    return app.resolve(event, context)
