import json
import os
from datetime import datetime, timezone

import boto3

sns_client = boto3.client("sns")
SNS_TOPIC_ARN = os.environ.get("ALERTS_SNS_TOPIC_ARN", "")
STAGE = os.environ.get("STAGE", "prod")


def lambda_handler(event, context):
    """
    Receives batched SQS messages (Error events from EventBridge) and sends
    a single SNS digest notification. With reservedConcurrency=1 and a
    batchWindow of 60s, this guarantees at most 1 alert email per minute.
    """
    records = event.get("Records", [])
    if not records:
        return {"statusCode": 200, "body": "No records"}

    errors_by_source = {}
    for record in records:
        try:
            body = json.loads(record.get("body", "{}"))
            source = body.get("source", "unknown")
            detail = body.get("detail", {})
            detail_type = body.get("detail-type", "Error")
            org = detail.get("organization", "unknown")
            user = detail.get("username", "unknown")
            error_msg = detail.get("error", detail.get("message", "No details"))

            key = source
            if key not in errors_by_source:
                errors_by_source[key] = []
            errors_by_source[key].append({
                "detail_type": detail_type,
                "organization": org,
                "username": user,
                "error": str(error_msg)[:200],
            })
        except (json.JSONDecodeError, TypeError):
            errors_by_source.setdefault("parse_error", []).append({
                "detail_type": "ParseError",
                "organization": "unknown",
                "username": "unknown",
                "error": str(record.get("body", ""))[:200],
            })

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_errors = sum(len(v) for v in errors_by_source.values())

    subject = f"[{STAGE.upper()}] {total_errors} error(s) from {len(errors_by_source)} source(s)"

    lines = [
        f"Error Alert Digest - {timestamp}",
        f"Environment: {STAGE}",
        f"Total errors in batch: {total_errors}",
        "",
    ]

    for source, errors in errors_by_source.items():
        lines.append(f"--- {source} ({len(errors)} error(s)) ---")
        # Show up to 5 unique errors per source to keep email readable
        seen = set()
        for err in errors:
            err_key = f"{err['detail_type']}:{err['error'][:80]}"
            if err_key in seen:
                continue
            seen.add(err_key)
            lines.append(f"  [{err['detail_type']}] org={err['organization']} user={err['username']}")
            lines.append(f"    {err['error']}")
            if len(seen) >= 5:
                remaining = len(errors) - len(seen)
                if remaining > 0:
                    lines.append(f"    ... and {remaining} more similar error(s)")
                break
        lines.append("")

    message = "\n".join(lines)

    if SNS_TOPIC_ARN:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],
            Message=message,
        )

    return {"statusCode": 200, "body": f"Sent digest with {total_errors} errors"}
