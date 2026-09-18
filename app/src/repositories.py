"""OpenSearch delivery. Generic — nothing tenant-shaped reaches this file."""
import json
from typing import Any, Dict, List, Optional

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from pydantic import BaseModel, PrivateAttr
from requests_aws4auth import AWS4Auth

from .domain import AppEventModel


class OpenSearchAppEventsRepository(BaseModel):
    endpoint: str
    region: str = "us-east-1"
    index: str
    # "es" for a managed OpenSearch/Elasticsearch domain, "aoss" for an
    # OpenSearch Serverless collection. The SigV4 signing service differs; the
    # rest of the client does not.
    service: str = "es"
    _client: Any = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        self._client = self._build_client()

    def _build_client(self) -> OpenSearch:
        # SigV4 with the Lambda execution role's own credentials. There are no
        # stored credentials anywhere in this service, and there must not be:
        # access is granted by naming this role in the domain's access policy.
        credentials = boto3.Session().get_credentials()
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            self.region,
            self.service,
            session_token=credentials.token,
        )
        host = self.endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        return OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )

    def save_event(self, event: AppEventModel) -> AppEventModel:
        """Index one document and return it carrying the generated id.

        Indexing failures are caught and logged rather than raised, which is a
        real weakness of this path and the strongest argument for
        DeliveryMode=Firehose: a document OpenSearch refuses — a mapper conflict
        above all — is discarded whole, with zero Lambda Errors, no alarm and no
        DLQ. Under Firehose the same document lands in the failed-document S3
        prefix and moves DeliveryToAmazonOpenSearchService.Success, which is
        measured by Firehose rather than by this code and so cannot be masked.
        """
        try:
            response = self._client.index(index=self.index, body=json.loads(event.to_json()))
            document_id = response.get("_id")
            if document_id:
                event.event_id = document_id
            print(f"Event indexed to OpenSearch: {response.get('result')} (ID: {document_id})")
        except Exception as e:  # noqa: BLE001 — see docstring
            print(f"OpenSearch indexing failed: {e}")
            import traceback

            traceback.print_exc()
        return event

    # ------------------------------------------------------------------
    # Read side. Used only by the search API (CreateSearchApi=true); the
    # forwarder never calls these.
    #
    # Every query below sorts or filters on `.keyword` sub-fields rather than
    # the analysed text field. An analysed `source` matches "billing" against
    # "billing.invoice"; the keyword sub-field is the exact value, which is what
    # a filter means. `payload` is deliberately the one field searched as text,
    # because it is a JSON string and free-text is the only useful thing to do
    # with it.
    # ------------------------------------------------------------------

    @staticmethod
    def _hydrate(source: Dict[str, Any]) -> Dict[str, Any]:
        """Turn the stored document back into something an API client wants.

        `payload` is written as a JSON *string* so OpenSearch never maps a wild
        per-event shape. On the way out it becomes an object again -- returning
        a string containing JSON would push that parsing onto every caller.
        """
        doc = dict(source)
        raw = doc.get("payload")
        if isinstance(raw, str):
            try:
                doc["payload"] = json.loads(raw)
            except (ValueError, TypeError):
                doc["payload"] = {"_unparsed": raw}
        return doc

    def _format(self, response: Dict[str, Any]) -> Dict[str, Any]:
        hits = (response.get("hits") or {}).get("hits") or []
        total = ((response.get("hits") or {}).get("total") or {}).get("value", len(hits))
        items = []
        for h in hits:
            doc = self._hydrate(h.get("_source") or {})
            # The OpenSearch document id is authoritative; under the Lambda
            # delivery path the stored event_id is backfilled from it anyway,
            # and under Firehose it holds the EventBridge id instead.
            doc.setdefault("event_id", h.get("_id"))
            items.append(doc)
        return {"items": items, "total": total}

    def search(self, *, source=None, event=None, organization=None, text=None,
               date_from=None, date_to=None, size=50, offset=0) -> Dict[str, Any]:
        filters = []
        if source:
            filters.append({"term": {"source.keyword": source}})
        if event:
            filters.append({"term": {"event.keyword": event}})
        if organization:
            filters.append({"term": {"organization.keyword": organization}})
        if date_from or date_to:
            rng = {}
            if date_from:
                rng["gte"] = date_from
            if date_to:
                rng["lte"] = date_to
            filters.append({"range": {"timestamp": rng}})

        must = []
        if text:
            must.append({"multi_match": {"query": text,
                                         "fields": ["payload", "event", "source", "username"]}})

        query: Dict[str, Any] = {"bool": {}}
        if filters:
            query["bool"]["filter"] = filters
        if must:
            query["bool"]["must"] = must
        if not filters and not must:
            query = {"match_all": {}}

        body = {"query": query, "sort": [{"timestamp": {"order": "desc"}}],
                "from": offset, "size": size, "track_total_hits": True}
        return self._format(self._client.search(index=self.index, body=body))

    def facets(self, *, size: int = 500) -> List[Dict[str, Any]]:
        """The distinct (source, detail-type) pairs in the index.

        A composite aggregation rather than nested terms: it is the one that
        paginates deterministically, and the set of event types is exactly what
        an alert rule or a trigger picker needs to offer.
        """
        body = {"size": 0, "aggs": {"pairs": {"composite": {"size": size, "sources": [
            {"source": {"terms": {"field": "source.keyword"}}},
            {"event": {"terms": {"field": "event.keyword"}}},
        ]}}}}
        response = self._client.search(index=self.index, body=body)
        buckets = (((response.get("aggregations") or {}).get("pairs") or {}).get("buckets")) or []
        return [{"source": b["key"].get("source"),
                 "detail_type": b["key"].get("event"),
                 "count": b.get("doc_count", 0)} for b in buckets]

    def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        """One document by id, or None. A miss is not an error."""
        try:
            response = self._client.get(index=self.index, id=event_id)
        except Exception:
            return None
        if not response.get("found"):
            return None
        doc = self._hydrate(response.get("_source") or {})
        doc.setdefault("event_id", response.get("_id"))
        return doc

    def set_context(self, *, environment: str, application: str) -> None:
        """No-op. Satisfies the AppEventsAnalyticsRepository Protocol."""
