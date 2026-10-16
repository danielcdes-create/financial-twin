"""B01-B09 controls: finance boundaries, history and stale user intent."""
import copy
import sys
import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from uuid import uuid4
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.account_positions import account_positions
from app.account_profiles import account_usage, movement_account_context, delete_profile, AccountProfileDelete
from app.cards import BillEdit, edit_bill
from app.financial_time import civil_date, local_date, day_start
from app.month_close import view
from app.future_commitments import digest as source_digest
from app.subcategories import SubcategoryRename, rename_subcategory, delete_subcategory, list_subcategories
from test_account_profiles import ProfileDB
import test_month_close as month_fixture
import test_cards as card_fixture
import test_subcategories as subcategory_fixture


class Audit1203Tests(unittest.TestCase):
    def test_reference_preserves_informed_civil_day_in_positions(self):
        db = ProfileDB()
        db.account_balance_snapshots.docs.append({'account_key':'itau',
            'as_of':day_start(date(2026,10,3)), 'amount':Decimal128('70.07'),
            'reference_kind':'civil_day', 'as_of_date':'2026-10-02'})
        def position(): return next(item for item in account_positions(db) if item['key']=='itau')
        self.assertEqual(position()['balance_reference_date'],'2026-10-02')
        self.assertEqual(position()['balance'],70.07)
        self.assertEqual(position()['balance_as_of'],'2026-10-03T03:00:00+00:00')
        db.account_balance_snapshots.docs[0]['reference_kind']='instant'
        self.assertIsNone(position()['balance_reference_date'])
        db.account_balance_snapshots.docs[0].pop('reference_kind')
        self.assertIsNone(position()['balance_reference_date'])

    def fixture(self, cls):
        fixture = cls()
        fixture.setUp()
        return fixture

    def test_B01_date_boundary_includes_debit_capture_and_transfer(self):
        cutoff = day_start(date(2026, 9, 15))
        db = ProfileDB()
        bank = ObjectId()
        db.accounts.docs.append({'_id': bank, 'tipo':'conta_corrente', 'instituicao':'Itaú'})
        db.account_balance_snapshots.docs.append({'account_key':'itau','as_of':cutoff,'amount':Decimal128('100'), 'reference_kind':'civil_day'})
        db.transactions.docs.append({'_id':ObjectId(),'account_id':bank,'data_evento':cutoff,'valor':Decimal128('-20')})
        db.provisional_transactions.docs.extend([
            {'_id':ObjectId(),'status':'confirmed','account_profile_id':'itau','data_evento':cutoff,'valor':Decimal128('-13')},
            {'_id':ObjectId(),'status':'confirmed','data_evento':cutoff,'valor':Decimal128('-7'),'transfer':{'from_account_key':'itau','to_account_key':'inter'}}])
        def position(): return next(item for item in account_positions(db) if item['key']=='itau')
        self.assertEqual(position()['balance'], 60)
        self.assertEqual(position()['movement_count'], 3)
        db.account_balance_snapshots.docs[0]['reference_kind'] = 'instant'
        self.assertEqual(position()['balance'], 100)
        db.account_balance_snapshots.docs[0].pop('reference_kind')
        self.assertEqual(position()['balance'], 100)  # Unlabelled historical references retain their semantics.

    def test_B02_original_and_profile_bridge_respects_ambiguity(self):
        db = ProfileDB(); oid=ObjectId()
        db.accounts.docs.append({'_id':oid, 'tipo':'conta_corrente','instituicao':'Itaú'})
        self.assertEqual(movement_account_context(db,str(oid)), (oid,'itau'))
        self.assertEqual(movement_account_context(db,'profile:itau'), (oid,'itau'))
        db.accounts.docs.append({'_id':ObjectId(),'tipo':'conta_corrente','instituicao':'Itaú'})
        self.assertEqual(movement_account_context(db,str(oid)), (oid,None))
        self.assertEqual(movement_account_context(db,'profile:itau'), (None,'itau'))

    def test_B03_every_monthly_history_link_blocks_deletion(self):
        for history in ({'balances':{'account:inter':{'opening':'500','closing':'500'}}},
                        {'items':{'account:inter:statement':{'status':'done'}}},
                        {'custom':[{'group_id':'account:inter','title':'Pendente'}]},
                        {'closed_snapshot':{'groups':[{'id':'account:inter'}]}}):
            with self.subTest(history=history):
                db=ProfileDB(); db.account_profiles.docs.append({'_id':'month-close:2026-09','entity_type':'month_close', **history})
                self.assertEqual(account_usage(db,'inter')['fechamentos_mensais'],1)
                before=copy.deepcopy(db.account_profiles.docs)
                with self.assertRaises(HTTPException) as error:
                    delete_profile(db,'inter',AccountProfileDelete(expected_revision=1))
                self.assertEqual(error.exception.status_code,409)
                self.assertEqual(before,db.account_profiles.docs)

    def test_B04_monthly_schedule_changes_invalidate_only_relevant_period(self):
        fixture=self.fixture(month_fixture.MonthCloseTests); db=fixture.db
        before=view(db,'2026-09')
        db.commitments.docs.append({'_id':ObjectId(),'status':'ativo','name':'Assinatura','amount':'777.00',
            'frequency':'mensal','next_due_date':'2026-09-20','purchase_date':'2026-09-01','renewal_status':'automatica'})
        after=view(db,'2026-09')
        self.assertNotEqual(before['source_token'],after['source_token'])
        state={'revision':1,'status':'closed','closed_source_token':before['source_token'],
               'items':{'general:commitments':{'status':'done','source_token':next(i['source_token'] for i in before['groups'][0]['items'] if i['id']=='general:commitments')}},
               'closed_snapshot':{'totals':before['totals']}}
        changed=view(db,'2026-09',state)
        self.assertTrue(changed['source_changed'])
        self.assertTrue(next(i for i in changed['groups'][0]['items'] if i['id']=='general:commitments')['needs_review'])
        self.assertEqual(changed['last_close'],state['closed_snapshot'])
        db.commitments.docs[-1]['next_due_date']='2026-12-20'; db.commitments.docs[-1]['purchase_date']='2026-12-01'
        self.assertEqual(before['source_token'],view(db,'2026-09')['source_token'])

    def test_B04_installment_effective_source_included(self):
        fixture=self.fixture(month_fixture.MonthCloseTests); db=fixture.db
        before=view(db,'2026-09')['source_token']
        plan={'_id':ObjectId(),'status':'ativo','installments':[{'numero':1,'competencia':'2026-09','valor':Decimal128('10'),'status':'projetada'}]}
        db.installment_plans.docs.append(plan)
        after=view(db,'2026-09')['source_token']; self.assertNotEqual(before,after)
        part=plan['installments'][0]
        db.future_schedule_overrides.docs.append({'_id':'installment:'+str(plan['_id']),
            'rows':{'1':{'original_token':source_digest(part),'amount':'20','due_date':'2026-09-20','cancelled':False}}})
        self.assertNotEqual(after,view(db,'2026-09')['source_token'])

    def test_B05_account_activity_overrides_future_opening_date(self):
        fixture=self.fixture(month_fixture.MonthCloseTests); db=fixture.db
        db.account_profiles.docs.append({'_id':'account-profile:itau','key':'itau','revision':1,
            'profile':{'instituicao':'Itaú','tipo':'conta_corrente','data_abertura':'2026-10-05'}})
        result=view(db,'2026-09')
        self.assertIn('account:itau',[g['id'] for g in result['groups']])
        self.assertTrue(result['groups'][0]['items'][0]['blockers'])

    def test_B06_civil_deadlines_keep_day_while_events_follow_brasilia(self):
        for value in (datetime(2026,10,1,tzinfo=timezone.utc), day_start(date(2026,10,1)), '2026-10-01'):
            self.assertEqual(civil_date(value),date(2026,10,1))
        self.assertEqual(local_date(datetime(2026,10,1,tzinfo=timezone.utc)),date(2026,9,30))
        fixture=self.fixture(month_fixture.MonthCloseTests); fixture.db.credit_card_bills.docs[0].update(competencia='2026-08',data_vencimento=datetime(2026,10,1,tzinfo=timezone.utc))
        self.assertIn('1 fatura',next(g for g in view(fixture.db,'2026-10')['groups'] if g['kind']=='card')['detail'])

    def test_B07_notes_preserve_financial_documents_and_divergence_rejects(self):
        fixture=self.fixture(card_fixture.CardTests); db=fixture.db
        body=lambda: BillEdit(expected_token=fixture.bill_item()['token'],competencia='2026-10',data_vencimento='2026-10-15',valor_total='100.00',observacoes='Anotação')
        before=copy.deepcopy([db.credit_card_bills.docs, db.obligations.docs])
        edit_bill(str(fixture.bill),body(),db)
        self.assertEqual(before,[db.credit_card_bills.docs,db.obligations.docs])
        db.obligations.docs[0]['saldo_aberto']=Decimal128('1.00')
        before=copy.deepcopy([db.credit_card_bills.docs,db.obligations.docs])
        with self.assertRaises(HTTPException) as error: edit_bill(str(fixture.bill),body(),db)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(before,[db.credit_card_bills.docs,db.obligations.docs])

    def test_B08_old_generation_cannot_rename_or_delete_recreated_name(self):
        fixture=self.fixture(subcategory_fixture.SubcategoryTests); db=fixture.db
        fixture.create('Cursos antigos')
        old=next(i for i in list_subcategories('Habitação',db)['details'] if i['name']=='Cursos antigos')
        fixture.rename('Cursos antigos','Cursos atuais'); fixture.create('Cursos antigos')
        for deleting in (False,True):
            with self.subTest(deleting=deleting), self.assertRaises(HTTPException) as error:
                if deleting: delete_subcategory(category='Habitação',name=old['name'],expected_token=old['token'],db=db)
                else: rename_subcategory(SubcategoryRename(category='Habitação',name=old['name'],new_name='Errado',expected_token=old['token']),db)
            self.assertEqual(error.exception.status_code,409)
        self.assertIn('Cursos antigos',[i['name'] for i in list_subcategories('Habitação',db)['details']])

    def test_B08_new_usage_invalidates_review_and_missing_token_is_rejected(self):
        fixture=self.fixture(subcategory_fixture.SubcategoryTests); db=fixture.db; fixture.create('Cursos')
        token=next(i['token'] for i in list_subcategories('Habitação',db)['details'] if i['name']=='Cursos')
        db.transactions.docs.append({'_id':ObjectId(),'categoria':'Habitação','subcategoria':'Cursos'})
        with self.assertRaises(HTTPException) as error:
            rename_subcategory(SubcategoryRename(category='Habitação',name='Cursos',new_name='Novo',expected_token=token),db)
        self.assertEqual(error.exception.status_code,409)
        with self.assertRaises(HTTPException) as error:
            rename_subcategory(SubcategoryRename(category='Habitação',name='Cursos',new_name='Novo'),db)
        self.assertEqual(error.exception.status_code,428)

    def test_B09_card_capture_in_activity_and_sources_without_double_counting(self):
        fixture=self.fixture(month_fixture.MonthCloseTests); db=fixture.db
        before=view(db,'2026-09'); token=next(g for g in before['groups'] if g['kind']=='card')['items'][0]['source_token']
        db.provisional_transactions.docs.append({'_id':ObjectId(),'status':'confirmed','account_id':fixture.card,
            'data_evento':day_start(date(2026,9,20)),'valor':Decimal128('-50'),'natureza':'COMPRA',
            'categoria_sugerida':'Teste','linked_transaction_id':str(fixture.purchase)})
        result=view(db,'2026-09'); card=next(g for g in result['groups'] if g['kind']=='card')
        self.assertNotEqual(token,card['items'][0]['source_token'])
        self.assertFalse(card['items'][0]['can_not_apply'])
        self.assertEqual(before['totals'],result['totals'])


if __name__ == '__main__': unittest.main()
