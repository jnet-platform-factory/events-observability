# Serverless Events Observability

> [!NOTE]
> **This is a published-artifact mirror.**
>
> The code here is exactly what Serverless Application Repository version
> **1.2.1** ships. Development happens in `junctionnet/platform-infrastructure`
> under `events-observability/`; this repository is refreshed automatically on
> release and does not take pull requests.
>
> The SAR application is shared with specific AWS accounts rather than published
> publicly. If you are outside those accounts you can read this source, but the
> application is not deployable by you.

Every event on an EventBridge bus, indexed into OpenSearch and searchable —
plus a throttled error-alert digest, dead-letter queues and alarms for the paths
that can fail silently.

Deploys entirely into your own AWS account. Bring your own OpenSearch domain or
Serverless collection; this stack does not provision one.

```yaml
Resources:
  EventsObservability:
    Type: AWS::Serverless::Application
    Properties:
      Location:
        ApplicationId: arn:aws:serverlessrepo:us-east-1:<account>:applications/serverless-events-observability
        SemanticVersion: 1.1.0
      Parameters:
        NamePrefix: acme
        EnvironmentName: prod
        OpenSearchEndpoint: search-acme-xxxx.us-east-1.es.amazonaws.com
        OpenSearchResourceArn: arn:aws:es:us-east-1:111122223333:domain/acme/*
        OpenSearchIndex: acme-events
```

That is a complete deployment. Publish to the bus it creates and documents start
appearing in the index.

## The document, and why its shape is configuration

Each event becomes one document:

```json
{
  "event_id":    "b4f1…",
  "source":      "billing.invoice",
  "event":       "InvoiceIssued",
  "environment": "prod",
  "application": "billing",
  "organization": "acme-corp",
  "username":    "jdoe",
  "timestamp":   "2026-09-17T09:12:44.518Z",
  "payload":     "{\"invoice_id\":\"INV-9\",\"total\":1200}"
}
```

`payload` is the entire event detail, stored as a **JSON string**. That is
deliberate: a per-event detail shape cannot be mapped into a rigid index mapping
without eventually colliding. The cost is that nothing inside `payload` can be
aggregated or filtered on — so anything you want to query lives at the top level.

Which is what `ShapingConfig` is for:

```json
{
  "defaults": {"application": "billing"},
  "promote":  [{"from": "total", "as": "invoice_total", "type": "int"}],
  "nested":   {"actor": {"user": "username", "org": "organization"}}
}
```

- **`defaults`** — fallbacks for the four core fields (`environment`,
  `application`, `organization`, `username`) when an event omits them or sends an
  empty value.
- **`promote`** — lift a top-level detail key out into its own document field so
  it can be summed, filtered and charted. `type` is `int`, `str` or `raw`.
- **`nested`** — build a composite object out of resolved core fields.

Anything the config cannot honour exactly fails the cold start with a named
error. It never falls back to a different shape — see below for why that matters
more than it sounds.

### Choose `type` deliberately

An index with no explicit mapping takes each field's type from **the first
non-null value it ever sees**, permanently. An emitter that sends `"12"` where it
once sent `12` maps the field as text, and every aggregation over it fails from
then on — fixable only by a reindex. Worse, indexing errors on the per-event path
are swallowed, so a mapping conflict discards the **whole document** and raises
nothing.

`"type": "int"` coerces what is unambiguously an integer and drops everything
else — including booleans, which are integers in Python and never a real count.
The coercers exist so that a careless emitter cannot decide your mapping.

## Delivery modes

`DeliveryMode=Lambda` (default) invokes the forwarder once per event, which
indexes one document per invocation. Simple, and fine at low volume.

`DeliveryMode=Firehose` points the rule at an Amazon Data Firehose stream.
Firehose buffers records, calls the same function as a *transformation* (a batch
in, shaped documents out), then bulk-indexes — and owns retry plus backup of
rejected documents to S3. At volume this is the difference between N HTTP round
trips and one `_bulk`.

Firehose also fixes a real blind spot: a document OpenSearch rejects lands in
`failed/<env>/` in the backup bucket and moves
`DeliveryToAmazonOpenSearchService.Success`, which Firehose measures rather than
this code — so it cannot be swallowed the way the per-event path swallows it.

### Capabilities — and they differ by how you deploy

This template creates IAM roles with **explicit names** and attaches **resource
policies** (SQS queue policies, an SNS topic policy). Both need acknowledging,
and the acknowledgement is spelled differently depending on which API you go
through. Getting it wrong fails the deploy before anything is created.

**Deploying from the Serverless Application Repository** — `serverlessrepo`
requires `CAPABILITY_RESOURCE_POLICY` and refuses without it:

```bash
aws serverlessrepo create-cloud-formation-change-set \
  --application-id <application arn> --semantic-version <version> \
  --stack-name <your stack name> \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
                 CAPABILITY_AUTO_EXPAND CAPABILITY_RESOURCE_POLICY \
  --parameter-overrides file://params.json
```

Omit it and you get, before any resource exists:

```
Required capabilities [CAPABILITY_RESOURCE_POLICY] were not provided.
```

**Deploying or updating through CloudFormation directly** — `create-change-set`
and `sam deploy` **reject** `CAPABILITY_RESOURCE_POLICY`; their enum does not
contain it:

```bash
aws cloudformation create-change-set \
  --stack-name <your stack name> --template-url <url> \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameters file://params.json
```

Pass it anyway and you get:

```
Value '[…, CAPABILITY_RESOURCE_POLICY]' at 'capabilities' failed to satisfy
constraint: Member must satisfy enum value set:
[CAPABILITY_AUTO_EXPAND, CAPABILITY_NAMED_IAM, CAPABILITY_IAM]
```

So the two paths are not interchangeable, and a tenant migrating an existing
stack will use both: `serverlessrepo` for a fresh deploy, CloudFormation for an
in-place update of a stack that already exists.

**`CAPABILITY_NAMED_IAM`, not `CAPABILITY_IAM`.** The Firehose delivery role has
a fixed name on purpose — its ARN is written into an OpenSearch access policy,
possibly in another account, and a CloudFormation-generated name would silently
invalidate that grant every time the role is replaced.

## Firehose against a cross-account domain takes two deploys

Firehose verifies at `CreateDeliveryStream` time that its delivery role can reach
the cluster. But that role is created by this stack, and OpenSearch will not
accept a principal ARN that does not exist yet. One deploy cannot do both.

1. Deploy with `FirehoseStreamEnabled=false`. Creates the role, the bucket and
   the log group, and nothing that talks to OpenSearch.
2. Take `FirehoseDeliveryRoleArn` from the outputs and add it to the domain's
   access policy.
3. Deploy again with `FirehoseStreamEnabled=true`. A successful
   `CreateDeliveryStream` **is** the proof that step 2 worked.

Two things about that grant, both of which cost someone a day to find:

- **Firehose signs as its own delivery role, not the forwarder's execution
  role.** Granting the Lambda role is not enough.
- **The read half is not optional, and it needs the bare domain root** —
  `…:domain/name` with no trailing `/*`, which the `/*` wildcard does not match —
  plus `_all/_settings`, `_cluster/stats`, `_nodes*`, `_stats` and
  `<index>*/_mapping`. Firehose probes the cluster before delivering. Omitting
  the root is what produces the misleading *"Verify that the IAM role has access
  to the Elasticsearch cluster endpoint"*.

`ClusterEndpoint` is used as given and must resolve in **your** account — a
cross-account domain cannot be addressed by `DomainARN`.

### Latency under Firehose is about 2× the buffer interval

`FirehoseBufferIntervalSeconds` applies to **both** of the stream's buffers,
which sit in series: the processing buffer before the transform and the delivery
buffer before the bulk write. Measured end to end: 105s at 60, 41.8s at 30. Set
it with that doubling in mind if anything reads the index interactively.

## The search API

Off by default. `CreateSearchApi=true` adds an HTTP API over the same index the
forwarder writes to:

```
GET /search        filter by source, detail-type, organization, free text, time range
GET /facets        the distinct (source, detail-type) pairs present
GET /{event_id}    one archived event
GET /openapi.json  the generated spec
```

Results are newest-first and paged (`size`, max 200; `offset`). `payload` comes
back as an object, not the JSON string it is stored as — the string exists so
OpenSearch never has to map a per-event shape, and undoing that on the way out is
this API's job rather than every caller's.

Filters match exactly, on `.keyword` sub-fields. An analysed match would return
`billing.invoice` for `source=billing`, which is not what a filter means. `q` is
the one fuzzy parameter and searches the payload text.

**The API is unauthenticated unless you set `SearchApiAuthorizerArn`.** Point it
at a Lambda authorizer — identity from the `Authorization` header, simple
responses — and every route but `/openapi.json` goes behind it. The archive holds
every event on your bus, which is usually more than you want world-readable.

No custom domain is created. Take `SearchApiEndpoint` or `SearchApiId` and map
your own; DNS is yours, and a stack that forwards events should not own it.

`POST /emit` publishes to the bus on the caller's behalf, for clients that cannot
reach EventBridge directly. It is behind `EnableEmitEndpoint=true` and off by
default because it is the only route that writes.

The API's role is read-only on the index. It cannot write the archive it serves —
`/emit` goes through the bus so the forwarder still decides what gets indexed.

## Bringing your own bus, or your own rule

Both are parameters, because in a mature account neither is usually yours to
create:

- **`EventBusName`** — blank creates a bus named
  `<NamePrefix>-events-<EnvironmentName>`; set it and the stack attaches to an
  existing bus and creates nothing. Two IaC tools owning one bus is how a
  rollback destroys a resource the other tool believes it manages.
- **`CreateForwardingRule`** — `true` creates a rule matching every event in the
  account. Set it `false` when the filtering is yours — a source allowlist, a
  detail-type filter, or a rule that already exists and must keep its name — and
  point your own rule at the `ForwarderFunctionArn` or `DeliveryStreamArn`
  output. Filtering at the rule is also the cheapest place to filter: an event
  that never matches is never delivered and never billed.

  A Firehose target needs `EventsToFirehoseRoleArn` as the target's `RoleArn` —
  unlike a Lambda target, there is no resource policy to attach on the Firehose
  side.

  Set it `false` and forget to create a rule and nothing is forwarded. Set it
  `true` alongside an existing rule and every event is indexed twice.

## OpenSearch Serverless

Set `OpenSearchServiceName=aoss` and point `OpenSearchResourceArn` at the
collection. Add `ForwarderRoleArn` to a data access policy on the collection.

Firehose mode requires `es` — Firehose reaches a Serverless collection through a
different destination block, which is not wired up here. Use Lambda mode for
`aoss`.

## Verifying a deployment

The surface is **EventBridge in, an OpenSearch document out**. Not the Lambda,
not the template. Publish to the bus, then read the index.

Under Firehose, the reconciliation invariant is:

```
IncomingRecords == DeliveryToAmazonOpenSearchService.Records
                 + <objects under failed/<env>/ in the backup bucket>
                 + <delivery DLQ depth>
```

`IncomingRecords` is published before the delivered-records metric for the same
batch, so a check run immediately after publishing shows a transient gap of
exactly the batch size. Poll until they converge before calling it a leak.

Worth probing explicitly, because they are the easy things to get wrong: an event
whose source your rule does not match, an event missing a field your config
promotes, and the same event published twice (you get two documents — re-ingestion
is not deduplicated).

## Alarms

On the bus: `FailedInvocations`, `ThrottledRules`. On the forwarder: errors,
duration, and an invocation spike. On the error path: DLQ depth. Under Firehose:
`DeliveryToAmazonOpenSearchService.Success` **and** `.DataFreshness` — a stalled
stream reports no failures at all, so `Success` alone would never fire.

All of them publish to the `AlertsSnsTopicArn` output. **Subscribe something to
it.** An SNS topic with no subscriptions accepts every publish and reports
success, and `describe-alarm-history` will say "Successfully executed action"
while nobody is told anything.

## Licence

Apache-2.0.
