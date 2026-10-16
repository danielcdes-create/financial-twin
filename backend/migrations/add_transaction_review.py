"""Add optional manual review fields without rewriting historical transactions."""

from pathlib import Path

from pymongo import MongoClient

from app.config import Settings
from app.main import configured_mongodb_uri


REVIEW_PROPERTIES = {
    "status_revisao": {
        "enum": ["pendente", "validado"],
        "description": "Estado da revisão humana; ausência equivale a pendente. Independente da conciliação.",
    },
    "editado_em": {
        "bsonType": "date",
        "description": "Última edição manual direta da classificação.",
    },
    "revisado_em": {
        "bsonType": "date",
        "description": "Data da validação humana explícita.",
    },
}


def main() -> None:
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
        validator = options.get("validator") or {}
        schema = validator.get("$jsonSchema")
        if not isinstance(schema, dict):
            raise RuntimeError("transactions does not use a JSON schema validator")
        properties = schema.setdefault("properties", {})
        if all(properties.get(name) == rule for name, rule in REVIEW_PROPERTIES.items()):
            print("transactions review validator already up to date")
            return
        properties.update(REVIEW_PROPERTIES)
        database.command({
            "collMod": "transactions", "validator": validator,
            "validationLevel": options.get("validationLevel", "strict"),
            "validationAction": options.get("validationAction", "error"),
        })
        print("transactions review validator updated")
    finally:
        client.close()


if __name__ == "__main__":
    main()
