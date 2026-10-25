"""Create the planned indexes and report the query plans without reading records."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from pymongo import DESCENDING, ASCENDING, MongoClient
from app.config import get_settings
from app.main import configured_mongodb_uri


def stages(value):
    found = []
    if isinstance(value, dict):
        if "stage" in value:
            found.append(value["stage"])
        for child in value.values():
            found.extend(stages(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(stages(child))
    return found


def explain(db, collection, query, sort):
    plan = db.command("explain", {"find": collection, "filter": query, "sort": sort, "limit": 50}, verbosity="executionStats")
    stats = plan.get("executionStats", {})
    return {
        "stages": stages(plan.get("queryPlanner", {}).get("winningPlan", {})),
        "returned": stats.get("nReturned"),
        "documents_examined": stats.get("totalDocsExamined"),
        "keys_examined": stats.get("totalKeysExamined"),
        "execution_ms": stats.get("executionTimeMillis"),
    }


def main():
    settings = get_settings()
    if not settings.mongodb_uri:
        raise SystemExit("MongoDB não configurado")
    client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=5000)
    db = client[settings.mongodb_database]
    created = {
        "transactions": [
            db.transactions.create_index([("data_evento", DESCENDING), ("_id", DESCENDING)], name="movement_page"),
            db.transactions.create_index([("categoria", ASCENDING), ("data_evento", DESCENDING), ("_id", DESCENDING)], name="movement_category_page"),
            db.transactions.create_index([("competencia", DESCENDING), ("data_evento", DESCENDING), ("_id", DESCENDING)], name="transaction_competence_window"),
            db.transactions.create_index([("status_revisao", ASCENDING), ("data_evento", DESCENDING), ("_id", DESCENDING)], name="transaction_review_status_page"),
        ],
        "provisional_transactions": [
            db.provisional_transactions.create_index([("status", ASCENDING), ("data_evento", DESCENDING), ("_id", DESCENDING)], name="provisional_status_page"),
            db.provisional_transactions.create_index([("status", ASCENDING), ("competencia", DESCENDING), ("data_evento", DESCENDING)], name="provisional_competence_window"),
            db.provisional_transactions.create_index([("event_id", ASCENDING)], name="provisional_by_event_id"),
        ],
        "review_queue": [
            db.review_queue.create_index([("status", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)], name="review_status_page"),
        ],
        "obligations": [
            db.obligations.create_index([("status", ASCENDING), ("tipo", ASCENDING), ("data_vencimento", ASCENDING)], name="obligation_status_type_due"),
        ],
        "transaction_evidence": [
            db.transaction_evidence.create_index([("transaction_id", ASCENDING), ("status", ASCENDING)], name="evidence_by_transaction"),
        ],
        "audit_log": [
            db.audit_log.create_index([("event", ASCENDING), ("transaction_id", ASCENDING), ("created_at", DESCENDING)], name="audit_reviewed_labels"),
        ],
    }
    report = {
        "database": settings.mongodb_database,
        "indexes": created,
        "plans": {
            "movements": explain(db, "transactions", {}, {"data_evento": -1, "_id": -1}),
            "movements_by_category": explain(db, "transactions", {"categoria": "Mercado"}, {"data_evento": -1, "_id": -1}),
            "transactions_competence": explain(db, "transactions", {"competencia": {"$gte": "2026-01", "$lte": "2026-12"}}, {"competencia": -1, "data_evento": -1, "_id": -1}),
            "review_records": explain(db, "transactions", {"status_revisao": {"$ne": "validado"}}, {"data_evento": -1, "_id": -1}),
            "provisional": explain(db, "provisional_transactions", {"status": {"$in": ["provisional", "enriched", "confirmed"]}}, {"data_evento": -1, "_id": -1}),
            "reviews": explain(db, "review_queue", {"status": "pending"}, {"created_at": -1, "_id": -1}),
            "obligations_open": explain(db, "obligations", {"status": "futura", "tipo": "financiamento"}, {"data_vencimento": 1}),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    client.close()


if __name__ == "__main__":
    main()
