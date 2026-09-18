"""The function's entry point, for both delivery modes."""
import base64

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.data_classes import EventBridgeEvent, event_source
from aws_lambda_powertools.utilities.data_classes.kinesis_firehose_event import (
    KinesisFirehoseDataTransformationRecord,
    KinesisFirehoseDataTransformationResponse,
    KinesisFirehoseEvent,
    KinesisFirehoseRecord,
)

from .factories import ServiceFactory, shaping_config
from .shaping import shape

logger = Logger(service="events-observability")

# Warmup pings are not platform events. Filtered at the rule as well; this is the
# belt to that pair of braces, and it is cheap.
EXCLUDED_DETAIL_TYPES = {"WarmupLambda"}


def lambda_handler(event, context):
    """The only entry point. Dispatches on the shape of the payload.

    Two things can invoke this function, and which one does is not knowable from
    this stack:

      - an EventBridge rule, per event, with an EventBridge envelope
        (DeliveryMode=Lambda)
      - Firehose, per batch, as a data transformation (DeliveryMode=Firehose)

    Sniffing the payload rather than selecting a Handler from DeliveryMode is
    deliberate, and it is about deploy ordering. Where the rule and the function
    live in different stacks — which is the normal case, and the one
    CreateForwardingRule=false exists for — the two deploy separately, so any
    order between them leaves a window in which the rule is still invoking
    per-event while the Handler already expects Firehose records. Every event in
    that window would raise, exhaust the rule's retries, and be lost. One entry
    point that recognises both payloads has no such window.

    The Firehose transformation payload is unmistakable: `deliveryStreamArn` and
    `records` together appear in nothing EventBridge sends.
    """
    if isinstance(event, dict) and "deliveryStreamArn" in event and "records" in event:
        return firehose_transform(event, context)
    return eventbridge_handler(event, context)


@logger.inject_lambda_context(log_event=True)
@event_source(data_class=EventBridgeEvent)
def eventbridge_handler(event: EventBridgeEvent, context):
    """The per-event path: shape one envelope and index it directly."""
    if event.detail_type in EXCLUDED_DETAIL_TYPES:
        logger.info(f"Skipping excluded event type: {event.detail_type}")
        return True

    app_event = shape(event, shaping_config())
    logger.debug("Shaped event", extra={"document": app_event.to_dict()})

    # Present only if the tenant's field map declares it under `nested`.
    user_context = getattr(app_event, "organization_user_context", None)
    events_service = ServiceFactory.create_app_events_forwarder_service(user_context)
    events_service.save(app_event)
    return True


@event_source(data_class=KinesisFirehoseEvent)
def firehose_transform(event: KinesisFirehoseEvent, context):
    """The batched path: shape a Firehose batch, let Firehose do the `_bulk` write.

    Reached through `lambda_handler`'s dispatch, never wired directly as the
    Lambda Handler — see the docstring there for why.

    The transformation contract is per-record, not per-batch: every input
    recordId MUST come back exactly once, tagged Ok, Dropped or ProcessingFailed.
    Raising out of this function fails the whole batch, so one malformed event
    would block every well-formed event travelling with it. That is why every
    branch below returns a record rather than propagating.
    """
    response = KinesisFirehoseDataTransformationResponse()
    for record in event.records:
        response.add_record(_shape_record(record))
    return response.asdict()


def _shape_record(record: KinesisFirehoseRecord) -> KinesisFirehoseDataTransformationRecord:
    """Apply the same shaping the Lambda path uses to one Firehose record."""
    try:
        envelope = record.data_as_json
    except Exception:
        # Not JSON at all, so nothing downstream can use it. The original bytes
        # are kept rather than re-encoded, so the S3 backup copy is the record as
        # it arrived and can be replayed.
        logger.exception("Record is not valid JSON", extra={"record_id": record.record_id})
        return KinesisFirehoseDataTransformationRecord(
            record_id=record.record_id, result="ProcessingFailed", data=record.data
        )

    detail_type = envelope.get("detail-type")
    if detail_type in EXCLUDED_DETAIL_TYPES:
        logger.info(f"Dropping excluded event type: {detail_type}")
        return KinesisFirehoseDataTransformationRecord(
            record_id=record.record_id, result="Dropped", data=record.data
        )

    try:
        app_event = shape(EventBridgeEvent(envelope), shaping_config())
        # In the Lambda path event_id is backfilled from OpenSearch's generated
        # _id after the write. Firehose does the writing, so that value is not
        # knowable here; the EventBridge event id is used instead, which is more
        # useful anyway because it correlates the indexed document with the
        # put_events call and with any S3 or DLQ copy.
        app_event.event_id = envelope.get("id")
        return KinesisFirehoseDataTransformationRecord(
            record_id=record.record_id,
            data=base64.b64encode(app_event.to_json().encode("utf-8")).decode("utf-8"),
        )
    except Exception:
        logger.exception(
            "Failed to shape event",
            extra={"record_id": record.record_id, "detail_type": detail_type},
        )
        return KinesisFirehoseDataTransformationRecord(
            record_id=record.record_id, result="ProcessingFailed", data=record.data
        )
