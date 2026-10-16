import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0,str(Path(__file__).parents[1]))
from observed_contract import observed_mock_payment
from app.commitments import CommitmentIn, advance, future_pending_total, schedule, stored_document
from app.main import CommitmentPaymentLink, commitment_output, link_commitment_payment


class CommitmentTests(unittest.TestCase):
    def base(self, **changes):
        value = dict(kind="parcelamento", name="Compra", purchase_date=date(2026, 1, 1),
                     next_due_date=date(2026, 1, 31), amount=Decimal("29.90"),
                     frequency="mensal", installment_count=3, next_installment_number=1,
                     payment_method="pix", renewal_status="nao_aplicavel")
        value.update(changes)
        return CommitmentIn(**value)

    def test_month_end_schedule_and_finite_count(self):
        item=stored_document(self.base())
        occurrences=schedule(item)
        self.assertEqual([x["due_date"] for x in occurrences],
                         ["2026-01-31", "2026-02-28", "2026-03-31"])
        self.assertEqual([x["installment_number"] for x in occurrences], [1,2,3])

    def test_future_cutoff_drops_the_cutoff_day_and_later_charges(self):
        item=stored_document(self.base())
        item['future_cutoff']='2026-03-01'
        self.assertEqual([row['due_date'] for row in schedule(item)], ['2026-01-31', '2026-02-28'])

    def test_historical_link_on_the_next_charge_empties_the_schedule(self):
        item=stored_document(self.base())
        item['linked_transactions']=[{'due_date':'2026-01-31','occurrence_key':'occ','settled_next':False}]
        self.assertEqual(schedule(item), [])

    def test_obligation_remainder_stays_on_the_last_parcel(self):
        item=stored_document(CommitmentIn(kind='obrigacao', name='Tributo', purchase_date=date(2026, 10, 1),
            next_due_date=date(2026, 10, 10), cash_due_date=date(2026, 10, 10), obligation_plan='parcelado',
            amount=Decimal('100'), frequency='mensal', installment_count=3, next_installment_number=1,
            payment_method='pix', renewal_status='nao_aplicavel'))
        self.assertEqual([row['amount'] for row in schedule(item)], [33.33, 33.33, 33.34])

    def test_invalid_installment_and_card_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.base(installment_count=3, next_installment_number=4)
        with self.assertRaises(ValidationError):
            self.base(payment_method="cartao")

    def test_paused_has_no_projection_and_annual_leap_date(self):
        item=stored_document(self.base(status="pausado"))
        self.assertEqual(schedule(item), [])
        self.assertEqual(advance(date(2024,2,29),"anual",1),date(2025,2,28))

    def test_nonrenewing_subscription_only_projects_remaining_charges(self):
        item=stored_document(CommitmentIn(kind="assinatura",name="Serviço",purchase_date=date(2026,1,1),
            next_due_date=date(2026,9,25),amount=Decimal("29.90"),frequency="mensal",
            payment_method="pix",renewal_status="nao_renovar",remaining_charges=2,
            end_date=date(2026,10,31)))
        self.assertEqual(len(schedule(item)),2)
        item["remaining_charges"]=0
        self.assertEqual(schedule(item),[])

    def test_manual_renewal_only_projects_next_charge(self):
        item=stored_document(CommitmentIn(kind="assinatura",name="Serviço",purchase_date=date(2026,1,1),
            next_due_date=date(2026,9,25),amount=Decimal("29.90"),frequency="anual",
            payment_method="pix",renewal_status="manual"))
        self.assertEqual(len(schedule(item)),1)

    def test_dates_are_bson_dates_but_api_keeps_date_only_format(self):
        item=stored_document(self.base())
        self.assertEqual(item["purchase_date"],datetime(2026,1,1,tzinfo=timezone.utc))
        self.assertEqual(item["next_due_date"],datetime(2026,1,31,tzinfo=timezone.utc))
        output=commitment_output(item)
        self.assertEqual(output["purchase_date"],"2026-01-01")
        self.assertEqual(output["next_due_date"],"2026-01-31")
        self.assertEqual(output["future_pending_total"],89.7)

    def test_cancelled_has_no_future_pending_total(self):
        item=stored_document(self.base(status="cancelado"))
        self.assertEqual(future_pending_total(item),Decimal("0"))

    def test_cancelled_subscription_with_remaining_charges_requires_review(self):
        with self.assertRaises(ValidationError):
            self.base(kind="assinatura",installment_count=None,next_installment_number=None,
                renewal_status="nao_renovar",remaining_charges=6,status="cancelado")
        item=stored_document(self.base(kind="assinatura",installment_count=None,next_installment_number=None,
            renewal_status="nao_renovar",remaining_charges=6))
        item["status"]="cancelado"
        self.assertTrue(commitment_output(item)["future_pending_requires_review"])
        self.assertIsNone(commitment_output(item)["future_pending_total"])

    def test_real_expense_link_advances_installment_without_creating_cash_entry(self):
        item=stored_document(self.base())
        item["_id"]=ObjectId()
        tx_id=ObjectId()
        db=MagicMock()
        db.commitments.find_one.side_effect=[item,None]
        db.transactions.find_one.return_value={"_id":tx_id,"valor":Decimal("-29.90"),"fluxo":"saida","data_evento":datetime(2026,1,31,tzinfo=timezone.utc),"classification":{"migration_reason":"despesa_real"}}
        result=link_commitment_payment(item["commitment_id"],observed_mock_payment(CommitmentPaymentLink(transaction_id=str(tx_id)),db,item),db)
        self.assertEqual(result["payment"]["due_date"],"2026-01-31")
        update=db.commitments.update_one.call_args.args[1]
        self.assertEqual(update["$set"]["next_due_date"],datetime(2026,2,28,tzinfo=timezone.utc))
        self.assertEqual(update["$set"]["next_installment_number"],2)
        db.transactions.insert_one.assert_not_called()

    def test_invoice_payment_cannot_settle_commitment(self):
        item=stored_document(self.base())
        item["_id"]=ObjectId()
        db=MagicMock()
        db.commitments.find_one.side_effect=[item,None]
        db.transactions.find_one.return_value={"_id":ObjectId(),"valor":Decimal("-29.90"),"fluxo":"saida","classification":{"migration_reason":"pagamento_fatura"}}
        with self.assertRaises(HTTPException) as error:
            link_commitment_payment(item["commitment_id"],observed_mock_payment(CommitmentPaymentLink(transaction_id=str(ObjectId())),db,item),db)
        self.assertEqual(error.exception.status_code,422)
        db.commitments.update_one.assert_not_called()

    def test_confirmed_capture_can_link_historically_without_advancing(self):
        item=stored_document(self.base(kind="assinatura",installment_count=None,next_installment_number=None,
            renewal_status="nao_renovar",remaining_charges=6,next_due_date=date(2026,9,26)))
        item["status"]="cancelado"  # Historical inconsistent record, now flagged for review.
        item["_id"]=ObjectId()
        db=MagicMock()
        db.commitments.find_one.side_effect=[item,None]
        db.provisional_transactions.find_one.return_value={"provisional_id":"capture-1","status":"confirmed",
            "valor":Decimal("-29.90"),"fluxo":"saida","data_evento":datetime(2026,9,25,tzinfo=timezone.utc),
            "merchant_raw":"PAYPAL.PAYPAL ."}
        result=link_commitment_payment(item["commitment_id"],observed_mock_payment(CommitmentPaymentLink(provisional_id="capture-1"),db,item),db)
        self.assertFalse(result["payment"]["settled_next"])
        update=db.commitments.update_one.call_args.args[1]
        self.assertNotIn("next_due_date",update["$set"])
        self.assertEqual(update["$push"]["linked_transactions"]["provisional_id"],"capture-1")

    def test_monthly_commitment_rejects_second_payment_in_same_month(self):
        item=stored_document(self.base(kind="assinatura",installment_count=None,next_installment_number=None,
            renewal_status="automatica",next_due_date=date(2026,9,26)))
        item["_id"]=ObjectId()
        item["linked_transactions"]=[{"transaction_id":str(ObjectId()),"paid_date":"2026-09-25"}]
        db=MagicMock()
        db.commitments.find_one.side_effect=[item,None]
        db.transactions.find_one.return_value={"_id":ObjectId(),"valor":Decimal("-29.90"),"fluxo":"saida",
            "data_evento":datetime(2026,9,26,tzinfo=timezone.utc),"classification":{"migration_reason":"despesa_real"}}
        with self.assertRaises(HTTPException) as error:
            link_commitment_payment(item["commitment_id"],observed_mock_payment(CommitmentPaymentLink(transaction_id=str(ObjectId())),db,item),db)
        self.assertEqual(error.exception.status_code,409)
        db.commitments.update_one.assert_not_called()


if __name__ == "__main__": unittest.main()
