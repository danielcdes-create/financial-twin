import copy
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from bson import ObjectId
from bson.decimal128 import Decimal128
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitment_estimates import effective_commitments
from app.commitments import schedule_from
from app.commitments import CommitmentIn, stored_document
from decimal import Decimal
from app.main import commitment_output


class Collection:
    def __init__(self): self.docs = []
    def find(self, query): return copy.deepcopy(self.docs)
    def find_one(self, query):
        return next((copy.deepcopy(row) for row in self.docs if all(row.get(k)==v for k,v in query.items())),None)


class DB(dict):
    def __getattr__(self, key): return self[key]
    def __missing__(self, key):
        self[key] = Collection()
        return self[key]


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.db = DB(); self.account = ObjectId()
        self.db['accounts'].docs.append({'_id':self.account,'tipo':'conta_corrente','instituicao':'Itaú'})
        self.item = {'commitment_id':'cemig-test', 'kind':'assinatura', 'name':'Cemig',
            'amount':'999.00', 'account_id':str(self.account), 'frequency':'mensal', 'status':'ativo',
            'renewal_status':'automatica', 'purchase_date':'2026-09-01', 'next_due_date':'2026-11-01'}
        self.db['commitment_amount_rules'].docs.append({'_id':'cemig-test', 'provider':'cemig',
            'method':'last_three_bills', 'account_id':str(self.account)})
        self.fixed_today = patch('app.commitment_estimates.today', return_value=date(2026,10,8))
        self.fixed_today.start(); self.addCleanup(self.fixed_today.stop)

    def bill(self, day, value, captured=False, **extra):
        row = {'_id':ObjectId(), 'account_id':self.account, 'data_evento':datetime.fromisoformat(day).replace(hour=15,tzinfo=timezone.utc),
            'valor':Decimal128('-'+value), 'fluxo':'saida', 'moeda':'BRL',
            'classification':{'migration_reason':'despesa_real'}, 'estabelecimento_original':'DA CEMIG'}
        if captured: row.update(status='confirmed', provisional_id=str(ObjectId()), merchant_raw='CEMIG')
        row.update(extra)
        self.db['provisional_transactions' if captured else 'transactions'].docs.append(row)
        return row

    def current(self, **kw): return effective_commitments(self.db,[self.item],**kw)[0]

    def test_average_updates_on_new_confirmation_and_never_changes_history(self):
        self.bill('2026-07-01','157.00'); self.bill('2026-08-03','148.40'); self.bill('2026-09-01','158.53')
        pending=self.bill('2026-10-01','172.68',True,status='provisional')
        self.assertEqual(self.current()['amount'],'154.64')
        pending['status']='confirmed'
        baseline={key:copy.deepcopy(value.docs) for key,value in self.db.items()}
        current=self.current()
        self.assertEqual(current['amount'],'159.87')
        self.assertEqual(commitment_output(current)['schedule'][0]['amount'],159.87)
        self.assertEqual(schedule_from(current,date(2026,11,1),2)[1]['amount'],159.87)
        self.assertEqual({key:value.docs for key,value in self.db.items()},baseline)
        self.assertEqual(self.item['amount'],'999.00')

    def test_account_currency_refund_transfer_and_linked_capture_excluded(self):
        old=self.bill('2026-07-01','157.00'); self.bill('2026-08-03','148.40'); self.bill('2026-09-01','158.53')
        self.bill('2026-10-01','800.00',account_id=ObjectId())
        self.bill('2026-10-02','700.00',moeda='USD')
        self.bill('2026-10-03','600.00',natureza='ESTORNO')
        self.bill('2026-10-04','500.00',natureza='TRANSFERENCIA')
        self.bill('2026-10-05','157.00',True,linked_transaction_id=str(old['_id']))
        self.bill('2026-11-01','900.00',True)
        self.assertEqual(self.current()['amount'],'154.64')
        self.assertEqual(self.current()['amount_estimate']['sample_count'],3)

    def test_last_three_bills_are_not_zero_filled_calendar_months(self):
        self.db['commitment_amount_rules'].docs[0]['provider']='supergasbras'
        for day,value in [('2026-03-26','14.85'),('2026-06-23','14.84'),('2026-09-15','32.05')]:
            self.bill(day,value,estabelecimento_original='PIX QRS SUPERGASBRA')
        self.assertEqual(self.current()['amount'],'20.58')

    def test_insufficient_history_keeps_reference_and_discloses_gap(self):
        self.bill('2026-09-01','158.53')
        result=self.current()
        self.assertEqual(result['amount'],'999.00')
        self.assertTrue(result['amount_estimate']['requires_review'])
        self.assertEqual(result['amount_estimate']['sample_count'],1)

    def test_old_month_does_not_use_later_bills_and_changed_account_needs_review(self):
        for day,value in [('2026-07-01','157.00'),('2026-08-03','148.40'),('2026-09-01','158.53'),('2026-10-01','172.68')]:self.bill(day,value)
        self.assertEqual(self.current(as_of=date(2026,9,30))['amount'],'154.64')
        self.item['account_id']=str(ObjectId())
        result=self.current()
        self.assertEqual(result['amount'],'999.00')
        self.assertTrue(result['amount_estimate']['requires_review'])

    def test_fixed_override_is_editable_and_automatic_mode_can_be_restored(self):
        for day,value in [('2026-07-01','157.00'),('2026-08-03','148.40'),('2026-09-01','158.53')]:self.bill(day,value)
        self.item.update(amount_mode='fixed',amount='175.20')
        current=self.current()
        self.assertEqual(current['amount'],'175.20')
        self.assertNotIn('amount_estimate',current)
        self.assertTrue(current['amount_rule_available'])
        self.item['amount_mode']='last_three_bills'
        self.assertEqual(self.current()['amount'],'154.64')
        self.item['status']='cancelado'
        self.assertEqual(schedule_from(self.current(),date(2026,11,1),12),[])

    def test_old_client_edit_preserves_mode_and_old_creation_payload_shape(self):
        body=CommitmentIn(kind='assinatura',name='Cemig',purchase_date=date(2026,9,1),
            next_due_date=date(2026,11,1),amount=Decimal('159.87'),payment_method='debito',account_id=str(self.account))
        self.assertNotIn('amount_mode',body.model_dump(mode='json'))
        old=stored_document(body);old['amount_mode']='last_three_bills'
        edited=stored_document(body,old)
        self.assertEqual(edited['amount_mode'],'last_three_bills')

    def test_profile_capture_counts_but_divergent_account_identity_does_not(self):
        for day,value in [('2026-07-01','157.00'),('2026-08-03','148.40'),('2026-09-01','158.53')]:self.bill(day,value)
        self.bill('2026-10-01','1000.00',True,account_id=None,account_profile_id='itau')
        self.bill('2026-10-02','999.00',True,account_id=ObjectId(),account_profile_id='itau')
        self.assertEqual(self.current()['amount'],'435.64')

    def test_missing_rule_remains_visible_as_review_required(self):
        self.item['amount_mode']='last_three_bills'
        self.db['commitment_amount_rules'].docs.clear()
        result=self.current()
        self.assertTrue(result['amount_estimate']['requires_review'])
        self.assertFalse(result['amount_estimate']['rule_valid'])
        self.assertEqual(result['amount'],'999.00')

    def test_profile_only_capture_does_not_cross_ambiguous_bank_accounts(self):
        for day,value in [('2026-07-01','157.00'),('2026-08-03','148.40'),('2026-09-01','158.53')]:self.bill(day,value)
        self.db['accounts'].docs.append({'_id':ObjectId(),'tipo':'conta_corrente','instituicao':'Itaú'})
        self.bill('2026-10-01','999.00',True,account_id=None,account_profile_id='itau')
        self.assertEqual(self.current()['amount'],'154.64')

    def test_null_mode_cannot_silently_disable_automatic_estimates(self):
        body=CommitmentIn(kind='assinatura',name='Cemig',purchase_date=date(2026,9,1),
            next_due_date=date(2026,11,1),amount=Decimal('159.87'),payment_method='debito',
            account_id=str(self.account),amount_mode=None)
        old=stored_document(body);old['amount_mode']='last_three_bills'
        self.assertEqual(stored_document(body,old)['amount_mode'],'last_three_bills')

    def test_legacy_edit_cannot_move_automatic_rule_to_another_account(self):
        from app.main import update_commitment
        from fastapi import HTTPException
        self.item.update(revision=1,amount_mode='last_three_bills')
        self.db['commitments'].docs.append(self.item)
        body=CommitmentIn(kind='assinatura',name='Cemig',purchase_date=date(2026,9,1),
            next_due_date=date(2026,11,1),amount=Decimal('159.87'),payment_method='debito',
            account_id=str(ObjectId()),revision=1)
        with patch('app.main.validate_commitment_refs'), self.assertRaises(HTTPException) as failure:
            update_commitment('cemig-test',body,self.db)
        self.assertEqual(failure.exception.status_code,422)

    def test_editing_notes_keeps_reference_separate_from_calculated_amount(self):
        from app.main import update_commitment
        from unittest.mock import Mock
        from types import SimpleNamespace
        body=CommitmentIn(kind='assinatura',name='Cemig',purchase_date=date(2026,9,1),
            next_due_date=date(2026,11,1),amount=Decimal('159.87'),payment_method='debito',
            account_id=str(self.account),revision=1,amount_mode='last_three_bills',notes='New note')
        original=stored_document(body);original.update(_id=ObjectId(),commitment_id='cemig-test',amount='999.00')
        self.db['commitments'].docs.append(original)
        collection=self.db['commitments']
        collection.replace_one=Mock(return_value=SimpleNamespace(matched_count=1))
        self.db['audit_log'].insert_one=Mock()
        with patch('app.main.validate_commitment_refs'):
            update_commitment('cemig-test',body,self.db)
        written=collection.replace_one.call_args.args[1]
        self.assertEqual(written['amount'],'999.00')
        self.assertEqual(written['notes'],'New note')

    def test_candidates_include_profile_capture_and_exclude_currency_and_identity_conflicts(self):
        from app.main import commitment_payment_candidates
        from test_capture_batches import BatchDB
        db=BatchDB()
        db.accounts.docs=copy.deepcopy(self.db.accounts.docs)
        capture=self.bill('2026-10-28','159.00',True,account_id=None,account_profile_id='itau')
        foreign=self.bill('2026-10-02','150.00',True,moeda='USD')
        divergent=self.bill('2026-10-03','155.00',True,account_id=ObjectId(),account_profile_id='itau')
        db.provisional_transactions.docs=[capture,foreign,divergent]
        db.commitments.docs=[self.item]
        effective={**self.item,'amount_estimate':{'rule_valid':True,'provider':'cemig'}}
        with patch('app.main.effective_commitments',return_value=[effective]):
            result=commitment_payment_candidates('cemig-test',db)
        self.assertEqual([x['provisional_id'] for x in result['items']],[capture['provisional_id']])
        effective.update(name='Supergasbras',amount_estimate={'rule_valid':True,'provider':'supergasbras'})
        capture.update(estabelecimento_original='PIX QRS SUPERGASBRA',merchant_raw='SUPERGASBRA')
        with patch('app.main.effective_commitments',return_value=[effective]):
            result=commitment_payment_candidates('cemig-test',db)
        self.assertEqual([x['provisional_id'] for x in result['items']],[capture['provisional_id']])

    def test_invalid_rule_candidates_return_conflict_instead_of_server_error(self):
        from app.main import commitment_payment_candidates
        from fastapi import HTTPException
        self.item.update(account_id='profile:itau',amount_mode='last_three_bills')
        from test_capture_batches import BatchDB
        db=BatchDB()
        db.commitments.docs=[self.item]
        db.commitment_amount_rules.docs=copy.deepcopy(self.db['commitment_amount_rules'].docs)
        with self.assertRaises(HTTPException) as failure:
            commitment_payment_candidates('cemig-test',db)
        self.assertEqual(failure.exception.status_code,409)


if __name__=='__main__': unittest.main()
