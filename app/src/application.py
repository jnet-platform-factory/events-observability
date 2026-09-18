"""The service layer. Generic."""
from typing import Any, Dict

from .domain import AppEventModel


class AppEventsForwarderService:
    def __init__(self, repository, context: Dict[str, Any] = None):
        self.repository = repository
        self.context = context or {}

    def save(self, app_event: AppEventModel) -> AppEventModel:
        return self.repository.save_event(app_event)
