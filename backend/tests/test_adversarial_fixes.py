import sys, unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import MagicMock
from decimal import Decimal
from pydantic import ValidationError
from fastapi import HTTPException
from bson.decimal128 import Decimal128

sys.path.insert(0,str(Path(__file__).parents[1]))
from app.schemas import ParsedPayload
from app.transaction_service import stored_instant,TransactionService
from app.main import validate_confirmed_provisional,create_commitment
from app.commitments import CommitmentIn

class AuditContractTests(unittest.TestCase):
    def test_unknown_direction_and_merchant_types_are_rejected(self):
        for body in ({'direction':'debit'},{'merchant_raw':17},{'event_timestamp':123}):
            with self.subTest(body=body),self.assertRaises(ValidationError):ParsedPayload.model_validate(body)

    def test_final_money_rejects_subcent_zero_and_nonfinite(self):
        for value in ('0.004','0','-10','NaN','Infinity','100000001'):
            with self.subTest(value=value),self.assertRaises(ValidationError):ParsedPayload(amount=value)
        self.assertEqual(ParsedPayload(amount='0.01').amount,Decimal('0.01'))

    def test_incomplete_payload_remains_parseable_without_inventing_amount(self):
        self.assertIsNone(ParsedPayload().amount)
        self.assertIsNone(ParsedPayload().merchant_raw)

    def test_invalid_input_never_gets_written_as_pending(self):
        db=MagicMock();service=TransactionService(db)
        with self.assertRaises(ValueError):service.process({'event_id':'invalid','captured_at':datetime.now(timezone.utc),'parsed_payload':{'event_timestamp':123}})
        db.raw_events.insert_one.assert_not_called()

    def test_bson_precision_is_canonical_in_any_offset(self):
        expected=datetime(2026,9,20,15,0,0,123000,tzinfo=timezone.utc)
        self.assertEqual(stored_instant('2026-09-20T12:00:00.123456-03:00'),expected)
        self.assertEqual(stored_instant('2026-09-20T15:00:00.123999Z'),expected)

    def test_confirmation_rejects_final_zero_or_whitespace_type(self):
        for changes in ({'valor':Decimal128('-0.00')},{'tipo':' '},{'valor':None}):
            body={'valor':Decimal128('-10'),'tipo':'compra','fluxo':'saida',**changes}
            with self.subTest(changes=changes),self.assertRaises(HTTPException):validate_confirmed_provisional(body)

    def test_creation_requires_explicit_retry_identity(self):
        body=CommitmentIn(kind='assinatura',name='Service',purchase_date='2026-09-01',next_due_date='2026-09-20',amount='10.00',payment_method='pix',renewal_status='automatica')
        db=MagicMock()
        with self.assertRaises(HTTPException) as failure:create_commitment(body,db)
        self.assertEqual(failure.exception.status_code,428)
        db.commitments.insert_one.assert_not_called()

if __name__=='__main__':unittest.main()
