import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson.decimal128 import Decimal128
from app.transaction_service import EventConflict, TransactionService

class Result:
    def __init__(self,inserted_id=None): self.inserted_id=inserted_id
class Cursor(list):
    def limit(self,n): return Cursor(self[:n])
class Collection:
    def __init__(self,docs=None): self.docs=list(docs or [])
    def create_index(self,*a,**k): pass
    def _matches(self,doc,query):
        for key,want in query.items():
            value=doc.get(key)
            if isinstance(want,dict):
                if '$in' in want and value not in want['$in']: return False
                if '$gte' in want and (value is None or value<want['$gte']): return False
                if '$lte' in want and (value is None or value>want['$lte']): return False
            elif value!=want: return False
        return True
    def find_one(self,query,projection=None): return next((d for d in self.docs if self._matches(d,query)),None)
    def find(self,query): return Cursor(d for d in self.docs if self._matches(d,query))
    def insert_one(self,doc):
        if doc.get('event_id') and self.find_one({'event_id':doc['event_id']}):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError('duplicate')
        item=dict(doc);item.setdefault('_id',f'id-{len(self.docs)+1}');self.docs.append(item);return Result(item['_id'])
    def update_one(self,query,update):
        doc=self.find_one(query)
        if not doc:return
        doc.update(update.get('$set',{}))
        for k in update.get('$unset',{}): doc.pop(k,None)
        for k,v in update.get('$inc',{}).items():doc[k]=doc.get(k,0)+v
    def count_documents(self,q):return len(self.find(q))

class FakeDB(dict):
    def __missing__(self,k): self[k]=Collection();return self[k]
    def __getattr__(self,k): return self[k]

def event(event_id,kind,stamp,merchant='Padaria ABC'):
    return {'event_id':event_id,'source_type':kind,'source_provider':kind,'captured_at':stamp,'raw_payload':{'text':merchant},'parsed_payload':{'amount':26,'currency':'BRL','merchant_raw':merchant,'event_timestamp':stamp,'event_type':'card_purchase','direction':'expense'}}

class IngestionTests(unittest.TestCase):
    def test_manual_purchase_preserves_payment_account_and_is_ready_for_review(self):
        from unittest.mock import patch
        db=FakeDB();stamp=datetime(2026,10,9,14,50,45,tzinfo=timezone.utc)
        captured=event('manual-pix','manual',stamp)
        captured['parsed_payload'].update({'amount':'32.90','event_type':'manual_purchase','payment_method':'pix','account_profile_id':'santander'})
        with patch('app.transaction_service.validate_account_key') as validate:
            result=TransactionService(db).process(captured)
        self.assertEqual(result['status'],'provisional')
        validate.assert_called_once_with('santander',db,selectable=True)
        item=db.provisional_transactions.docs[0]
        self.assertEqual(item['tipo'],'compra')
        self.assertEqual(item['metodo_pagamento'],'pix')
        self.assertEqual(item['account_profile_id'],'santander')
        self.assertEqual(item['valor'].to_decimal(),__import__('decimal').Decimal('-32.90'))
        self.assertEqual(item['data_evento'],stamp)

    def test_manual_income_has_explicit_type_and_positive_amount(self):
        db=FakeDB();stamp=datetime(2026,10,9,12,tzinfo=timezone.utc)
        captured=event('income','manual',stamp)
        captured['parsed_payload'].update({'event_type':'manual_income','direction':'income'})
        TransactionService(db).process(captured)
        self.assertEqual(db.provisional_transactions.docs[0]['tipo'],'receita')
        self.assertGreater(db.provisional_transactions.docs[0]['valor'].to_decimal(),0)

    def test_four_evidences_link_to_one_legacy_transaction(self):
        from bson import ObjectId
        stamp=datetime(2026,9,23,12,tzinfo=timezone.utc)
        account_id=ObjectId()
        ledger=Collection([{'_id':'canonical-1','account_id':account_id,'valor':Decimal128('-26.00'),'data_evento':stamp,'estabelecimento_original':'PADARIA ABC','tipo':'compra',
            'bank_transaction_ref':'purchase-1','account_identity':'account-1','source_provider':'com.itau'}])
        db=FakeDB(transactions=ledger,accounts=Collection([{'_id':account_id,'instituicao':'Itaú','tipo':'conta_corrente'}]))
        service=TransactionService(db)
        for i,source in enumerate(('android_notification','receipt','financial_feed','statement')):
            evidence=event(f'e-{i}',source,stamp)
            evidence['source_provider']='com.itau'
            evidence['parsed_payload'].update(bank_transaction_ref='purchase-1',account_identity='account-1')
            if source=='android_notification':
                evidence['source_provider']='com.itau'
                evidence['raw_payload'].update({'package':'com.itau','origin_verification':'exact_package_allowlist'})
            result=service.process(evidence)
            self.assertEqual(result['status'],'matched')
        self.assertEqual(len(db['transactions'].docs),1)
        self.assertEqual(len(db['transaction_evidence'].docs),4)
        self.assertEqual(db['provisional_transactions'].docs,[])

    def test_two_capture_sources_with_same_scoped_reference_enrich_one_provisional(self):
        stamp=datetime(2026,9,23,12,tzinfo=timezone.utc)
        db=FakeDB();service=TransactionService(db)
        first=event('notification','android_notification',stamp)
        second=event('receipt','receipt',stamp)
        for evidence in (first,second):
            evidence['source_provider']='synthetic-bank'
            evidence['parsed_payload'].update(bank_transaction_ref='purchase-1',account_identity='account-1')
        self.assertEqual(service.process(first)['status'],'provisional')
        self.assertEqual(service.process(second)['status'],'matched')
        self.assertEqual(len(db['provisional_transactions'].docs),1)
        self.assertEqual(db['provisional_transactions'].docs[0]['status'],'enriched')
        self.assertEqual(len(db['transaction_evidence'].docs),1)

    def test_retries_are_idempotent_and_payload_conflicts_rejected(self):
        stamp=datetime(2026,9,23,12,tzinfo=timezone.utc)
        service=TransactionService(FakeDB());first=event('retry-id','manual',stamp)
        service.process(first);again=service.process(first)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['status'],'provisional')
        with self.assertRaises(EventConflict): service.process({**first,'raw_payload':{'text':'changed'}})

    def test_manual_own_transfer_remains_one_reviewable_capture(self):
        stamp=datetime(2026,9,28,12,tzinfo=timezone.utc)
        db=FakeDB()
        captured=event('own-transfer','manual',stamp,'Itaú → Santander')
        captured['parsed_payload'].update({'amount':200,'event_type':'own_transfer','from_account_key':'itau','to_account_key':'santander'})
        result=TransactionService(db).process(captured)
        self.assertEqual(result['status'],'provisional')
        item=db.provisional_transactions.docs[0]
        self.assertEqual(item['natureza'],'TRANSFERENCIA')
        self.assertEqual(item['transfer']['from_account_key'],'itau')
        self.assertEqual(item['transfer']['to_account_key'],'santander')
        self.assertNotIn('merchant_id',item)

if __name__=='__main__':unittest.main()
