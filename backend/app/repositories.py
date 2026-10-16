from datetime import datetime, timezone
from typing import Any
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

class EventConflict(Exception):
    pass

class EventRepository:
    def __init__(self, uri: str, database: str):
        self.client = MongoClient(uri, serverSelectionTimeoutMS=2500)
        self.collection = self.client[database]["raw_events"]
        self.collection.create_index([("event_id", ASCENDING)], unique=True)

    def ping(self) -> None:
        self.client.admin.command("ping")

    def close(self) -> None:
        self.client.close()

    def put_if_new(self, event: dict[str, Any]) -> bool:
        event["received_at"] = datetime.now(timezone.utc)
        event["processing_status"] = "pending"
        event["confidence"] = None
        event["linked_transaction_id"] = None
        try:
            self.collection.insert_one(event)
            return True
        except DuplicateKeyError:
            existing = self.collection.find_one({"event_id": event["event_id"]})
            if existing is None:
                raise
            keys = ("source_type", "source_provider", "captured_at", "raw_payload", "parsed_payload")
            if any(existing.get(key) != event.get(key) for key in keys):
                raise EventConflict(event["event_id"])
            return False
