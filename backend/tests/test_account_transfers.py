import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.account_positions import account_positions, reconcile_capture_transfer
from app.main import ProvisionalUpdate, update_provisional, validate_confirmed_provisional
from test_ingestion import Collection, FakeDB


class ProjectedCollection(Collection):
    def find(self, query, projection=None):
        return super().find(query)


class AccountTransferTests(unittest.TestCase):
    def test_review_replaces_vendor_with_two_accounts(self):
        capture={"_id":ObjectId(),"provisional_id":"capture-1","status":"confirmed","natureza":"TRANSFERENCIA","valor":Decimal128("-200.00"),"fluxo":"saida","tipo":"pix","merchant_raw":"Banco Santander","merchant_id":ObjectId(),"merchant_name":"Pessoa sintética"}
        db=FakeDB(provisional_transactions=Collection([capture]))
        result=update_provisional("capture-1",ProvisionalUpdate(vendedor="",from_account_key="itau",to_account_key="santander"),db)
        self.assertTrue(result["changed"])
        self.assertEqual(result["item"]["transfer"]["from_account_key"],"itau")
        self.assertEqual(result["item"]["transfer"]["to_account_key"],"santander")
        self.assertNotIn("merchant_id",capture)
        self.assertNotIn("merchant_name",capture)
        validate_confirmed_provisional(capture)

    def test_same_account_is_rejected(self):
        capture={"_id":ObjectId(),"provisional_id":"capture-2","status":"provisional","natureza":"TRANSFERENCIA","valor":Decimal128("-200.00"),"fluxo":"saida","tipo":"transferencia"}
        db=FakeDB(provisional_transactions=Collection([capture]))
        with self.assertRaises(HTTPException) as error:
            update_provisional("capture-2",ProvisionalUpdate(from_account_key="itau",to_account_key="itau"),db)
        self.assertEqual(error.exception.status_code,422)
        self.assertNotIn("transfer",capture)

    def test_balance_counts_each_transfer_leg_after_reference(self):
        now=datetime.now(timezone.utc)
        accounts=ProjectedCollection([
            {"_id":ObjectId(),"instituicao":"Itaú","tipo":"conta_corrente","status":"ativo"},
            {"_id":ObjectId(),"instituicao":"Banco Santander","tipo":"conta_corrente","status":"ativo"},
        ])
        snapshots=ProjectedCollection([
            {"account_key":"itau","amount":Decimal128("1000.00"),"as_of":now-timedelta(days=1)},
            {"account_key":"santander","amount":Decimal128("500.00"),"as_of":now-timedelta(days=1)},
        ])
        captures=ProjectedCollection([{"status":"confirmed","data_evento":now,"valor":Decimal128("-200.00"),"natureza":"TRANSFERENCIA","transfer":{"from_account_key":"itau","to_account_key":"santander"}}])
        db=FakeDB(accounts=accounts,account_balance_snapshots=snapshots,transactions=ProjectedCollection(),provisional_transactions=captures)
        rows={row["key"]:row for row in account_positions(db)}
        self.assertEqual(rows["itau"]["balance"],800)
        self.assertEqual(rows["santander"]["balance"],700)
        self.assertEqual(rows["inter"]["balance_status"],"sem_referencia")

    def test_reconciliation_requires_unique_bank_leg(self):
        now=datetime.now(timezone.utc)
        origin=ObjectId();destination=ObjectId()
        capture={"_id":ObjectId(),"status":"confirmed","data_evento":now,"valor":Decimal128("-200.00"),"transfer":{"from_account_key":"itau","to_account_key":"santander"}}
        db=FakeDB(accounts=ProjectedCollection([{"_id":origin,"instituicao":"Itaú","tipo":"conta_corrente"},{"_id":destination,"instituicao":"Santander","tipo":"conta_corrente"}]),transactions=ProjectedCollection([{"_id":ObjectId(),"account_id":origin,"data_evento":now,"valor":Decimal128("-200.00"),"descricao_original":"PIX enviado Santander"},{"_id":ObjectId(),"account_id":destination,"data_evento":now,"valor":Decimal128("200.00"),"descricao_original":"PIX recebido Itaú"}]),provisional_transactions=ProjectedCollection())
        transfer,changed=reconcile_capture_transfer(db,capture)
        self.assertTrue(changed)
        self.assertEqual(transfer["reconciliation_status"],"conciliada")

    def test_reconciled_leg_is_not_counted_twice_in_balance(self):
        now=datetime.now(timezone.utc)
        origin=ObjectId();source_id=ObjectId()
        db=FakeDB(
            accounts=ProjectedCollection([{"_id":origin,"instituicao":"Itaú","tipo":"conta_corrente"}]),
            account_balance_snapshots=ProjectedCollection([{"account_key":"itau","amount":Decimal128("1000.00"),"as_of":now-timedelta(days=1)}]),
            transactions=ProjectedCollection([{"_id":source_id,"account_id":origin,"data_evento":now,"valor":Decimal128("-200.00")}]),
            provisional_transactions=ProjectedCollection([{"status":"confirmed","data_evento":now,"valor":Decimal128("-200.00"),"natureza":"TRANSFERENCIA","transfer":{"from_account_key":"itau","to_account_key":"santander","source_transaction_id":str(source_id),"reconciliation_status":"parcial"}}]),
        )
        row=next(row for row in account_positions(db) if row["key"]=="itau")
        self.assertEqual(row["balance"],800)
        self.assertEqual(row["movement_count"],1)

    def test_same_amount_transfer_peers_remain_for_review(self):
        now=datetime.now(timezone.utc)
        capture={"_id":ObjectId(),"status":"confirmed","data_evento":now,"valor":Decimal128("-200.00"),"transfer":{"from_account_key":"itau","to_account_key":"santander"}}
        peer={"_id":ObjectId(),"status":"confirmed","data_evento":now,"valor":Decimal128("-200.00"),"transfer":{"from_account_key":"itau","to_account_key":"santander"}}
        db=FakeDB(accounts=ProjectedCollection(),transactions=ProjectedCollection(),provisional_transactions=ProjectedCollection([capture,peer]))
        transfer,changed=reconcile_capture_transfer(db,capture)
        self.assertFalse(changed)
        self.assertNotIn("source_transaction_id",transfer)


if __name__=="__main__": unittest.main()
