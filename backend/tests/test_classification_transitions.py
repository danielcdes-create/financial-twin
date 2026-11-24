"""Explicit user clears and required-field transitions; only in-memory fakes with strict BSON schema validation."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from app import main
from app.source_tokens import source_token
from bson_schema_validator import ValidatingCollection, validate_bson_document
from test_ingestion import FakeDB
from test_transaction_review import ReviewCollection

TX_VALIDATOR = json.loads(
    (Path(__file__).parent / "fixtures" / "transactions-validator.json").read_text(encoding="utf-8")
)


def _canonical_tx_row(*, platform_id: ObjectId | None = None, merchant_id: ObjectId | None = None) -> dict:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    oid = ObjectId()
    doc = {
        "_id": oid,
        "external_key": f"tx-{oid}",
        "schema_version": 2,
        "account_id": ObjectId(),
        "data_evento": now,
        "competencia": "2026-10",
        "valor": Decimal128("-10.00"),
        "moeda": "BRL",
        "fluxo": "saida",
        "tipo": "compra",
        "descricao_original": "Compra sintética",
        "categoria": "Alimentação",
        "subcategoria": "Anterior",
        "metodo_pagamento": "pix",
        "localidade": "Local anterior",
        "localidade_contexto": "nao_determinado",
        "localidade_real_validada": False,
        "origem_localidade": "nao_informada",
        "detalhe": "Detalhe anterior",
        "source": {
            "source_record_id": ObjectId(),
            "identificador_original": f"orig-{oid}",
            "fonte": "unit_test",
        },
        "status_conciliacao": "pendente",
        "created_at": now,
        "updated_at": now,
    }
    if platform_id is not None:
        doc["platform_id"] = platform_id
    if merchant_id is not None:
        doc["merchant_id"] = merchant_id
    return doc


class ClassificationTransitionsTests(unittest.TestCase):
    def setUp(self):
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        self.platform = {
            "_id": ObjectId(),
            "external_key": "platform-sintetica",
            "schema_version": 2,
            "tipo_entidade": "marca",
            "nome_padrao": "Plataforma sintética",
            "aliases": [
                {
                    "valor": "PLATAFORMA SINTETICA",
                    "normalizado": "PLATAFORMA SINTETICA",
                    "status": "confirmado",
                    "source_record_ids": [],
                }
            ],
            "created_at": now,
            "updated_at": now,
        }
        self.vendor = {
            "_id": ObjectId(),
            "external_key": "merchant-sintetico",
            "schema_version": 2,
            "tipo_entidade": "marca",
            "nome_padrao": "Vendedor sintético",
            "aliases": [
                {
                    "valor": "VENDEDOR SINTETICO",
                    "normalizado": "VENDEDOR SINTETICO",
                    "status": "confirmado",
                    "source_record_ids": [],
                }
            ],
            "created_at": now,
            "updated_at": now,
        }
        self.row = _canonical_tx_row(platform_id=self.platform["_id"], merchant_id=self.vendor["_id"])
        self.db = FakeDB(
            transactions=ValidatingCollection("transactions", [self.row], schema=TX_VALIDATOR, validate_initial=True),
            merchants=ValidatingCollection("merchants", [self.platform, self.vendor], validate_initial=True),
        )

    def update(self, **fields):
        value = main.TransactionClassificationUpdate(expected_token=source_token(self.row), **fields)
        return main._update_transaction_classification(str(self.row["_id"]), value, self.db)

    def test_explicit_optional_clears_are_persisted_and_audited(self):
        result = self.update(subcategoria="", metodo_pagamento="", plataforma="", localidade="", detalhe="")
        self.assertTrue(result["changed"])
        validate_bson_document(TX_VALIDATOR, self.row, path="transactions")
        for field in ("subcategoria", "localidade", "detalhe"):
            self.assertFalse(self.row.get(field))
        self.assertNotIn("metodo_pagamento", self.row)
        self.assertNotIn("platform_id", self.row)
        self.assertNotIn("user_cleared_fields", self.row)
        self.assertEqual(self.row["descricao_original"], "Compra sintética")
        after = self.db.audit_log.docs[-1]["after"]
        self.assertIsNone(after["plataforma"])
        self.assertIsNone(after["metodo_pagamento"])

    def test_explicit_vendor_and_platform_clear_obeys_transactions_schema(self):
        result = self.update(vendedor="", plataforma="")
        self.assertTrue(result["changed"])
        validate_bson_document(TX_VALIDATOR, self.row, path="transactions")
        self.assertNotIn("merchant_id", self.row)
        self.assertNotIn("platform_id", self.row)
        self.assertNotIn("vendedor", self.row)
        self.assertNotIn("plataforma", self.row)
        self.assertNotIn("user_cleared_fields", self.row)
        self.assertFalse(result["item"].get("vendedor"))
        self.assertFalse(result["item"].get("plataforma"))

    def test_setting_new_vendor_while_clearing_platform_obeys_transactions_schema(self):
        result = self.update(vendedor="Padaria Nova Era", plataforma="")
        self.assertTrue(result["changed"])
        validate_bson_document(TX_VALIDATOR, self.row, path="transactions")
        self.assertIsInstance(self.row.get("merchant_id"), ObjectId)
        self.assertNotEqual(self.row["merchant_id"], self.vendor["_id"])
        self.assertNotIn("platform_id", self.row)
        self.assertNotIn("vendedor", self.row)
        self.assertNotIn("merchant_name", self.row)
        self.assertNotIn("plataforma", self.row)
        self.assertNotIn("user_cleared_fields", self.row)
        self.assertEqual(result["item"]["vendedor"], "Padaria Nova Era")
        self.assertFalse(result["item"].get("plataforma"))
        self.assertTrue(any(m.get("nome_padrao") == "Padaria Nova Era" for m in self.db.merchants.docs))

    def test_category_change_accepts_explicit_dependent_subcategory_clear(self):
        with self.assertRaises(HTTPException) as level1:
            self.update(categoria="Transporte", subcategoria="")
        self.assertEqual(level1.exception.status_code, 422)
        self.update(categoria="Categoria sintética", subcategoria="")
        validate_bson_document(TX_VALIDATOR, self.row, path="transactions")
        self.assertEqual(self.row["categoria"], "Categoria sintética")
        self.assertFalse(self.row.get("subcategoria"))

    def test_omitted_optionals_preserve_their_previous_values(self):
        self.row.pop("subcategoria")
        self.update(detalhe="Novo detalhe")
        validate_bson_document(TX_VALIDATOR, self.row, path="transactions")
        self.assertEqual(self.row["metodo_pagamento"], "pix")
        self.assertEqual(self.row["platform_id"], self.platform["_id"])
        self.assertEqual(self.row["merchant_id"], self.vendor["_id"])

    def test_whitespace_cannot_clear_required_classification(self):
        for field in ("categoria", "tipo", "competencia", "localidade_contexto", "status_conciliacao"):
            with self.subTest(field=field):
                with self.assertRaises(HTTPException) as failure:
                    self.update(**{field: " "})
                self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(self.row["categoria"], "Alimentação")

    def test_provisional_optional_clears_preserve_revision_and_raw_evidence(self):
        row = {
            **self.row,
            "provisional_id": "clear-synthetic",
            "revision": 1,
            "status": "enriched",
            "merchant_raw": "Evidência original",
            "categoria_sugerida": "Alimentação",
        }
        self.db.provisional_transactions = ReviewCollection([row])
        value = main.ProvisionalUpdate(
            expected_revision=1,
            categoria_sugerida="",
            subcategoria="",
            metodo_pagamento="",
            plataforma="",
            localidade="",
            detalhe="",
        )
        main._atomic_update_provisional(row["provisional_id"], value, self.db)
        self.assertEqual(row["revision"], 2)
        self.assertEqual(row["merchant_raw"], "Evidência original")
        self.assertNotIn("platform_id", row)
        self.assertNotIn("metodo_pagamento", row)
        self.assertFalse(row.get("categoria_sugerida"))
        after = self.db.audit_log.docs[-1]["after"]
        self.assertIsNone(after["plataforma"])
        self.assertIsNone(after["metodo_pagamento"])
        self.assertNotIn("vendedor", after, "Clearing another field must not claim that the vendor was cleared")

    def test_exact_batch_propagates_optional_clears(self):
        peer = _canonical_tx_row(platform_id=self.platform["_id"], merchant_id=self.vendor["_id"])
        self.db.transactions.docs.append(peer)
        matches = [(peer, "Compra sintética")]
        with patch.object(main, "batch_classification_matches", return_value=matches):
            token = main.preview_signature([peer], str(self.row["_id"]))
            self.update(
                subcategoria="",
                metodo_pagamento="",
                plataforma="",
                apply_similar_classification=True,
                classification_preview_token=token,
            )
        validate_bson_document(TX_VALIDATOR, peer, path="transactions")
        self.assertFalse(peer.get("subcategoria"))
        self.assertNotIn("metodo_pagamento", peer)
        self.assertNotIn("platform_id", peer)

    def test_legacy_platform_clear_removes_both_representations_and_audits_previous_value(self):
        for kind in ("provisional", "transaction"):
            for has_platform_id in (False, True):
                with self.subTest(kind=kind, has_platform_id=has_platform_id):
                    base = _canonical_tx_row(
                        platform_id=self.platform["_id"] if has_platform_id else None,
                        merchant_id=self.vendor["_id"],
                    )
                    row = {**base, "plataforma": "Plataforma legada sintética"}
                    previous = self.platform["nome_padrao"] if has_platform_id else row["plataforma"]
                    peer_base = _canonical_tx_row(
                        platform_id=self.platform["_id"] if has_platform_id else None,
                        merchant_id=self.vendor["_id"],
                    )
                    peer = {**peer_base, "plataforma": "Plataforma legada sintética"}
                    db = FakeDB(merchants=ReviewCollection([self.platform, self.vendor]))
                    if kind == "provisional":
                        row.update(
                            provisional_id="legacy-platform-synthetic",
                            revision=1,
                            status="enriched",
                            merchant_raw="Evidência original",
                        )
                        db.provisional_transactions = ReviewCollection([row])
                        result = main._atomic_update_provisional(
                            row["provisional_id"],
                            main.ProvisionalUpdate(expected_revision=1, plataforma=""),
                            db,
                        )
                        self.assertEqual(row["revision"], 2)
                        self.assertEqual(row["merchant_raw"], "Evidência original")
                    else:
                        # Unsetting an old field also repairs rows admitted by an older schema.
                        db.transactions = ValidatingCollection(
                            "transactions", [row, peer], schema=TX_VALIDATOR, validate_initial=False
                        )
                        with patch.object(main, "batch_classification_matches", return_value=[(peer, "Compra sintética")]):
                            value = main.TransactionClassificationUpdate(
                                expected_token=source_token(row),
                                plataforma="",
                                apply_similar_classification=True,
                                classification_preview_token=main.preview_signature([peer], str(row["_id"])),
                            )
                            result = main._update_transaction_classification(str(row["_id"]), value, db)
                        validate_bson_document(TX_VALIDATOR, row, path="transactions")
                        validate_bson_document(TX_VALIDATOR, peer, path="transactions")
                        self.assertNotIn("platform_id", peer)
                        self.assertNotIn("plataforma", peer)
                        self.assertEqual(row["descricao_original"], "Compra sintética")
                    self.assertTrue(result["changed"])
                    self.assertNotIn("platform_id", row)
                    self.assertNotIn("plataforma", row)
                    self.assertFalse(result["item"].get("plataforma"))
                    event = (
                        "provisional_transaction_updated"
                        if kind == "provisional"
                        else "transaction_classification_updated"
                    )
                    audit = next(entry for entry in db.audit_log.docs if entry["event"] == event)
                    self.assertEqual(audit["before"]["plataforma"], previous)
                    self.assertIsNone(audit["after"]["plataforma"])
                    self.assertNotIn("vendedor", audit["after"])
                    self.assertEqual(len(db.merchants.docs), 2)

    def test_whitespace_platform_clear_never_creates_a_blank_merchant(self):
        for kind in ("provisional", "transaction"):
            for has_platform_id in (False, True):
                with self.subTest(kind=kind, has_platform_id=has_platform_id):
                    row = _canonical_tx_row(
                        platform_id=self.platform["_id"] if has_platform_id else None,
                        merchant_id=self.vendor["_id"],
                    )
                    db = FakeDB(merchants=ReviewCollection([self.platform, self.vendor]))
                    if kind == "provisional":
                        row.update(
                            provisional_id="whitespace-platform-synthetic",
                            revision=1,
                            status="enriched",
                            merchant_raw="Evidência original",
                        )
                        db.provisional_transactions = ReviewCollection([row])
                        result = main._atomic_update_provisional(
                            row["provisional_id"],
                            main.ProvisionalUpdate(expected_revision=1, plataforma=" \t\n "),
                            db,
                        )
                        self.assertEqual(row["merchant_raw"], "Evidência original")
                        self.assertEqual(row["revision"], 2 if has_platform_id else 1)
                    else:
                        db.transactions = ValidatingCollection(
                            "transactions", [row], schema=TX_VALIDATOR, validate_initial=True
                        )
                        value = main.TransactionClassificationUpdate(
                            expected_token=source_token(row), plataforma=" \t\n "
                        )
                        result = main._update_transaction_classification(str(row["_id"]), value, db)
                        validate_bson_document(TX_VALIDATOR, row, path="transactions")
                        self.assertEqual(row["descricao_original"], "Compra sintética")
                    self.assertNotIn("platform_id", row)
                    self.assertFalse(result["item"].get("plataforma"))
                    self.assertEqual(result["changed"], has_platform_id)
                    self.assertEqual(db.merchants.docs, [self.platform, self.vendor])
                    self.assertTrue(all(merchant.get("nome_padrao", "").strip() for merchant in db.merchants.docs))
                    if has_platform_id:
                        audit = db.audit_log.docs[-1]
                        self.assertEqual(audit["before"]["plataforma"], self.platform["nome_padrao"])
                        self.assertIsNone(audit["after"]["plataforma"])
                    else:
                        self.assertEqual(db.audit_log.docs, [])


if __name__ == "__main__":
    unittest.main()
