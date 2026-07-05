from typing import Dict, Any

from .domain import AppEventModel


class AppEventsForwarderService:
    def __init__(self, repository, context: Dict[str, Any] = None):
        self.repository = repository
        self.context = context or {}

    def save(self, app_event: AppEventModel) -> AppEventModel:
        response = self.repository.save_event(app_event)
        return response
