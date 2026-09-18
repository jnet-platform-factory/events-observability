"""OpenSearch delivery. Generic — nothing tenant-shaped reaches this file."""
import json
from typing import Any

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

    def set_context(self, *, environment: str, application: str) -> None:
        """No-op. Satisfies the AppEventsAnalyticsRepository Protocol."""
