"""Extend the transactions validator with the optional recurrence object.

Dry run by default. Pass --apply to execute the validator update.
"""

import argparse
import copy
import sys
from pathlib import Path

from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.main import configured_mongodb_uri


RECURRENCE_SCHEMA = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["tipo", "periodicidade"],
    "properties": {
        "tipo": {"enum": ["recorrente_fixa", "recorrente_variavel", "eventual"]},
        "periodicidade": {
            "enum": ["semanal", "mensal", "trimestral", "semestral", "anual", "irregular", "nao_aplicavel"]
        },
        "obligation_id": {"bsonType": "objectId"},
        "source": {"enum": ["mobile_manual_review", "reviewed_exact_match", "reviewed_high_confidence_match", "obligation", "derived"]},
    },
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply validator changes to MongoDB (default: dry-run).")
    args = parser.parse_args(argv)
    settings = Settings(_env_file=Path(__file__).resolve().parents[2] / ".env")
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required")
    client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=5000)
    try:
        database = client[settings.mongodb_database]
        collection = next(database.list_collections(filter={"name": "transactions"}), None)
        if collection is None:
            raise RuntimeError("transactions collection does not exist")
        options = collection.get("options") or {}
        validator = copy.deepcopy(options.get("validator") or {})
        json_schema = validator.get("$jsonSchema")
        if not isinstance(json_schema, dict):
            raise RuntimeError("transactions does not use a JSON schema validator")
        properties = json_schema.setdefault("properties", {})
        if properties.get("recorrencia") == RECURRENCE_SCHEMA:
            print("transactions recurrence validator already up to date")
            return
        if not args.apply:
            print("dry-run: transactions recurrence validator requires update (pass --apply to execute)")
            return
        properties["recorrencia"] = RECURRENCE_SCHEMA
        database.command(
            {
                "collMod": "transactions",
                "validator": validator,
                "validationLevel": options.get("validationLevel", "strict"),
                "validationAction": options.get("validationAction", "error"),
            }
        )
        print("transactions recurrence validator updated")
    finally:
        client.close()


if __name__ == "__main__":
    main()
