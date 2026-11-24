import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo.errors import WriteError

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from bson_schema_validator import (
    BsonSchemaValidationError,
    ValidatingCollection,
    assert_valid_collection_doc,
    load_canonical_validators,
    validate_bson_document,
)


def _valid_tx() -> dict:
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    oid = ObjectId()
    return {
        "_id": oid,
        "external_key": f"tx-{oid}",
        "schema_version": 2,
        "account_id": ObjectId(),
        "data_evento": now,
        "competencia": "2026-11",
        "valor": Decimal128("-45.90"),
        "moeda": "BRL",
        "fluxo": "saida",
        "tipo": "compra",
        "descricao_original": "Compra Teste",
        "categoria": "Alimentação",
        "localidade_contexto": "nao_determinado",
        "localidade_real_validada": False,
        "origem_localidade": "nao_informada",
        "source": {
            "source_record_id": ObjectId(),
            "identificador_original": f"orig-{oid}",
            "fonte": "unit_test",
        },
        "status_conciliacao": "pendente",
        "created_at": now,
        "updated_at": now,
    }


class BsonSchemaValidatorTests(unittest.TestCase):
    def test_canonical_fixture_contains_all_seven_collections(self):
        schemas = load_canonical_validators()
        self.assertEqual(
            set(schemas.keys()),
            {
                "accounts",
                "transactions",
                "credit_card_bills",
                "installment_plans",
                "obligations",
                "merchants",
                "reconciliations",
            },
        )

    def test_strict_bson_types_and_constraints(self):
        tx = _valid_tx()
        assert_valid_collection_doc("transactions", tx)

        # Bare datetime.date rejected for bsonType 'date'
        bad_date = {**tx, "data_evento": date(2026, 11, 1)}
        with self.assertRaises(BsonSchemaValidationError):
            assert_valid_collection_doc("transactions", bad_date)

        # bool rejected for bsonType 'int'
        bad_int = {**tx, "schema_version": True}
        with self.assertRaises(BsonSchemaValidationError):
            assert_valid_collection_doc("transactions", bad_int)

        # float/str rejected for bsonType 'decimal'
        with self.assertRaises(BsonSchemaValidationError):
            assert_valid_collection_doc("transactions", {**tx, "valor": "-45.90"})

        # exclusiveMinimum on nested foreign_amount.fx_rate_to_brl
        zero_fx = {
            **tx,
            "foreign_amount": {
                "value": Decimal128("10.00"),
                "currency": "USD",
                "fx_rate_to_brl": Decimal128("0.00"),
            },
        }
        with self.assertRaises(BsonSchemaValidationError):
            assert_valid_collection_doc("transactions", zero_fx)

        valid_fx = {
            **tx,
            "foreign_amount": {
                "value": Decimal128("10.00"),
                "currency": "USD",
                "fx_rate_to_brl": Decimal128("5.75"),
            },
        }
        assert_valid_collection_doc("transactions", valid_fx)

        # nullable union bsonType ['string', 'null']
        nullable_schema = {
            "$jsonSchema": {
                "bsonType": "object",
                "additionalProperties": False,
                "required": ["label"],
                "properties": {
                    "label": {"bsonType": ["string", "null"], "minLength": 2},
                    "tags": {
                        "bsonType": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"bsonType": "string"},
                    },
                },
            }
        }
        validate_bson_document(nullable_schema, {"label": None})
        validate_bson_document(nullable_schema, {"label": "ok", "tags": ["a", "b"]})
        with self.assertRaises(BsonSchemaValidationError):
            validate_bson_document(nullable_schema, {"label": "x"})
        with self.assertRaises(BsonSchemaValidationError):
            validate_bson_document(nullable_schema, {"label": "ok", "tags": ["a", "a"]})

    def test_validating_collection_raises_write_error_121_without_mutating_state(self):
        tx = _valid_tx()
        col = ValidatingCollection("transactions", [tx], validate_initial=True)

        with self.assertRaises(WriteError) as ctx:
            col.update_one({"_id": tx["_id"]}, {"$set": {"vendedor": "Forbidden"}})
        self.assertEqual(ctx.exception.code, 121)
        self.assertNotIn("vendedor", tx)

        res = col.update_one({"_id": tx["_id"]}, {"$set": {"detalhe": "Nota válida"}})
        self.assertEqual(res.matched_count, 1)
        self.assertEqual(res.modified_count, 1)
        self.assertEqual(tx["detalhe"], "Nota válida")


if __name__ == "__main__":
    unittest.main()
