import copy
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from app.commitment_profile import (
    ProfileWrite,
    StatementProfileWrite,
    apply_commitment_profile,
    close_imported_open,
    paint_movement,
    sync_statement_metadata,
)
from app.commitments import schedule
from app.future_commitments import effective_document, effective_documents, future_obligations_view
from app.installment_cards import attach_cards
from app.main import commitment_output, with_installment_references, with_merchant_names
from bson_schema_validator import ValidatingCollection, assert_valid_collection_doc
from test_capture_batches import BatchDB

FORBIDDEN_TX_FIELDS = ("vendedor", "merchant_name", "plataforma", "card_id", "card_label")


def money(value):
    return Decimal128(str(value))


class CommitmentProfileTests(unittest.TestCase):
    def test_app_route_matches_the_editor(self):
        from app.commitment_profile import router

        paths = {getattr(route, "path", "") for route in router.routes}
        self.assertIn("/v1/commitment-profile", paths)
        self.assertIn("/v1/commitment-profile/from-statement", paths)

    def setUp(self):
        clock = patch("app.future_commitments.today", return_value=date(2026, 10, 14))
        clock.start()
        self.addCleanup(clock.stop)
        self.db = BatchDB()
        self.db["transactions"] = ValidatingCollection("transactions", validate_initial=True)
        for name in (
            "future_schedule_overrides",
            "future_commitment_commands",
            "audit_log",
            "commitment_profile_commands",
            "reconciliations",
            "commitments",
            "installment_plans",
            "obligations",
            "merchants",
            "accounts",
            "account_profiles",
            "installment_card_links",
        ):
            self.db[name]
        self.default_account_id = ObjectId()
        now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        self.db.accounts.insert_one({
            "_id": self.default_account_id,
            "external_key": "acc-bank-default",
            "schema_version": 2,
            "instituicao": "Itaú",
            "tipo": "conta_corrente",
            "titular": "Titular Sintetico",
            "moeda": "BRL",
            "status": "ativo",
            "created_at": now,
            "updated_at": now,
        })

    def seed_merchant(self, name: str) -> ObjectId:
        now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        mid = ObjectId()
        self.db.merchants.insert_one({
            "_id": mid,
            "external_key": f"merchant-{mid}",
            "schema_version": 2,
            "tipo_entidade": "marca",
            "nome_padrao": name,
            "aliases": [{"valor": name.upper(), "normalizado": name.upper(), "status": "confirmado", "source_record_ids": []}],
            "created_at": now,
            "updated_at": now,
        })
        return mid

    def tx(
        self,
        valor: str = "-10.00",
        day: str = "2026-10-12",
        *,
        account_id: ObjectId | None = None,
        merchant_id: ObjectId | None = None,
        platform_id: ObjectId | None = None,
        categoria: str = "Outros",
        subcategoria: str | None = None,
        natureza: str | None = "COMPRA",
        metodo_pagamento: str | None = None,
        descricao_original: str = "LOJA1000",
    ) -> dict:
        oid = ObjectId()
        evt = datetime.fromisoformat(day + "T15:00:00+00:00")
        now = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
        document = {
            "_id": oid,
            "external_key": f"tx-{oid}",
            "schema_version": 2,
            "account_id": account_id or self.default_account_id,
            "data_evento": evt,
            "competencia": day[:7],
            "valor": money(valor),
            "moeda": "BRL",
            "fluxo": "saida" if Decimal(valor) < 0 else "entrada",
            "tipo": "compra",
            "descricao_original": descricao_original,
            "categoria": categoria,
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
        if natureza:
            document["natureza"] = natureza
        if subcategoria is not None:
            document["subcategoria"] = subcategoria
        if merchant_id is not None:
            document["merchant_id"] = merchant_id
        if platform_id is not None:
            document["platform_id"] = platform_id
        if metodo_pagamento is not None:
            document["metodo_pagamento"] = metodo_pagamento
        self.db.transactions.insert_one(document)
        return document

    def test_paint_movement_enforces_canonical_transactions_schema_and_read_enrichment(self):
        new_acc_id = ObjectId()
        card_oid = ObjectId()
        now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        self.db.accounts.insert_one({
            "_id": new_acc_id,
            "external_key": "acc-santander",
            "schema_version": 2,
            "instituicao": "Santander",
            "tipo": "conta_corrente",
            "titular": "Titular Sintetico",
            "moeda": "BRL",
            "status": "ativo",
            "created_at": now,
            "updated_at": now,
        })
        self.db.accounts.insert_one({
            "_id": card_oid,
            "external_key": "card-nubank-4321",
            "schema_version": 2,
            "instituicao": "Nubank",
            "tipo": "cartao_credito",
            "cartao_final": "4321",
            "titular": "Titular Sintetico",
            "moeda": "BRL",
            "status": "ativo",
            "created_at": now,
            "updated_at": now,
        })
        row = self.tx("-55.00", "2026-10-10", descricao_original="IFD*FARMACIA")
        paint_movement(
            self.db,
            row,
            {
                "display_name": "Farmacia Popular",
                "plataforma": "iFood",
                "categoria": "Saude",
                "subcategoria": "farmacia",
                "natureza": "COMPRA",
                "metodo_pagamento": "cartao_credito",
                "account_id": str(new_acc_id),
                "card_id": str(card_oid),
                "card_label": "Nubank 4321",
            },
        )
        assert_valid_collection_doc("transactions", row)
        for forbidden in FORBIDDEN_TX_FIELDS:
            self.assertNotIn(forbidden, row)
        self.assertIsInstance(row.get("merchant_id"), ObjectId)
        self.assertIsInstance(row.get("platform_id"), ObjectId)
        self.assertIsInstance(row.get("account_id"), ObjectId)
        self.assertEqual(row["metodo_pagamento"], "cartao_credito")

        enriched = with_merchant_names([row], self.db)[0]
        self.assertEqual(enriched["vendedor"], "Farmacia Popular")
        self.assertEqual(enriched["plataforma"], "iFood")

    def test_metadata_reaches_every_linked_row_and_amount_stays_inside_scope(self):
        raw_merchant = self.seed_merchant("Codigo bruto")
        paid = self.tx("-10.00", "2026-09-12", merchant_id=raw_merchant)
        opened = self.tx("-10.00", "2026-10-12", merchant_id=raw_merchant)
        later = self.tx("-10.00", "2026-12-12", merchant_id=raw_merchant)
        plan_id = ObjectId()
        plan = {
            "_id": plan_id,
            "descricao": "LOJA1000",
            "status": "ativo",
            "numero_parcelas": 3,
            "installments": [
                {"numero": 1, "competencia": "2026-09", "valor": money("10.00"), "status": "paga", "transaction_id": paid["_id"]},
                {"numero": 2, "competencia": "2026-11", "data_vencimento": datetime(2026, 11, 2, tzinfo=timezone.utc), "valor": money("10.00"), "status": "projetada", "open_statement_id": opened["_id"]},
                {"numero": 3, "competencia": "2026-12", "data_vencimento": datetime(2026, 12, 2, tzinfo=timezone.utc), "valor": money("10.00"), "status": "projetada", "open_statement_id": later["_id"]},
            ],
        }
        self.db.installment_plans.docs.append(copy.deepcopy(plan))
        saved = apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="installment",
                identity=str(plan_id),
                display_name="Loja Sintetica",
                natureza="COMPRA",
                categoria="Saude",
                subcategoria="farmacia",
                metodo_pagamento="cartao_credito",
                amount=Decimal("12.50"),
                due_date=date(2026, 11, 5),
                scope="next",
            ),
        )
        self.assertTrue(saved["persisted"])
        self.assertEqual(self.db.installment_plans.docs[0]["descricao"], "LOJA1000")
        self.assertEqual(self.db.installment_plans.docs[0]["installments"], plan["installments"])
        for row in (paid, opened, later):
            assert_valid_collection_doc("transactions", row)
            for forbidden in FORBIDDEN_TX_FIELDS:
                self.assertNotIn(forbidden, row)
            self.assertIsInstance(row.get("merchant_id"), ObjectId)
            enriched_row = with_merchant_names([row], self.db)[0]
            self.assertEqual(enriched_row["vendedor"], "Loja Sintetica")
            self.assertEqual(row["categoria"], "Saude")
            self.assertEqual(row["subcategoria"], "farmacia")
            self.assertEqual(row["metodo_pagamento"], "cartao_credito")
            self.assertEqual(row["descricao_original"], "LOJA1000")

        self.assertEqual(paid["valor"].to_decimal(), Decimal("-10.00"))
        self.assertEqual(opened["valor"].to_decimal(), Decimal("-12.50"))
        self.assertEqual(later["valor"].to_decimal(), Decimal("-10.00"))
        self.assertEqual(opened["data_evento"].date().isoformat(), "2026-11-05")
        shown = effective_document(self.db, "installment", self.db.installment_plans.docs[0])
        self.assertEqual(shown["descricao_original"], "LOJA1000")
        self.assertEqual(shown["descricao"], "Loja Sintetica")
        self.assertEqual(shown["installments"][0]["valor"].to_decimal(), Decimal("10.00"))
        self.assertEqual(shown["installments"][1]["valor"].to_decimal(), Decimal("12.50"))
        self.assertEqual(shown["installments"][2]["valor"].to_decimal(), Decimal("10.00"))

        apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="installment",
                identity=str(plan_id),
                amount=Decimal("8.00"),
                scope="future",
            ),
        )
        self.assertEqual(paid["valor"].to_decimal(), Decimal("-10.00"))
        self.assertEqual(opened["valor"].to_decimal(), Decimal("-8.00"))
        self.assertEqual(later["valor"].to_decimal(), Decimal("-8.00"))
        shown = effective_document(self.db, "installment", self.db.installment_plans.docs[0])
        self.assertEqual(shown["installments"][0]["status"], "paga")
        self.assertEqual(
            [part["valor"].to_decimal() for part in shown["installments"][1:]],
            [Decimal("8.00"), Decimal("8.00")],
        )

        before = {row["_id"]: row["valor"].to_decimal() for row in self.db.transactions.docs}
        sync_statement_metadata(
            self.db,
            StatementProfileWrite(
                request_id=uuid4(),
                transaction_id=str(opened["_id"]),
                vendedor="Loja Sintetica",
                categoria="Casa",
                natureza="COMPRA",
            ),
        )
        self.assertEqual(before, {row["_id"]: row["valor"].to_decimal() for row in self.db.transactions.docs})
        self.assertTrue(all(row["categoria"] == "Casa" for row in self.db.transactions.docs))

        plan_before_close = copy.deepcopy(plan)
        close_imported_open(self.db, "installment", str(plan_id))
        shown = effective_document(self.db, "installment", self.db.installment_plans.docs[0])
        self.assertEqual([part["status"] for part in shown["installments"]], ["paga", "cancelada", "cancelada"])
        self.assertEqual(self.db.installment_plans.docs[0], plan_before_close)
        again = effective_document(self.db, "installment", copy.deepcopy(plan))
        self.assertEqual([part["status"] for part in again["installments"]], ["paga", "cancelada", "cancelada"])
        self.assertEqual(paid["valor"].to_decimal(), Decimal("-10.00"))

    def test_financing_series_edits_the_whole_open_set_and_close_does_not_recreate_it(self):
        paid_tx = self.tx("-40.00", "2026-09-08")
        next_tx = self.tx("-20.00", "2026-11-08")
        after_tx = self.tx("-20.00", "2026-12-08")
        paid_id, next_id, after_id = ObjectId(), ObjectId(), ObjectId()
        rows = [
            {
                "_id": paid_id,
                "status": "futura",
                "tipo": "financiamento",
                "descricao": "DIVIDA1000",
                "external_key": "sintetico|divida|parcela-1",
                "data_vencimento": datetime(2026, 9, 8, tzinfo=timezone.utc),
                "saldo_aberto": money("20.00"),
                "valor_pago": money("20.00"),
                "transaction_id": paid_tx["_id"],
            },
            {
                "_id": next_id,
                "status": "futura",
                "tipo": "financiamento",
                "descricao": "DIVIDA1000",
                "external_key": "sintetico|divida|parcela-2",
                "data_vencimento": datetime(2026, 11, 8, tzinfo=timezone.utc),
                "saldo_aberto": money("20.00"),
                "valor_pago": money("0.00"),
                "open_statement_id": next_tx["_id"],
            },
            {
                "_id": after_id,
                "status": "futura",
                "tipo": "financiamento",
                "descricao": "DIVIDA1000",
                "external_key": "sintetico|divida|parcela-3",
                "data_vencimento": datetime(2026, 12, 8, tzinfo=timezone.utc),
                "saldo_aberto": money("20.00"),
                "valor_pago": money("0.00"),
                "open_statement_id": after_tx["_id"],
            },
        ]
        originals = copy.deepcopy(rows)
        self.db.obligations.docs.extend(rows)
        apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="obligation",
                identity="sintetico|divida",
                display_name="Divida Sintetica",
                natureza="COMPRA",
                categoria="Moradia",
                subcategoria="financiamento",
                amount=Decimal("15.00"),
                scope="next",
            ),
        )
        self.assertEqual(self.db.obligations.docs, originals)
        assert_valid_collection_doc("transactions", paid_tx)
        for forbidden in FORBIDDEN_TX_FIELDS:
            self.assertNotIn(forbidden, paid_tx)
        self.assertEqual(with_merchant_names([paid_tx], self.db)[0]["vendedor"], "Divida Sintetica")
        self.assertEqual(next_tx["valor"].to_decimal(), Decimal("-15.00"))
        self.assertEqual(after_tx["valor"].to_decimal(), Decimal("-20.00"))
        self.assertEqual(paid_tx["valor"].to_decimal(), Decimal("-40.00"))

        apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="obligation",
                identity="sintetico|divida",
                amount=Decimal("9.00"),
                scope="future",
            ),
        )
        self.assertEqual(paid_tx["valor"].to_decimal(), Decimal("-40.00"))
        self.assertEqual(next_tx["valor"].to_decimal(), Decimal("-9.00"))
        self.assertEqual(after_tx["valor"].to_decimal(), Decimal("-9.00"))
        first = close_imported_open(self.db, "obligation", "sintetico|divida")
        self.assertEqual(first["closed"], 2)
        listed = [item["id"] for item in future_obligations_view(self.db)["items"]]
        self.assertNotIn(str(next_id), listed)
        self.assertNotIn(str(after_id), listed)
        second = close_imported_open(self.db, "obligation", "sintetico|divida")
        self.assertEqual(second["closed"], 0)
        self.assertEqual([item["id"] for item in future_obligations_view(self.db)["items"]], listed)
        self.assertEqual(self.db.obligations.docs, originals)
        self.assertEqual(paid_tx["valor"].to_decimal(), Decimal("-40.00"))

    def test_live_statement_row_supplies_the_seller_category_and_card(self):
        plan_id, card_id, bank_id = ObjectId(), ObjectId(), ObjectId()
        self.db.accounts.docs.append({"_id": card_id, "tipo": "cartao_credito", "instituicao": "Banco", "cartao_final": "1111"})
        self.db.accounts.docs.append({"_id": bank_id, "tipo": "conta_corrente", "instituicao": "Banco"})
        self.db.account_profiles.docs.append({"_id": f"card-profile:{card_id}", "values": {"apelido": "Cartao Sintetico 1111"}})
        self.db.installment_card_links.docs.append({"_id": str(plan_id), "card_id": str(card_id)})
        seller_id = self.seed_merchant("Loja Sintetica")
        document = self.tx(
            "-10.00",
            "2026-10-12",
            account_id=bank_id,
            merchant_id=seller_id,
            categoria="Saude",
            subcategoria="farmacia",
            natureza="COMPRA",
            metodo_pagamento="cartao_credito",
            descricao_original="LOJA1000",
        )
        plan = {
            "_id": plan_id,
            "descricao": "LOJA1000",
            "status": "ativo",
            "numero_parcelas": 2,
            "installments": [
                {"numero": 1, "status": "paga", "transaction_id": document["_id"]},
                {"numero": 2, "status": "projetada", "valor": money("10.00")},
            ],
        }
        self.db.installment_plans.docs.append(copy.deepcopy(plan))
        listed = attach_cards(self.db, [plan])[0]
        self.assertEqual(listed["descricao"], "LOJA1000")
        self.assertEqual(listed["display_name"], "Loja Sintetica")
        self.assertEqual(listed["linked_purchase"]["vendedor"], "Loja Sintetica")
        self.assertEqual(listed["categoria"], "Saude")
        self.assertEqual(listed["subcategoria"], "farmacia")
        self.assertEqual(listed["card_label"], "Cartao Sintetico 1111")
        self.assertFalse(self.db.audit_log.docs)

        enriched = with_merchant_names([document], self.db)
        with_installment_references([document], enriched, self.db)
        self.assertEqual(enriched[0]["card_label"], "Cartao Sintetico 1111")
        self.assertEqual(enriched[0]["installment_plan"]["card_label"], "Cartao Sintetico 1111")
        self.assertEqual(enriched[0]["installment_plan"]["name"], "Loja Sintetica")
        self.assertNotIn("cartao_label", enriched[0])

        painted = apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="installment",
                identity=str(plan_id),
                card_id="Cartao Sintetico 2222",
            ),
        )
        self.assertTrue(painted["persisted"])
        assert_valid_collection_doc("transactions", document)
        for forbidden in FORBIDDEN_TX_FIELDS:
            self.assertNotIn(forbidden, document)

        visible = attach_cards(self.db, effective_documents(self.db, "installment", self.db.installment_plans.docs))[0]
        self.assertEqual(visible["card_label"], "Cartao Sintetico 2222")
        self.assertEqual(visible["profile"]["card_label"], "Cartao Sintetico 2222")
        again = with_merchant_names([document], self.db)
        with_installment_references([document], again, self.db)
        self.assertEqual(again[0]["card_label"], "Cartao Sintetico 2222")
        self.assertEqual(again[0]["installment_plan"]["card_label"], "Cartao Sintetico 2222")

    def test_clearing_optional_fields_and_changing_category_resets_stale_subcategory(self):
        bank_id = ObjectId()
        card_id = ObjectId()
        orig_merchant = self.seed_merchant("Academia Original")
        wellhub_id = self.seed_merchant("Wellhub")
        tx_doc = self.tx(
            "-99.90",
            "2026-10-10",
            account_id=bank_id,
            merchant_id=orig_merchant,
            platform_id=wellhub_id,
            categoria="Saude",
            subcategoria="academia",
            natureza="COMPRA",
            metodo_pagamento="cartao_credito",
            descricao_original="ACADEMIA WELLHUB",
        )
        ob_id = ObjectId()
        self.db.obligations.docs.append({
            "_id": ob_id,
            "status": "futura",
            "tipo": "recorrente",
            "descricao": "Academia Original",
            "external_key": "sintetico|academia|parcela-1",
            "data_vencimento": datetime(2026, 11, 10, tzinfo=timezone.utc),
            "saldo_aberto": money("99.90"),
            "valor_pago": money("0.00"),
            "open_statement_id": tx_doc["_id"],
        })
        apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="obligation",
                identity="sintetico|academia",
                display_name="Gym Custom",
                categoria="Saude",
                subcategoria="academia",
                natureza="COMPRA",
                plataforma="Wellhub",
                metodo_pagamento="cartao_credito",
                card_id=str(card_id),
            ),
        )
        assert_valid_collection_doc("transactions", tx_doc)
        eff1 = effective_document(self.db, "obligation", self.db.obligations.docs[-1])
        self.assertEqual(eff1["natureza"], "COMPRA")
        self.assertEqual(eff1["plataforma"], "Wellhub")
        self.assertEqual(eff1["metodo_pagamento"], "cartao_credito")

        apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="obligation",
                identity="sintetico|academia",
                display_name=None,
                categoria="Lazer",
                natureza=None,
                plataforma=None,
                metodo_pagamento=None,
                card_id=None,
            ),
        )
        tx_after = self.db.transactions.find_one({"_id": tx_doc["_id"]})
        assert_valid_collection_doc("transactions", tx_after)
        for forbidden in FORBIDDEN_TX_FIELDS:
            self.assertNotIn(forbidden, tx_after)
        self.assertEqual(tx_after["categoria"], "Lazer")
        self.assertEqual(tx_after.get("subcategoria", ""), "")
        self.assertNotIn("commitment_label", tx_after)
        self.assertEqual(tx_after["account_id"], bank_id)
        self.assertNotIn("natureza", tx_after)
        self.assertNotIn("platform_id", tx_after)
        self.assertNotIn("metodo_pagamento", tx_after)

        enriched_after = with_merchant_names([tx_after], self.db)[0]
        self.assertEqual(enriched_after["vendedor"], "Gym Custom")
        self.assertNotIn("plataforma", enriched_after)

        eff2 = effective_document(self.db, "obligation", self.db.obligations.docs[-1])
        self.assertEqual(eff2["categoria"], "Lazer")
        self.assertNotIn("subcategoria", eff2)
        self.assertNotIn("plataforma", eff2)

    def test_managed_parcelamento_updates_amount_and_due_date_for_next_and_future_scope(self):
        cid = ObjectId()
        self.db.commitments.docs.append({
            "_id": cid,
            "commitment_id": str(cid),
            "name": "Notebook Parcelado",
            "kind": "parcelamento",
            "status": "ativo",
            "installment_count": 4,
            "next_installment_number": 1,
            "frequency": "mensal",
            "renewal_status": "finita",
            "payment_method": "pix",
            "purchase_date": "2026-10-15",
            "amount": "250.00",
            "next_due_date": "2026-11-15",
            "schedule_anchor_day": 15,
            "category": "Tecnologia",
            "subcategoria": "hardware",
            "revision": 2,
        })
        res_next = apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="managed",
                identity=str(cid),
                scope="next",
                expected_revision=2,
                amount=Decimal("275.50"),
                due_date=date(2026, 11, 22),
                categoria="Educacao",
            ),
        )
        self.assertTrue(res_next["persisted"])
        doc = self.db.commitments.find_one({"_id": cid})
        # scope="next" must write installment_overrides[base_due_date] WITHOUT overwriting top-level amount or next_due_date
        self.assertEqual(doc["amount"], "250.00")
        self.assertIsInstance(doc["amount"], str)
        self.assertEqual(doc["next_due_date"], "2026-11-15")
        self.assertEqual(doc["schedule_anchor_day"], 15)
        self.assertIn("2026-11-15", doc.get("installment_overrides", {}))
        self.assertEqual(
            doc["installment_overrides"]["2026-11-15"],
            {"amount": "275.50", "due_date": "2026-11-22", "cancelled": False},
        )
        self.assertEqual(doc["category"], "Educacao")
        self.assertNotIn("subcategoria", doc)
        self.assertEqual(doc["revision"], 3)

        sched_after_next = schedule(doc, 4)
        self.assertEqual(
            [(r["due_date"], r["amount"]) for r in sched_after_next],
            [
                ("2026-11-22", 275.5),
                ("2026-12-15", 250.0),
                ("2027-01-15", 250.0),
                ("2027-02-15", 250.0),
            ],
        )

    def test_managed_parcelamento_future_scope_shifts_due_day_and_preserves_str_amount(self):
        cid = ObjectId()
        self.db.commitments.docs.append({
            "_id": cid,
            "commitment_id": str(cid),
            "name": "Monitor Parcelado",
            "kind": "parcelamento",
            "status": "ativo",
            "installment_count": 4,
            "next_installment_number": 1,
            "frequency": "mensal",
            "renewal_status": "finita",
            "payment_method": "pix",
            "purchase_date": "2026-10-10",
            "amount": money("180.00"),
            "next_due_date": "2026-11-10",
            "schedule_anchor_day": 10,
            "revision": 1,
        })
        res_future = apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="managed",
                identity=str(cid),
                scope="future",
                expected_revision=1,
                amount=Decimal("195.75"),
                due_date=date(2026, 11, 25),
            ),
        )
        self.assertTrue(res_future["persisted"])
        doc = self.db.commitments.find_one({"_id": cid})
        self.assertIsInstance(doc["amount"], str)
        self.assertEqual(doc["amount"], "195.75")
        self.assertEqual(doc["next_due_date"], "2026-11-25")
        self.assertEqual(doc["schedule_anchor_day"], 25)
        self.assertEqual(doc["revision"], 2)

        sched = schedule(doc, 4)
        self.assertEqual(
            [r["due_date"] for r in sched],
            ["2026-11-25", "2026-12-25", "2027-01-25", "2027-02-25"],
        )
        self.assertTrue(all(r["amount"] == 195.75 for r in sched))

    def test_active_managed_commitment_amount_edit_never_breaks_schedule_or_output(self):
        cid = ObjectId()
        self.db.commitments.docs.append({
            "_id": cid,
            "commitment_id": str(cid),
            "name": "Streaming Ativo",
            "kind": "assinatura",
            "status": "ativo",
            "frequency": "mensal",
            "renewal_status": "automatica",
            "payment_method": "pix",
            "purchase_date": "2026-01-10",
            "amount": "39.90",
            "next_due_date": "2026-11-30",
            "schedule_anchor_day": 30,
            "schedule_anchor_end": False,
            "revision": 1,
        })
        apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="managed",
                identity=str(cid),
                expected_revision=1,
                amount=Decimal("44.90"),
                due_date=date(2026, 11, 30),
            ),
        )
        doc = self.db.commitments.find_one({"_id": cid})
        self.assertEqual(doc["amount"], "44.90")
        self.assertFalse(doc["schedule_anchor_end"])
        out = commitment_output(doc)
        self.assertEqual(out["amount"], 44.9)
        self.assertEqual([r["due_date"] for r in schedule(doc, 3)], ["2026-11-30", "2026-12-30", "2027-01-30"])
        doc["amount"] = Decimal128("49.90")
        self.assertEqual(commitment_output(doc)["amount"], 49.9)

    def test_future_scope_shifts_monthly_due_dates_without_collapsing_series(self):
        plan_id = ObjectId()
        self.db.installment_plans.docs.append({
            "_id": plan_id,
            "descricao": "CURSO PARCELADO",
            "status": "ativo",
            "numero_parcelas": 3,
            "installments": [
                {"numero": 1, "competencia": "2026-11", "data_vencimento": datetime(2026, 11, 5, tzinfo=timezone.utc), "valor": money("100.00"), "status": "projetada"},
                {"numero": 2, "competencia": "2026-12", "data_vencimento": datetime(2026, 12, 5, tzinfo=timezone.utc), "valor": money("100.00"), "status": "projetada"},
                {"numero": 3, "competencia": "2027-01", "data_vencimento": datetime(2027, 1, 5, tzinfo=timezone.utc), "valor": money("100.00"), "status": "projetada"},
            ],
        })
        apply_commitment_profile(
            self.db,
            ProfileWrite(
                request_id=uuid4(),
                source="installment",
                identity=str(plan_id),
                amount=Decimal("110.00"),
                due_date=date(2026, 11, 20),
                scope="future",
            ),
        )
        eff = effective_document(self.db, "installment", self.db.installment_plans.docs[-1])
        dues = [part["data_vencimento"].date().isoformat() for part in eff["installments"]]
        self.assertEqual(dues, ["2026-11-20", "2026-12-20", "2027-01-20"])


if __name__ == "__main__":
    unittest.main()
