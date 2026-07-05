# Serverless Events Observability

Drop-in event observability for EventBridge-based serverless platforms. Deploys
entirely into **your own AWS account** — nothing is hosted by anyone else.

One deploy gives you three things:

1. **A central EventBridge bus** (`<prefix>-events-<env>`) that all your services
   publish domain events to.
2. **A throttled error-alert digest** — an EventBridge rule routes error events
   to SQS, and a Lambda batches them into at most **one SNS notification per
   minute** (no alert storms).
3. **An OpenSearch forwarder** — every event on the bus is indexed into your
   OpenSearch domain or Serverless collection for search, dashboards, and
   analytics.

Plus CloudWatch alarms for bus health (failed invocations, throttled rules, DLQ
depth) and forwarder guardrails (invocation spikes, errors, p99 duration).

## Architecture

```
producers ──put_events──► EventBridge bus ──┬─► rule (Error/*) ─► SQS ─► digest Lambda ─► SNS ─► you
                                            └─► rule (all)     ─────────► forwarder Lambda ─► OpenSearch
```

## Prerequisites

- An existing **OpenSearch domain** or **OpenSearch Serverless collection** in
  your account. This template does **not** provision OpenSearch — you bring the
  endpoint. (Auto-provisioning is on the roadmap; it's left out so a one-click
  deploy never surprises you with a standing OpenSearch bill.)

## Parameters

| Parameter | Required | Default | Notes |
|-----------|----------|---------|-------|
| `NamePrefix` | no | `events-observability` | Prefix for every resource name. |
| `EnvironmentName` | no | `prod` | Used in names and alert subjects. |
| `OpenSearchEndpoint` | **yes** | — | Your domain/collection endpoint. |
| `OpenSearchResourceArn` | **yes** | — | ARN to scope the forwarder's IAM. Managed: `arn:aws:es:REGION:ACCT:domain/NAME/*`. Serverless: `arn:aws:aoss:REGION:ACCT:collection/ID`. |
| `OpenSearchServiceName` | no | `es` | `es` for a managed domain, `aoss` for Serverless (sets the SigV4 signing service). |
| `OpenSearchRegion` | no | stack region | Set if OpenSearch lives in a different region than this stack. |
| `OpenSearchIndex` | **yes** | — | Index events are written to (lowercase; auto-created on first write). |
| `AlertEmail` | no | — | Optional email subscribed to the alerts topic. |

## Deploy from the Serverless Application Repository

Find **serverless-events-observability** in the SAR console, set the parameters,
and deploy — or deploy via CLI:

```bash
sam deploy \
  --template-url <SAR-provided-url> \
  --stack-name events-observability \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    OpenSearchEndpoint=search-my-domain-xxxx.us-east-1.es.amazonaws.com \
    OpenSearchResourceArn=arn:aws:es:us-east-1:111122223333:domain/my-domain/* \
    OpenSearchRegion=us-east-1 \
    OpenSearchIndex=platform-events \
    AlertEmail=team@example.com
```

### OpenSearch access — one required post-deploy step

The forwarder authenticates to OpenSearch with **AWS SigV4** using its Lambda
execution role (`ForwarderRoleArn` in the stack outputs). You must let that role
in:

- **Managed domain:** if the domain has a restrictive access policy, add
  `ForwarderRoleArn` as a principal allowed `es:ESHttp*` on the domain. (The
  stack already grants the role the matching IAM permissions.)
- **Serverless collection:** add `ForwarderRoleArn` to a **data access policy**
  on the collection granting index/write permissions, and keep
  `OpenSearchServiceName=aoss`.

## Publishing events

```python
import boto3, json

boto3.client("events").put_events(Entries=[{
    "Source": "order.service",
    "DetailType": "OrderCreated",           # any type => indexed to OpenSearch
    "Detail": json.dumps({
        "organization": "acme", "username": "jane@acme.com",
        "environment": "prod", "application": "orders",
        "order_id": "12345",
    }),
    "EventBusName": "<prefix>-events-<env>",
}])
```

To trigger the **error digest**, use a `DetailType` of `Error`,
`ProcessingError`, or `IntegrationError` and include an `error` (or `message`)
field in the detail.

## Outputs

`EventBusName`, `EventBusArn`, `AlertsSnsTopicArn`, `ErrorAlertsQueueArn`,
`ForwarderFunctionArn`, `ForwarderRoleArn`.

## Publishing this app to SAR (maintainers)

```bash
cd marketplace/events-observability
sam build --use-container                 # needs Docker (builds pydantic-core wheels)
sam package --s3-bucket <your-sar-artifact-bucket> --output-template-file packaged.yaml
sam publish --template packaged.yaml --region <region>
# then, in the SAR console, mark the application public (or share with specific accounts)
```

### Using the Makefile

A [Makefile](Makefile) wraps every step. Run `make` (or `make help`) to list
targets and see the current variable values.

```bash
cd marketplace/events-observability

# One-time: create the artifact bucket and grant SAR read access
make create-bucket S3_BUCKET=my-sar-artifacts

# Do everything: validate -> build -> package -> publish
make release S3_BUCKET=my-sar-artifacts
```

| Target | Description |
|--------|-------------|
| `make help` | List targets and show current vars (default target). |
| `make validate` | Lint & validate the SAM template. |
| `make build` | `sam build --use-container` (needs Docker). |
| `make create-bucket S3_BUCKET=…` | Create the SAR artifact bucket and attach the `serverlessrepo` read policy. |
| `make package S3_BUCKET=…` | Upload code artifacts and emit `packaged.yaml`. |
| `make publish` | Publish `packaged.yaml` to SAR. |
| `make release S3_BUCKET=…` | Full pipeline: validate → build → package → publish. |
| `make deploy` | Guided deploy into your own account for testing. |
| `make outputs` | Show deployed stack outputs. |
| `make logs-forwarder` / `make logs-digest` | Tail the forwarder / alert-digest logs. |
| `make destroy` | Delete the test stack. |
| `make clean` | Remove build artifacts (`.aws-sam`, `packaged.yaml`). |

**Variables** (override on the command line, e.g. `make release S3_BUCKET=… REGION=us-west-1`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `S3_BUCKET` | _(required for package/publish)_ | SAR artifact bucket. |
| `REGION` | `us-east-1` | Target AWS region. |
| `STACK_NAME` | `events-observability` | Stack name for test deploys. |
| `AWS_VAULT` | _(unset)_ | If set, wraps every command in `aws-vault exec <profile> --`. Otherwise export `AWS_PROFILE`. |

> **Bump `SemanticVersion`** in [template.yaml](template.yaml) before each new
> `make publish` — SAR rejects re-publishing the same version.

## License

Apache-2.0. See [LICENSE.txt](LICENSE.txt).
