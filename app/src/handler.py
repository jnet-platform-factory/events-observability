from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.data_classes import event_source, EventBridgeEvent

from .factories import ServiceFactory

# Service name is taken from POWERTOOLS_SERVICE_NAME (set in the template).
logger = Logger()


# -----------------------------
# Lambda handler
# -----------------------------
EXCLUDED_DETAIL_TYPES = {"WarmupLambda"}


@logger.inject_lambda_context(log_event=True)
@event_source(data_class=EventBridgeEvent)
def lambda_handler(event: EventBridgeEvent, context):
    """
    Pure business logic handler: saves the EventBridge event to OpenSearch.
    """
    if event.detail_type in EXCLUDED_DETAIL_TYPES:
        logger.info(f"Skipping excluded event type: {event.detail_type}")
        return True

    app_event = ServiceFactory.create_app_event_entity(event)
    logger.info(f"Processing event: {app_event.to_dict()}")

    events_service = ServiceFactory.create_app_events_forwarder_service(app_event.organization_user_context)
    saved_event = events_service.save(app_event)
    logger.info(f"Saved event: {saved_event.to_dict()}")
    return True
