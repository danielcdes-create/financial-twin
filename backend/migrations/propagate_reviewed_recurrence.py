"""Propagate user reviewed recurrence to records with the same normalized descriptor."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

from app.config import Settings
from app.main import canonical_key, configured_mongodb_uri


def raw_descriptor(item: dict) -> str:
    return str(item.get("estabelecimento_original") or item.get("descricao_original") or "")


def main() -> None:
    settings = Settings(_env_file=Path(__file__).resolve().parents[2] / ".env")
    client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=10000)
    try:
        database = client[settings.mongodb_database]
        now = datetime.now(timezone.utc)
        database.categories.update_one(
            {"key": "habitacao"},
            {"$set": {"name": "Habitação", "key": "habitacao", "active": True, "updated_at": now, "source": "system_migration"}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        reviewed = list(database.transactions.find({"recorrencia.source": "mobile_manual_review"}))
        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in reviewed:
            key = canonical_key(raw_descriptor(item))
            if key:
                grouped[key].append(item)
        updated_count = 0
        skipped_conflicts = 0
        for descriptor_key, sources in grouped.items():
            signatures = {
                (
                    source.get("categoria"),
                    source.get("subcategoria"),
                    (source.get("recorrencia") or {}).get("tipo"),
                    (source.get("recorrencia") or {}).get("periodicidade"),
                )
                for source in sources
            }
            if len(signatures) != 1:
                skipped_conflicts += 1
                continue
            category, subcategory, recurrence_type, periodicity = next(iter(signatures))
            if not category or not recurrence_type or not periodicity:
                continue
            candidates = []
            for candidate in database.transactions.find({}, {"descricao_original": 1, "estabelecimento_original": 1, "categoria": 1, "subcategoria": 1, "recorrencia": 1}):
                if canonical_key(raw_descriptor(candidate)) == descriptor_key:
                    candidates.append(candidate)
            target_ids = []
            before = []
            desired_recurrence = {"tipo": recurrence_type, "periodicidade": periodicity, "source": "reviewed_exact_match"}
            source_ids = {source["_id"] for source in sources}
            for candidate in candidates:
                if candidate["_id"] in source_ids:
                    continue
                current_recurrence = candidate.get("recorrencia") or {}
                current = (candidate.get("categoria"), candidate.get("subcategoria"), current_recurrence.get("tipo"), current_recurrence.get("periodicidade"))
                desired = (category, subcategory, recurrence_type, periodicity)
                if current != desired:
                    target_ids.append(candidate["_id"])
                    before.append({"id": candidate["_id"], "categoria": candidate.get("categoria"), "subcategoria": candidate.get("subcategoria"), "recorrencia": candidate.get("recorrencia")})
            if not target_ids:
                continue
            update = {"categoria": category, "recorrencia": desired_recurrence, "updated_at": now}
            if subcategory:
                update["subcategoria"] = subcategory
            result = database.transactions.update_many({"_id": {"$in": target_ids}}, {"$set": update})
            updated_count += result.modified_count
            database.audit_log.insert_one(
                {
                    "event": "reviewed_recurrence_exact_match_propagated",
                    "source": "system_migration",
                    "descriptor_key": descriptor_key,
                    "source_transaction_ids": [source["_id"] for source in sources],
                    "affected_transaction_ids": target_ids,
                    "before": before,
                    "after": {"categoria": category, "subcategoria": subcategory, "recorrencia": desired_recurrence},
                    "change_reason": "propagate_user_review_to_exact_normalized_descriptor",
                    "created_at": now,
                }
            )
        print(f"updated={updated_count} groups={len(grouped)} skipped_conflicts={skipped_conflicts}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
