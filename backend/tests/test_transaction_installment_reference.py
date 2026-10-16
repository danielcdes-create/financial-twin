import unittest
from unittest.mock import Mock, patch

from bson import Decimal128, ObjectId

from app.main import with_installment_references


class TestTransactionInstallmentReference(unittest.TestCase):
    def test_links_ledger_row_to_plan_and_future_parts_by_persisted_identity(self):
        transaction_id = ObjectId()
        plan_id = ObjectId()
        plan = {
            "_id": plan_id,
            "descricao": "Mercado Livre",
            "numero_parcelas": 8,
            "installments": [
                {"numero": 1, "status": "paga", "valor": Decimal128("46.93"), "transaction_id": ObjectId()},
                {"numero": 2, "status": "paga", "valor": Decimal128("46.93"), "transaction_id": transaction_id},
                {"numero": 3, "status": "projetada", "valor": Decimal128("46.93"), "data_vencimento": "2026-11-03"},
                {"numero": 4, "status": "projetada", "valor": Decimal128("46.93"), "data_vencimento": "2026-12-03"},
            ],
        }
        db = Mock()
        db.installment_plans.find.return_value = [plan]
        db.future_schedule_overrides.find.return_value = []
        db.audit_log.find.return_value = []
        document = {"_id": transaction_id, "installment": {"plan_id": plan_id, "numero": 2, "total": 8}}
        items = [{}]

        with patch("app.main.effective_documents", return_value=[plan]):
            with_installment_references([document], items, db)

        reference = items[0]["installment_plan"]
        self.assertEqual(reference["id"], str(plan_id))
        self.assertEqual(reference["current_number"], 2)
        self.assertEqual(reference["total"], 8)
        self.assertEqual(reference["future_count"], 2)
        self.assertEqual(reference["future_total"], 93.86)
        self.assertEqual(reference["next_due_date"], "2026-11-03")
