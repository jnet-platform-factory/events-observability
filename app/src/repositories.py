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
    # SigV4 signing service: "es" for a managed OpenSearch/Elasticsearch domain,
    # "aoss" for OpenSearch Serverless collections.
    service: str = "es"
    _client: Any = PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        self._client = self._build_client()

    def _build_client(self) -> OpenSearch:
        # SigV4 auth using the Lambda execution role's credentials.
        credentials = boto3.Session().get_credentials()
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            self.region,
            self.service,
            session_token=credentials.token,
        )
        host = self.endpoint.replace('https://', '').replace('http://', '').rstrip('/')
        return OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )

    def save_event(self, event: AppEventModel) -> AppEventModel:
        """Save event to OpenSearch and return event with document ID."""
        try:
            response = self._client.index(
                index=self.index,
                body=json.loads(event.to_json()),
            )
            document_id = response.get('_id')
            if document_id:
                event.event_id = document_id
            print(f"Event indexed to OpenSearch: {response.get('result')} (ID: {document_id})")
        except Exception as e:
            print(f"OpenSearch indexing failed: {e}")
            import traceback
            traceback.print_exc()
        return event

    def set_context(self, *, environment: str, application: str) -> None:
        pass
