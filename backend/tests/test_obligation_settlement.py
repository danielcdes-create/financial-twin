import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from bson import ObjectId
from bson.decimal128 import Decimal128
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1]))
from observed_contract import observed_mock_payment
from app.commitments import COMMITMENT_CUTOFF, CommitmentIn, schedule, stored_document
from app.main import CommitmentPaymentLink, commitment_output, commitment_payment_candidates, link_commitment_payment, list_commitments
from app.voice_capture import CaptureDraft, InterpretInput, interpret


class OpenCursor(list):
    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        return OpenCursor(self[:count])


class OpenCollection:
    """Returns every stored row. The route under test applies its own amount window."""

    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, _query=None, _projection=None):
        return OpenCursor(self.docs)

    def find_one(self, query=None, _projection=None):
        if query and "commitment_id" in query:
            return next((row for row in self.docs if row.get("commitment_id") == query["commitment_id"]), None)
        return None


class OpenDB:
    commitment_amount_rules = None

    def __init__(self, **collections):
        self._collections = collections

    def __getattr__(self, name):
        return self._collections.get(name, OpenCollection([]))


def obligation(**extra):
    fields = dict(
        kind="obrigacao",
        name="IPVA",
        purchase_date=date(2026, 10, 11),
        next_due_date=date(2026, 11, 10),
        cash_due_date=date(2026, 11, 20),
        amount=Decimal("100.00"),
        frequency="mensal",
        payment_method="pix",
        renewal_status="nao_aplicavel",
        obligation_plan="parcelado",
        installment_count=3,
        next_installment_number=1,
    )
    fields.update(extra)
    return stored_document(CommitmentIn(**fields))


def expense(day, value, description="IPVA 2027"):
    return {
        "_id": ObjectId(),
        "valor": Decimal128(str(value)),
        "fluxo": "saida",
        "data_evento": datetime(day.year, day.month, day.day, 15, tzinfo=timezone.utc),
        "classification": {"migration_reason": "despesa_real"},
        "descricao_original": description,
        "estabelecimento_original": description,
    }


class ObligationSettlementTests(unittest.TestCase):
    def test_cash_due_date_is_a_civil_date_and_cutoff_stays_fixed(self):
        stamped = obligation(obligation_plan="a_vista", installment_count=None, next_installment_number=None)
        stamped["cash_due_date"] = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
        output = commitment_output(stamped)
        self.assertEqual(output["cash_due_date"], "2026-11-20")
        self.assertNotIn("T", output["cash_due_date"])
        missing = obligation(obligation_plan="a_vista", installment_count=None, next_installment_number=None)
        missing.pop("cash_due_date")
        self.assertIsNone(commitment_output(missing)["cash_due_date"])
        self.assertEqual(COMMITMENT_CUTOFF, date(2026, 10, 1))
        db = MagicMock()
        db.commitment_amount_rules = None
        db.commitments.count_documents.return_value = 0
        cursor = MagicMock()
        cursor.sort.return_value.skip.return_value.limit.return_value = []
        db.commitments.find.return_value = cursor
        listed = list_commitments(limit=1000, offset=0, db=db)
        self.assertEqual(listed["commitment_cutoff_date"], "2026-10-01")

    def test_parcelado_sentinel_accepts_only_the_concluded_counter(self):
        with self.assertRaises(ValidationError):
            obligation(status="ativo", next_installment_number=4)
        with self.assertRaises(ValidationError):
            obligation(status="concluido", next_installment_number=5)
        edited = obligation(status="concluido", next_installment_number=4, next_due_date=date(2027, 1, 10), notes="Nota posterior")
        self.assertEqual(edited["status"], "concluido")
        self.assertEqual(edited["next_installment_number"], 4)
        self.assertEqual(edited["notes"], "Nota posterior")
        self.assertEqual(edited["installment_count"], 3)

    def test_parcelado_candidates_compare_the_installment_not_the_tax_total(self):
        item = obligation()
        item["commitment_id"] = "ipva-candidates"
        share = Decimal(str(schedule(item, 1)[0]["amount"]))
        self.assertNotEqual(share, Decimal(item["amount"]))
        close = expense(date(2026, 11, 10), -share, "IPVA parcela")
        total = expense(date(2026, 11, 10), Decimal("-100.00"), "IPVA total")
        db = OpenDB(commitments=OpenCollection([item]), transactions=OpenCollection([total, close]))
        result = commitment_payment_candidates("ipva-candidates", db)
        found = [row["descricao_original"] for row in result["items"]]
        self.assertEqual(found, ["IPVA parcela"])
        self.assertNotIn("IPVA total", found)

    def _link(self, item, paid, value):
        item["_id"] = ObjectId()
        tx_id = ObjectId()
        tx = expense(paid, value, item["name"])
        tx["_id"] = tx_id
        db = MagicMock()
        db.commitment_amount_rules = None
        db.commitments.find_one.side_effect = [item, None]
        db.transactions.find_one.return_value = tx
        result = link_commitment_payment(item["commitment_id"], observed_mock_payment(CommitmentPaymentLink(transaction_id=str(tx_id)), db, item), db)
        update = db.commitments.update_one.call_args.args[1]
        db.transactions.insert_one.assert_not_called()
        db.provisional_transactions.insert_one.assert_not_called()
        return result, update

    def test_avista_settle_concludes_without_an_extrato_row(self):
        item = obligation(obligation_plan="a_vista", installment_count=None, next_installment_number=None, next_due_date=date(2026, 11, 20), cash_due_date=date(2026, 11, 20))
        result, update = self._link(item, date(2026, 11, 20), Decimal("-100.00"))
        self.assertEqual(result["status"], "concluido")
        self.assertEqual(update["$set"]["status"], "concluido")
        self.assertNotIn("next_installment_number", update["$set"])
        self.assertEqual(update["$push"]["linked_transactions"]["expected_amount"], str(schedule(item, 1)[0]["amount"]))

    def test_parcelado_settle_advances_then_concludes_on_the_last_share(self):
        item = obligation()
        share = str(schedule(item, 1)[0]["amount"])
        _result, update = self._link(item, date(2026, 11, 10), Decimal("-" + share))
        self.assertEqual(update["$set"]["next_installment_number"], 2)
        self.assertNotEqual(update["$set"].get("status"), "concluido")
        self.assertEqual(update["$push"]["linked_transactions"]["expected_amount"], share)
        self.assertNotEqual(Decimal(share), Decimal(item["amount"]))
        last = obligation(next_installment_number=3, next_due_date=date(2027, 1, 10))
        last_share = str(schedule(last, 1)[0]["amount"])
        self.assertNotEqual(last_share, share)
        result, final = self._link(last, date(2027, 1, 10), Decimal("-" + last_share))
        self.assertEqual(final["$set"]["next_installment_number"], 4)
        self.assertEqual(final["$set"]["status"], "concluido")
        self.assertEqual(result["status"], "concluido")
        self.assertEqual(final["$push"]["linked_transactions"]["expected_amount"], last_share)

    def test_voice_draft_accepts_obrigacao_without_calling_xai_or_persisting(self):
        draft = CaptureDraft(kind="obrigacao", amount=Decimal("300.00"), obligation_plan="parcelado", cash_due_date=date(2027, 1, 15), name="IPVA")
        self.assertEqual(draft.kind, "obrigacao")
        self.assertEqual(draft.obligation_plan, "parcelado")
        with self.assertRaises(ValidationError):
            CaptureDraft(kind="obrigacao", obligation_plan="mensal")
        captured = {}

        def fake_post(_base, _key, payload, model):
            captured["text"] = payload["input"][0]["content"][0]["text"]
            captured["model"] = model
            return CaptureDraft(kind="obrigacao", amount=Decimal("300.00"), obligation_plan="a_vista", cash_due_date=date(2027, 1, 15), name="IPTU")

        settings = type("Settings", (), {"xai_api_key": "server-secret", "xai_base_url": "https://api.x.ai/v1", "xai_text_model": "grok-4.3"})()
        with patch("app.voice_capture.get_settings", return_value=settings), patch("app.voice_capture._post", side_effect=fake_post):
            result = interpret(InterpretInput(context="commitment", text="IPTU de trezentos reais à vista"))
        self.assertIn('kind="obrigacao"', captured["text"])
        self.assertIn("IPVA", captured["text"])
        self.assertIn("IPTU", captured["text"])
        self.assertIn("cash_due_date", captured["text"])
        self.assertIn('"a_vista"', captured["text"])
        self.assertIn('"parcelado"', captured["text"])
        self.assertIn("per charge", captured["text"])
        self.assertIn("amount is the total", captured["text"])
        self.assertIs(captured["model"], CaptureDraft)
        self.assertFalse(result["persisted"])
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["draft"]["kind"], "obrigacao")
        self.assertEqual(result["draft"]["cash_due_date"], "2027-01-15")
        self.assertEqual(result["draft"]["obligation_plan"], "a_vista")


if __name__ == "__main__":
    unittest.main()
