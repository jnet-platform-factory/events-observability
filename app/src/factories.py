import os
from typing import Any, Dict

from aws_lambda_powertools.utilities.data_classes import EventBridgeEvent

from .application import AppEventsForwarderService
from .domain import AppEventModel
from .repositories import OpenSearchAppEventsRepository

OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT")
OPENSEARCH_REGION = os.getenv("OPENSEARCH_REGION", "us-east-1")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "platform-events")
# "es" for a managed OpenSearch domain, "aoss" for OpenSearch Serverless.
OPENSEARCH_SERVICE = os.getenv("OPENSEARCH_SERVICE", "es")


class ServiceFactory:
    @staticmethod
    def create_app_events_forwarder_service(context: Dict[str, Any] = None) -> AppEventsForwarderService:
        repository = OpenSearchAppEventsRepository(
            endpoint=OPENSEARCH_ENDPOINT,
            region=OPENSEARCH_REGION,
            index=OPENSEARCH_INDEX,
            service=OPENSEARCH_SERVICE,
        )
        return AppEventsForwarderService(repository=repository, context=context)

    @staticmethod
    def create_app_event_entity(event: EventBridgeEvent) -> AppEventModel:
        detail = event.detail

        app_event = AppEventModel(
            environment=detail.get("environment", "unknown"),
            organization_user_context={
                "username": detail.get("username") or "default_user",
                "organization": detail.get("organization") or "default_org",
            },
            application=detail.get("application", "unknown"),
            organization=detail.get("organization") or "default_org",
            username=detail.get("username") or "default_user",
            source=event.source,
            event=event.detail_type,
            payload=detail,  # the entire detail object as payload
        )
        return app_event
