import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitments import CommitmentIn, future_pending_total, schedule, stored_document


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
        obligation_plan="a_vista",
    )
    fields.update(extra)
    return stored_document(CommitmentIn(**fields))


class ObligationScheduleTests(unittest.TestCase):
    def test_cash_plan_is_one_charge_of_the_total(self):
        item = obligation(next_due_date=date(2026, 11, 20), cash_due_date=date(2026, 11, 20))
        rows = schedule(item, 12)
        self.assertEqual([(row["due_date"], row["amount"]) for row in rows], [("2026-11-20", 100.0)])
        self.assertEqual(future_pending_total(item), Decimal("100.00"))
        self.assertEqual(item["obligation_plan"], "a_vista")
        self.assertEqual(item["cash_due_date"].date(), date(2026, 11, 20))

    def test_installments_put_the_remainder_on_the_last_parcel(self):
        item = obligation(
            obligation_plan="parcelado",
            installment_count=3,
            next_installment_number=1,
            next_due_date=date(2026, 11, 10),
        )
        rows = schedule(item, 12)
        self.assertEqual([row["amount"] for row in rows], [33.33, 33.33, 33.34])
        self.assertEqual([row["due_date"] for row in rows], ["2026-11-10", "2026-12-10", "2027-01-10"])
        self.assertEqual(future_pending_total(item), Decimal("100.00"))

    def test_end_date_keeps_the_chosen_month_and_drops_the_next(self):
        item = obligation(
            obligation_plan="parcelado",
            installment_count=3,
            next_installment_number=1,
            next_due_date=date(2026, 11, 10),
            end_date=date(2026, 12, 10),
        )
        rows = schedule(item, 12)
        self.assertEqual([row["due_date"] for row in rows], ["2026-11-10", "2026-12-10"])
        self.assertEqual(future_pending_total(item), Decimal("66.66"))

    def test_subscription_document_does_not_store_the_obligation_plan(self):
        item = stored_document(CommitmentIn(
            kind="assinatura", name="Serviço", purchase_date=date(2026, 1, 1),
            next_due_date=date(2026, 11, 10), amount=Decimal("10.00"), frequency="mensal",
            payment_method="pix", renewal_status="automatica",
        ))
        self.assertNotIn("obligation_plan", item)
        self.assertNotIn("cash_due_date", item)

    def test_cash_plan_rejects_installment_numbers(self):
        with self.assertRaises(ValueError):
            CommitmentIn(
                kind="obrigacao", name="IPVA", purchase_date=date(2026, 10, 11),
                next_due_date=date(2026, 11, 10), cash_due_date=date(2026, 11, 20),
                amount=Decimal("100.00"), payment_method="pix", renewal_status="nao_aplicavel",
                obligation_plan="a_vista", installment_count=3, next_installment_number=1,
            )


if __name__ == "__main__":
    unittest.main()
