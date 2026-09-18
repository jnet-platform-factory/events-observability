"""Wiring. Reads the environment once, builds the delivery side.

This is the asymmetric half of the seam: `factories` imports `repositories`,
which imports `opensearch-py` and `boto3`. `shaping` imports none of them, which
is what lets the shaping tests run — and the field maps be validated — without a
delivery dependency installed. See `tests/test_shaping_standalone.py`.
"""
import os
from functools import lru_cache
from typing import Any, Dict

from .application import AppEventsForwarderService
from .repositories import OpenSearchAppEventsRepository
from .shaping import load_config

OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT")
OPENSEARCH_REGION = os.getenv("OPENSEARCH_REGION", "us-east-1")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "platform-events")
OPENSEARCH_SERVICE = os.getenv("OPENSEARCH_SERVICE", "es")


@lru_cache(maxsize=1)
def shaping_config() -> Dict[str, Any]:
    """The tenant's field map, parsed once per execution environment.

    Deliberately not lazy about failing: `load_config` raises on anything it
    cannot honour exactly, and that exception is allowed to escape. A cold start
    that dies with a named config error is recoverable in a way that a silently
    differently-shaped document is not — the latter decides an index mapping
    permanently.
    """
    return load_config(os.getenv("SHAPING_CONFIG"))


class ServiceFactory:
    @staticmethod
    def create_repository() -> OpenSearchAppEventsRepository:
        """The index client, for readers as well as the forwarder.

        The search API wants a repository and no service around it; building a
        forwarder service just to reach through to `.repository` would read as
        though the API writes.
        """
        return OpenSearchAppEventsRepository(
            endpoint=OPENSEARCH_ENDPOINT,
            region=OPENSEARCH_REGION,
            index=OPENSEARCH_INDEX,
            service=OPENSEARCH_SERVICE,
        )

    @staticmethod
    def create_app_events_forwarder_service(context: Dict[str, Any] = None) -> AppEventsForwarderService:
        return AppEventsForwarderService(
            repository=ServiceFactory.create_repository(), context=context
        )
