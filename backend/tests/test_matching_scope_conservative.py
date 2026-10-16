"""Unknown bank provenance and unscoped references cannot authorize absorption."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from app.matching_scope import compatible, strong_reference
from app.transaction_service import TransactionService
from test_capture_batches import BatchDB


class ConservativeMatchingScopeTests(unittest.TestCase):
    def fixture(self, account='ACCOUNT-1', provider='statement-import'):
        db=BatchDB()
        stamp=datetime(2026,10,1,15,tzinfo=timezone.utc)
        account_id=ObjectId()
        db.accounts.docs.append({'_id':account_id,'instituicao':'Itaú','tipo':'conta_corrente'})
        peer={'_id':ObjectId(),'account_id':account_id,'data_evento':stamp,'valor':Decimal128('-26.00'),
              'fluxo':'saida','tipo':'compra','descricao_original':'PADARIA SINTÉTICA',
              'bank_transaction_ref':'BANK-REF-1','source_provider':provider,
              'classification':{'migration_reason':'despesa_real'}}
        if account is not None:peer['account_identity']=account
        db.transactions.docs.append(peer)
        event={'event_id':str(uuid4()),'source_type':'csv','source_provider':provider,
               'captured_at':stamp,'raw_payload':{},'parsed_payload':{'amount':'26.00','currency':'BRL',
               'merchant_raw':'PADARIA SINTÉTICA','event_timestamp':stamp.isoformat(),
               'direction':'expense','event_type':'card_purchase','bank_transaction_ref':'BANK-REF-1'}}
        if account is not None:event['parsed_payload']['account_identity']=account
        return db,event,peer

    def test_same_reference_without_known_account_remains_reviewable(self):
        for account in (None,'','   '):
            with self.subTest(account=account):
                db,event,peer=self.fixture(account)
                self.assertFalse(strong_reference(event,peer))
                self.assertFalse(compatible(event,peer,db))
                result=TransactionService(db).process(event)
                self.assertNotEqual(result['status'],'matched')
                self.assertEqual(db.transaction_evidence.docs,[])
                self.assertEqual(len(db.provisional_transactions.docs),1)

    def test_same_reference_in_different_accounts_or_providers_is_not_absorbed(self):
        for field,value in (('account_identity','ACCOUNT-2'),('source_provider','another-bank')):
            with self.subTest(field=field):
                db,event,peer=self.fixture()
                if field=='source_provider':event[field]=value
                else:event['parsed_payload'][field]=value
                self.assertFalse(strong_reference(event,peer))
                self.assertNotEqual(TransactionService(db).process(event)['status'],'matched')
                self.assertEqual(db.transaction_evidence.docs,[])

    def test_same_account_provider_and_reference_still_reconcile(self):
        db,event,peer=self.fixture()
        self.assertTrue(strong_reference(event,peer))
        self.assertEqual(TransactionService(db).process(event)['status'],'matched')
        self.assertEqual(len(db.transaction_evidence.docs),1)
        self.assertEqual(db.provisional_transactions.docs,[])

    def test_unknown_notification_package_stays_reviewable_without_inventing_account(self):
        db,event,peer=self.fixture(provider='com.unknown.bank')
        event.update(source_type='android_notification',raw_payload={'package':'com.unknown.bank'})
        event['parsed_payload'].pop('bank_transaction_ref')
        event['parsed_payload'].pop('account_identity')
        self.assertFalse(compatible(event,peer,db))
        result=TransactionService(db).process(event)
        self.assertNotEqual(result['status'],'matched')
        self.assertEqual(db.transaction_evidence.docs,[])
        self.assertNotIn('account_profile_id',db.provisional_transactions.docs[0])

    def test_known_notification_package_requires_same_bank_and_exact_package_origin(self):
        for package,provider,bank,matched in (
            ('com.itau','com.itau','Itaú',True),
            ('com.itau','com.santander.app','Itaú',False),
            ('com.itau','com.itau','Santander',False),
        ):
            with self.subTest(package=package,provider=provider,bank=bank):
                db,event,peer=self.fixture(provider=provider)
                db.accounts.docs[0]['instituicao']=bank
                event.update(source_type='android_notification',raw_payload={'package':package})
                result=TransactionService(db).process(event)
                self.assertEqual(result['status']=='matched',matched)
                self.assertEqual(len(db.transaction_evidence.docs),int(matched))


if __name__=='__main__':unittest.main()
