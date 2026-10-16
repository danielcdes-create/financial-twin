import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0,str(Path(__file__).parents[1]))
from app.account_profiles import (AccountProfileCreate, AccountProfileUpdate, AccountProfileDelete,
    account_profiles, account_usage, create_profile, update_profile, delete_profile, validate_account_key)
from app.account_positions import account_positions, reconcile_capture_transfer
from app.capture_batches import account_context
from app.main import app, accounts, movements, ProvisionalUpdate, update_provisional
from app.auth import require_session
from app.transaction_service import TransactionService
from test_capture_batches import BatchDB, BatchCollection
from test_transaction_review import ReviewCursor
from test_ingestion import event
from test_architecture_failures import rollback_fake, FailsOnce


class ProfileCursor(ReviewCursor):
    def skip(self,count): return ProfileCursor(self[count:])
    def limit(self,count): return ProfileCursor(self[:count] if count else self)


class ProfileCollection(BatchCollection):
    def find(self,query,projection=None):
        return ProfileCursor(row for row in self.docs if self._matches(row,query))


class ProfileDB(BatchDB):
    def __missing__(self,key):
        self[key]=ProfileCollection()
        return self[key]


class AccountProfileTests(unittest.TestCase):
    def setUp(self):
        self.db = ProfileDB()
        self.body = AccountProfileCreate(request_id=uuid4(), instituicao='Itaú', apelido='Reserva',
            tipo='poupanca', agencia='0012', numero='00001234', numero_digito='X')

    def create(self, **changes):
        return create_profile(self.db, AccountProfileCreate.model_validate({**self.body.model_dump(), **changes}))

    def edit(self, item, **changes):
        fields = {key: item[key] for key in AccountProfileUpdate.model_fields if key in item}
        return update_profile(self.db,item['key'],AccountProfileUpdate(**{**fields, 'expected_revision': item['revision'], **changes}))

    def test_two_accounts_same_bank_have_distinct_keys_and_balances(self):
        first=self.create()
        second=self.create(request_id=uuid4(),apelido='Dia a dia')
        self.assertNotEqual(first['key'],second['key'])
        now=datetime.now(timezone.utc)
        for item, amount in ((first,'1000'),(second,'2000')):
            self.db.account_balance_snapshots.docs.append({'account_key':item['key'],'amount':Decimal128(amount),'as_of':now-timedelta(days=1)})
        self.db.provisional_transactions.docs.append({'status':'confirmed','data_evento':now,'valor':Decimal128('-100'),
            'transfer':{'from_account_key':first['key'],'to_account_key':second['key']}})
        rows={item['key']:item for item in account_positions(self.db)}
        self.assertEqual(rows[first['key']]['balance'],900)
        self.assertEqual(rows[second['key']]['balance'],2100)
        self.assertIsNone(rows['itau']['balance'])

    def test_create_retry_is_idempotent_and_changed_payload_conflicts(self):
        first=self.create()
        self.assertEqual(first,self.create())
        self.assertEqual(len(self.db.audit_log.docs),1)
        with self.assertRaises(HTTPException) as error:self.create(apelido='Outro')
        self.assertEqual(error.exception.status_code,409)

    def test_edit_keeps_imported_identity_ledger_and_balance_unchanged(self):
        identity=ObjectId();now=datetime.now(timezone.utc)
        legacy={'_id':identity,'instituicao':'Itaú','tipo':'conta_corrente','status':'ativo'}
        tx={'_id':ObjectId(),'account_id':identity,'data_evento':now,'valor':Decimal128('-75')}
        self.db.accounts.docs.append(copy.deepcopy(legacy));self.db.transactions.docs.append(copy.deepcopy(tx))
        self.db.account_balance_snapshots.docs.append({'account_key':'itau','amount':Decimal128('1000'),'as_of':now-timedelta(days=1)})
        item=next(item for item in account_profiles(self.db) if item['key']=='itau')
        updated=self.edit(item,apelido='Principal',instituicao='Itaú Unibanco',tipo='investimento',numero='001122')
        row=next(row for row in account_positions(self.db) if row['key']=='itau')
        self.assertEqual(updated['revision'],2)
        self.assertEqual(row['balance'],925)
        self.assertEqual(row['legacy_account_id'],str(identity))
        self.assertEqual(self.db.accounts.find_one({'_id':identity}),legacy)
        self.assertEqual(self.db.transactions.docs,[tx])
        self.assertEqual(len(accounts(self.db)['items']),1)

    def test_stale_edit_and_delete_rejected(self):
        item=self.create();self.edit(item,apelido='Alterada')
        with self.assertRaises(HTTPException) as error:self.edit(item,apelido='Velha')
        self.assertEqual(error.exception.status_code,409)
        with self.assertRaises(HTTPException) as error:delete_profile(self.db,item['key'],AccountProfileDelete(expected_revision=1))
        self.assertEqual(error.exception.status_code,409)

    def test_close_archive_restore_preserve_key_and_history(self):
        item=self.create()
        closed=self.edit(item,status='encerrada',data_encerramento='2026-09-30')
        self.assertEqual(validate_account_key(item['key'],self.db)['status'],'encerrada')
        with self.assertRaises(HTTPException):validate_account_key(item['key'],self.db,selectable=True)
        archived=self.edit(closed,archived=True)
        self.assertTrue(archived['archived'])
        restored=self.edit(archived,archived=False)
        self.assertEqual(restored['key'],item['key'])
        self.assertEqual(restored['status'],'encerrada')
        reopened=self.edit(restored,status='aberta',data_encerramento=None)
        self.assertEqual(validate_account_key(reopened['key'],self.db,selectable=True)['revision'],5)

    def test_empty_delete_never_resurrects_builtin_or_custom(self):
        custom=self.create()
        for key in ('binance',custom['key']):
            delete_profile(self.db,key,AccountProfileDelete(expected_revision=1))
            self.assertNotIn(key,{item['key'] for item in account_profiles(self.db)})
            with self.assertRaises(HTTPException):validate_account_key(key,self.db)
        with self.assertRaises(HTTPException):self.create()

    def test_deletion_blocked_for_each_financial_link(self):
        key=self.create()['key']
        cases=[('account_balance_snapshots',{'account_key':key}),
            ('provisional_transactions',{'account_profile_id':key,'status':'cancelled'}),
            ('provisional_transactions',{'transfer':{'from_account_key':key}}),
            ('provisional_transactions',{'transfer':{'to_account_key':key}}),
            ('raw_events',{'parsed_payload':{'from_account_key':key}}),
            ('capture_batches',{'account_ref':'profile:'+key}),
            ('deleted_movements',{'document':{'account_profile_id':key}}),
            ('saving_experiments',{'account_key':key})]
        for name,row in cases:
            with self.subTest(name=name,row=row):
                self.db[name].docs.append(row)
                self.assertTrue(account_usage(self.db,key))
                with self.assertRaises(HTTPException) as error:delete_profile(self.db,key,AccountProfileDelete(expected_revision=1))
                self.assertEqual(error.exception.status_code,409)
                self.db[name].docs.clear()

    def test_duplicate_imported_bank_accounts_are_not_collapsed(self):
        now=datetime.now(timezone.utc)
        for amount in ('100','200'):
            identity=ObjectId()
            self.db.accounts.docs.append({'_id':identity,'tipo':'conta_corrente','instituicao':'Itaú'})
            self.db.transactions.docs.append({'account_id':identity,'data_evento':now,'valor':Decimal128(amount)})
        self.db.account_balance_snapshots.docs.append({'account_key':'itau','amount':Decimal128('1000'),'as_of':now-timedelta(days=1)})
        row=next(row for row in account_positions(self.db) if row['key']=='itau')
        self.assertIsNone(row['legacy_account_id'])
        self.assertEqual(row['balance'],1000)
        self.assertEqual(account_context(self.db,'profile:itau')['ids'],[])
        self.assertTrue(account_usage(self.db,'itau')['cadastro_historico'])

    def test_bank_name_alone_does_not_reconcile_between_accounts_in_same_bank(self):
        item=self.create()
        now=datetime.now(timezone.utc)
        identity=ObjectId()
        self.db.accounts.docs.append({'_id':identity,'tipo':'conta_corrente','instituicao':'Itaú'})
        self.db.transactions.docs.append({'_id':ObjectId(),'account_id':identity,'data_evento':now,
            'valor':Decimal128('-100'),'descricao_original':'Transferência Itaú'})
        capture={'_id':ObjectId(),'status':'confirmed','data_evento':now,'valor':Decimal128('-100'),
            'transfer':{'from_account_key':'itau','to_account_key':item['key']}}
        _,changed=reconcile_capture_transfer(self.db,capture)
        self.assertFalse(changed)

    def test_new_accounts_work_in_ingestion_review_batch_and_statement_filter(self):
        first=self.create();second=self.create(request_id=uuid4())
        now=datetime.now(timezone.utc)
        payload=event('profile-transfer','manual',now,'Reserva → Outra')
        payload['parsed_payload'].update({'event_type':'own_transfer','from_account_key':first['key'],'to_account_key':second['key'],'amount':100})
        self.assertEqual(TransactionService(self.db).process(payload)['status'],'provisional')
        capture=self.db.provisional_transactions.docs[0]
        capture.update({'status':'confirmed','revision':1})
        other={'_id':ObjectId(),'provisional_id':'expense','status':'provisional','revision':1,'valor':Decimal128('-10'),'data_evento':now,'fluxo':'saida','tipo':'compra'}
        self.db.provisional_transactions.docs.append(other)
        update_provisional('expense',ProvisionalUpdate(account_profile_id=first['key'],expected_revision=1),self.db)
        self.assertEqual(other['account_profile_id'],first['key'])
        self.assertEqual(account_context(self.db,'profile:'+first['key'])['profile'],first['key'])
        for cursor_mode in (False,True):
            result=movements(limit=20,offset=0,cursor_mode=cursor_mode,cursor=None,search=None,category=None,
                account_id='profile:'+second['key'],date_from=None,date_to=None,db=self.db)
            self.assertEqual([row['provisional_id'] for row in result['items']],[capture['provisional_id']])
        self.edit(first,status='encerrada')
        payload={**payload,'event_id':'closed-transfer'}
        with self.assertRaises(ValueError):TransactionService(self.db).process(payload)

    def test_date_and_digit_validation(self):
        for changes in ({'data_abertura':'2026-10-01','data_encerramento':'2026-09-01','status':'encerrada'},
            {'data_encerramento':'2026-09-01'}, {'numero':'','numero_digito':'9'},
            {'agencia_digito':'0','agencia':''},{'moeda':'USD'},{'instituicao':'   '}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                AccountProfileCreate.model_validate({**self.body.model_dump(),**changes})

    def test_atomic_audit_failure_rolls_back_profile_write(self):
        self.db.account_profiles
        self.db.audit_log=FailsOnce()
        with patch('app.main.mongo_transaction',lambda db:rollback_fake(db)):
            from app.main import add_account_profile
            with self.assertRaises(RuntimeError):add_account_profile(self.body,self.db)
        self.assertEqual(len(account_profiles(self.db)),6)

    def test_http_routes_validate_and_protect_history(self):
        from app.main import database
        app.dependency_overrides[require_session]=lambda:True
        app.dependency_overrides[database]=lambda:self.db
        try:
            client=TestClient(app)
            response=client.post('/v1/account-profiles',json=self.body.model_dump(mode='json'))
            self.assertEqual(response.status_code,201)
            key=response.json()['item']['key']
            preview=client.get(f'/v1/account-profiles/{key}/deletion')
            self.assertTrue(preview.json()['can_delete'])
            self.db.provisional_transactions.docs.append({'account_profile_id':key})
            response=client.request('DELETE',f'/v1/account-profiles/{key}',json={'expected_revision':1})
            self.assertEqual(response.status_code,409)
            self.assertTrue(validate_account_key(key,self.db))
        finally:app.dependency_overrides.clear()


if __name__=='__main__':unittest.main()
