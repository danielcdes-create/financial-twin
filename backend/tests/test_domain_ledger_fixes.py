"""NF01/NF02/NF03/NF09 boundaries. Only deterministic in-memory fixtures."""
import copy
import re
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from app.account_positions import account_positions
from app.commitments import CommitmentIn, stored_document, schedule, future_pending_total
from app.financial_time import day_start
from app.main import (CommitmentPaymentLink, link_commitment_payment, commitment_output, _dashboard,
                      TransactionClassificationUpdate, update_transaction_classification)
from app.month_close import get_checklist, financial_context, Version, close_month
from app.insights import closure, planning_context, rows_for
from app.dashboard_window import snapshot_for
from app.source_tokens import source_token
from test_account_profiles import ProfileCollection, ProfileCursor
from test_ingestion import FakeDB
from test_movement_deletion import values
from test_architecture_failures import rollback_fake, FailsOnce
import test_month_close


class DomainCollection(ProfileCollection):
    def _matches(self, row, query):
        query = dict(query)
        for key, expected in list(query.items()):
            if isinstance(expected, dict) and '$ne' in expected:
                if any(value == expected['$ne'] for value in values(row, key.split('.'))): return False
                query[key] = {operator: value for operator, value in expected.items() if operator != '$ne'}
                if not query[key]: query.pop(key)
            if isinstance(expected, dict) and '$regex' in expected:
                actual = row.get(key)
                if not isinstance(actual, str) or not re.search(expected['$regex'], actual): return False
        return super()._matches(row, query)

    def find(self, query=None, projection=None):
        return ProfileCursor(copy.deepcopy(row) for row in self.docs if self._matches(row, query or {}))

    def find_one(self, query, projection=None, sort=None):
        rows = list(self.find(query, projection))
        for key, order in reversed(sort or []): rows.sort(key=lambda row: row.get(key), reverse=order < 0)
        return rows[0] if rows else None

    def update_one(self, query, update, upsert=False):
        row = next((row for row in self.docs if self._matches(row, query)), None)
        if row is None:
            if not upsert: return SimpleNamespace(matched_count=0)
            row = copy.deepcopy(query); self.docs.append(row)
        row.update(copy.deepcopy(update.get('$set', {})))
        for key in update.get('$unset', {}): row.pop(key, None)
        for key, value in update.get('$inc', {}).items(): row[key] = row.get(key, 0) + value
        for key, value in update.get('$push', {}).items(): row.setdefault(key, []).append(copy.deepcopy(value))
        return SimpleNamespace(matched_count=1, modified_count=1)


class DomainDB(FakeDB):
    def __missing__(self, key):
        self[key] = DomainCollection(); return self[key]


def put(db, collection, item):
    db[collection].docs.append(copy.deepcopy(item))


def expense(when, value='-29.90', **fields):
    return {'_id': ObjectId(), 'data_evento': day_start(when), 'valor': Decimal128(value),
            'tipo': 'compra', 'fluxo': 'saida', 'categoria': 'Sintética',
            'classification': {'migration_reason': 'despesa_real'}, **fields}


def payment(db, item, tx, **fields):
    return link_commitment_payment(item['commitment_id'], CommitmentPaymentLink(
        transaction_id=str(tx['_id']), expected_revision=item['revision'],
        expected_source_token=source_token(tx), **fields), db)


class LedgerDomainFixTests(unittest.TestCase):
    def transfer_fixture(self, *, capture_day=date(2026, 10, 1), source_day=date(2026, 9, 30),
                         target_day=date(2026, 9, 30), link_target=True):
        db = DomainDB(); origin, destination = ObjectId(), ObjectId()
        for identity, institution in ((origin, 'Itaú'), (destination, 'Santander')):
            put(db, 'accounts', {'_id': identity, 'instituicao': institution, 'tipo': 'conta_corrente'})
        source = expense(source_day, '-100.00', account_id=origin, natureza='TRANSFERENCIA')
        target = expense(target_day, '100.00', account_id=destination, natureza='TRANSFERENCIA')
        put(db, 'transactions', source)
        if link_target: put(db, 'transactions', target)
        transfer = {'from_account_key': 'itau', 'to_account_key': 'santander',
                    'source_transaction_id': str(source['_id']),
                    'reconciliation_status': 'conciliada' if link_target else 'parcial'}
        if link_target: transfer['destination_transaction_id'] = str(target['_id'])
        capture = {'_id': ObjectId(), 'status': 'confirmed', 'data_evento': day_start(capture_day),
                   'valor': Decimal128('-100.00'), 'natureza': 'TRANSFERENCIA', 'transfer': transfer}
        put(db, 'provisional_transactions', capture)
        for key, amount in (('itau', '900.00'), ('santander', '600.00')):
            put(db, 'account_balance_snapshots', {'_id': ObjectId(), 'account_key': key,
                'amount': Decimal128(amount), 'as_of': day_start(date(2026, 10, 1)),
                'reference_kind': 'civil_day', 'as_of_date': '2026-09-30'})
        return db, source, target, capture

    def positions(self, db): return {row['key']: row for row in account_positions(db)}
    def flows(self, db, month):
        return {row['id']: row['balance']['flow'] for row in get_checklist(month, db)['groups'] if row['kind'] == 'account'}

    def test_linked_legs_before_reference_do_not_repeat_after_late_capture(self):
        db, *_ = self.transfer_fixture(); before = copy.deepcopy(db.transactions.docs)
        rows = self.positions(db)
        self.assertEqual([rows[key]['balance'] for key in ('itau', 'santander')], [900, 600])
        self.assertEqual([rows[key]['movement_count'] for key in ('itau', 'santander')], [0, 0])
        self.assertEqual(self.flows(db, '2026-10')['account:itau'], '0.00')
        self.assertEqual(self.flows(db, '2026-10')['account:santander'], '0.00')
        self.assertEqual(db.transactions.docs, before)

    def test_different_cutoffs_and_inverse_capture_boundary_use_posted_dates(self):
        db, *_ = self.transfer_fixture(capture_day=date(2026, 9, 30), source_day=date(2026, 10, 1), target_day=date(2026, 10, 2))
        db.account_balance_snapshots.docs[1]['as_of'] = day_start(date(2026, 10, 3))
        rows = self.positions(db)
        self.assertEqual(rows['itau']['balance'], 800)
        self.assertEqual(rows['itau']['movement_count'], 1)
        self.assertEqual(rows['santander']['balance'], 600)
        self.assertEqual(rows['santander']['movement_count'], 0)
        self.assertEqual(self.flows(db, '2026-09')['account:itau'], '0.00')
        self.assertEqual(self.flows(db, '2026-10')['account:itau'], '-100.00')
        self.assertEqual(self.flows(db, '2026-10')['account:santander'], '100.00')

    def test_one_unlinked_leg_falls_back_once_and_remains_pending(self):
        db, *_ = self.transfer_fixture(link_target=False)
        rows = self.positions(db)
        self.assertEqual(rows['itau']['balance'], 900)
        self.assertEqual(rows['santander']['balance'], 700)
        self.assertEqual(rows['santander']['movement_count'], 1)
        self.assertEqual(rows['santander']['pending_transfer_count'], 1)
        self.assertEqual(self.flows(db, '2026-10')['account:itau'], '0.00')
        self.assertEqual(self.flows(db, '2026-10')['account:santander'], '100.00')

    def test_broken_or_divergent_link_is_explicit_pending_without_invented_fallback(self):
        for broken in (True, False):
            with self.subTest(broken=broken):
                db, _, target, _ = self.transfer_fixture()
                if broken: db.transactions.docs.pop()
                else: db.transactions.docs[-1]['valor'] = Decimal128('50.00')
                rows = self.positions(db)
                self.assertEqual(rows['santander']['balance'], 600)
                self.assertEqual(rows['santander']['pending_transfer_count'], 1)
                self.assertEqual(self.flows(db, '2026-10')['account:santander'], '0.00')
                general = next(group for group in get_checklist('2026-10', db)['groups'] if group['id'] == 'general')
                self.assertTrue(next(item for item in general['items'] if item['id'] == 'general:transfers')['blockers'])

    def installment(self, **fields):
        db = DomainDB()
        body = CommitmentIn(kind='parcelamento', name='Compra sintética', purchase_date=date(2026, 1, 1),
            next_due_date=date(2026, 1, 31), amount=Decimal('29.90'), frequency='mensal',
            installment_count=3, next_installment_number=1, payment_method='pix', **fields)
        item = {'_id': ObjectId(), **stored_document(body)}; put(db, 'commitments', item)
        return db, item

    def test_two_occurrences_paid_in_february_advance_and_duplicate_sources_block(self):
        db, item = self.installment()
        first, second = expense(date(2026, 2, 1)), expense(date(2026, 2, 28))
        for tx in (first, second): put(db, 'transactions', tx)
        payment(db, item, first)
        current = db.commitments.find_one({'commitment_id': item['commitment_id']})
        payment(db, current, second)
        final = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertEqual([row['due_date'] for row in final['linked_transactions']], ['2026-01-31', '2026-02-28'])
        self.assertEqual([row['period_key'] for row in final['linked_transactions']], ['2026-01', '2026-02'])
        self.assertEqual(commitment_output(final)['next_due_date'], '2026-03-31')
        self.assertEqual(final['next_installment_number'], 3)
        self.assertEqual(future_pending_total(final), Decimal('29.9'))
        with self.assertRaises(HTTPException) as error: payment(db, final, second)
        self.assertEqual(error.exception.status_code, 409)

    def test_duplicate_occurrence_and_unknown_legacy_link_require_review(self):
        for link in ({'due_date': '2026-01-31', 'paid_date': '2026-02-01'}, {'paid_date': '2026-02-01'}):
            with self.subTest(link=link):
                db, item = self.installment(); item['linked_transactions'] = [link]
                db.commitments.docs[0] = copy.deepcopy(item)
                tx = expense(date(2026, 2, 1)); put(db, 'transactions', tx)
                with self.assertRaises(HTTPException) as error: payment(db, item, tx)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(db.commitments.docs[0]['linked_transactions'], [link])

    def test_weekly_ambiguous_payment_requires_explicit_occurrence(self):
        db = DomainDB(); item = {'_id': ObjectId(), **stored_document(CommitmentIn(kind='assinatura', name='Semanal',
            purchase_date=date(2026, 9, 1), next_due_date=date(2026, 10, 1), amount=Decimal('29.90'),
            frequency='semanal', payment_method='pix', renewal_status='automatica'))}
        put(db, 'commitments', item); tx = expense(date(2026, 10, 4)); put(db, 'transactions', tx)
        with self.assertRaises(HTTPException) as error: payment(db, item, tx)
        self.assertEqual(error.exception.status_code, 422)
        result = payment(db, item, tx, occurrence_due_date=date(2026, 10, 1))
        self.assertEqual(result['payment']['occurrence_key'], 'due:2026-10-01')

    def test_historical_only_cannot_consume_current_or_future_occurrence(self):
        for due in (date(2026, 1, 31), date(2026, 2, 28)):
            with self.subTest(due=due):
                db, item = self.installment(); tx = expense(date(2026, 2, 1)); put(db, 'transactions', tx)
                before = copy.deepcopy(db.commitments.docs)
                with self.assertRaises(HTTPException) as error:
                    payment(db, item, tx, historical_only=True, occurrence_due_date=due)
                self.assertEqual(error.exception.status_code, 422)
                self.assertEqual(db.commitments.docs, before)
                self.assertEqual(db.transactions.docs[0], tx)

    def test_historical_explicit_prior_occurrence_preserves_the_current_agenda(self):
        db, item = self.installment()
        item['next_due_date'] = datetime(2026, 2, 28, tzinfo=timezone.utc)
        item['next_installment_number'] = 2; db.commitments.docs[0] = copy.deepcopy(item)
        tx = expense(date(2026, 2, 1)); put(db, 'transactions', tx)
        result = payment(db, item, tx, historical_only=True, occurrence_due_date=date(2026, 1, 31))
        self.assertFalse(result['payment']['settled_next'])
        final = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertEqual(commitment_output(final)['next_due_date'], '2026-02-28')
        self.assertEqual([row['due_date'] for row in schedule(final)], ['2026-02-28', '2026-03-31'])
        self.assertEqual(future_pending_total(final), Decimal('59.8'))

    def test_persisted_contradictory_history_is_preserved_as_pending_without_projection(self):
        db, item = self.installment()
        item['linked_transactions'] = [{'transaction_id': str(ObjectId()), 'occurrence_key': 'due:2026-01-31',
            'due_date': '2026-01-31', 'paid_date': '2026-02-01', 'settled_next': False}]
        db.commitments.docs[0] = copy.deepcopy(item); before = copy.deepcopy(db.commitments.docs)
        output = commitment_output(item)
        self.assertTrue(output['payment_occurrence_requires_review'])
        self.assertTrue(output['payment_occurrence_review_reasons'])
        self.assertEqual(output['schedule'], [])
        self.assertIsNone(output['future_pending_total'])
        context = planning_context(date(2026, 1, 1), date(2026, 3, 31), db)
        self.assertEqual(context['flows'], [])
        self.assertTrue(next(row['reason'] for row in context['excluded_sources'] if row['ref'].startswith('commitment:')))
        general = next(group for group in get_checklist('2026-02', db)['groups'] if group['id'] == 'general')
        self.assertTrue(next(row for row in general['items'] if row['id'] == 'general:commitments')['blockers'])
        tx = expense(date(2026, 2, 28)); put(db, 'transactions', tx)
        with self.assertRaises(HTTPException) as error: payment(db, item, tx)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(db.commitments.docs, before)

    def test_finite_subscription_last_early_payment_removes_all_projections(self):
        for renewal in ('automatica', 'manual', 'nao_renovar'):
            with self.subTest(renewal=renewal):
                db = DomainDB(); fields = {'remaining_charges': 1} if renewal == 'nao_renovar' else {}
                item = {'_id': ObjectId(), **stored_document(CommitmentIn(kind='assinatura', name='Última cobrança',
                    purchase_date=date(2026, 1, 1), next_due_date=date(2026, 10, 9), end_date=date(2026, 10, 9),
                    amount=Decimal('29.90'), frequency='mensal', payment_method='pix', renewal_status=renewal, **fields))}
                put(db, 'commitments', item); tx = expense(date(2026, 10, 4)); put(db, 'transactions', tx)
                payment(db, item, tx)
                final = db.commitments.find_one({'commitment_id': item['commitment_id']})
                self.assertEqual(final['status'], 'concluido'); self.assertEqual(schedule(final), [])
                self.assertEqual(future_pending_total(final), Decimal('0'))
                self.assertEqual(planning_context(date(2026, 10, 4), date(2026, 10, 31), db)['flows'], [])
                self.assertEqual(financial_context(db, '2026-10')['schedules'][0]['rows'], [])

    def test_legacy_settled_final_occurrence_is_not_projected_or_rewritten(self):
        item = stored_document(CommitmentIn(kind='assinatura', name='Legado', purchase_date=date(2026, 1, 1),
            next_due_date=date(2026, 10, 9), end_date=date(2026, 10, 9), amount=Decimal('29.90'),
            payment_method='pix', renewal_status='automatica'))
        item['linked_transactions'] = [{'due_date': '2026-10-09', 'paid_date': '2026-10-04', 'settled_next': True}]
        before = copy.deepcopy(item)
        self.assertEqual(schedule(item), []); self.assertEqual(future_pending_total(item), Decimal('0'))
        self.assertEqual(item, before)

    def test_bounded_manual_renewal_pauses_when_later_occurrence_still_fits(self):
        db = DomainDB(); item = {'_id': ObjectId(), **stored_document(CommitmentIn(kind='assinatura', name='Manual',
            purchase_date=date(2026, 1, 1), next_due_date=date(2026, 10, 9), end_date=date(2026, 11, 9),
            amount=Decimal('29.90'), payment_method='pix', renewal_status='manual'))}
        put(db, 'commitments', item); tx = expense(date(2026, 10, 4)); put(db, 'transactions', tx)
        payment(db, item, tx)
        final = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertEqual(final['status'], 'pausado')
        self.assertEqual(commitment_output(final)['next_due_date'], '2026-11-09')

    def test_competence_moves_economics_across_all_consumers_without_cash_change(self):
        db = DomainDB(); bank = ObjectId()
        put(db, 'accounts', {'_id': bank, 'tipo': 'conta_corrente', 'instituicao': 'Itaú'})
        put(db, 'account_balance_snapshots', {'account_key': 'itau', 'amount': Decimal128('500.00'),
                                            'as_of': day_start(date(2026, 8, 31)), 'reference_kind': 'civil_day'})
        tx = expense(date(2026, 8, 31), '-100.00', account_id=bank, competencia='2026-08')
        put(db, 'transactions', tx)
        update_transaction_classification(str(tx['_id']), TransactionClassificationUpdate(
            competencia='2026-09', expected_token=source_token(tx)), db)
        refund = expense(date(2026, 10, 2), '25.00', account_id=bank, competencia='2026-09', natureza='ESTORNO')
        put(db, 'transactions', refund)
        self.assertEqual(get_checklist('2026-08', db)['totals']['expenses'], '0.00')
        self.assertEqual(get_checklist('2026-09', db)['totals']['expenses'], '75.00')
        self.assertEqual(closure(date(2026, 9, 1), date(2026, 9, 30), db)['expenses'], '75.00')
        self.assertEqual(self.flows(db, '2026-08')['account:itau'], '-100.00')
        self.assertEqual(self.flows(db, '2026-09')['account:itau'], '0.00')
        self.assertEqual(self.positions(db)['itau']['balance'], 425)
        september = _dashboard('month', date(2026, 9, 1), date(2026, 9, 30), db)
        self.assertEqual(september['expenses'], 75)
        self.assertEqual(september['monthly_series'], [{'month': '2026-09', 'income': 0.0, 'expenses': 75.0, 'net': -75.0}])
        optimized = snapshot_for(db, [('month', date(2026, 9, 1), date(2026, 9, 30))])
        self.assertEqual(_dashboard('month', date(2026, 9, 1), date(2026, 9, 30), db, optimized)['expenses'], 75)
        self.assertEqual(db.transactions.docs[0]['data_evento'], tx['data_evento'])

    def test_capture_competence_and_fallback_brasilia_boundary(self):
        db = DomainDB()
        captured = {'_id': ObjectId(), 'provisional_id': 'capture', 'status': 'confirmed',
            'valor': Decimal128('-10.00'), 'data_evento': day_start(date(2026, 8, 31)), 'competencia': '2026-09'}
        put(db, 'provisional_transactions', captured)
        late_utc = expense(date(2026, 9, 30), '-20.00')
        late_utc['data_evento'] = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)
        put(db, 'transactions', late_utc)
        self.assertEqual(get_checklist('2026-09', db)['totals']['expenses'], '30.00')
        self.assertEqual(get_checklist('2026-08', db)['totals']['expenses'], '0.00')
        self.assertEqual(len(rows_for(db, date(2026, 9, 1), date(2026, 9, 30))), 2)

    def test_confirmed_capture_linked_to_the_statement_is_not_counted_twice(self):
        db = DomainDB()
        tx = expense(date(2026, 9, 15), '-40.00')
        put(db, 'transactions', tx)
        put(db, 'provisional_transactions', {'_id': ObjectId(), 'provisional_id': 'cap', 'status': 'confirmed',
            'valor': Decimal128('-40.00'), 'data_evento': day_start(date(2026, 9, 15)),
            'linked_transaction_id': str(tx['_id'])})
        self.assertEqual(len(rows_for(db, date(2026, 9, 1), date(2026, 9, 30))), 1)
        self.assertEqual(closure(date(2026, 9, 1), date(2026, 9, 30), db)['expenses'], '40.00')

    def test_capture_account_fallback_and_explicit_profile_precedence_in_cash_consumers(self):
        db = DomainDB(); bank, other = ObjectId(), ObjectId()
        put(db, 'accounts', {'_id': bank, 'tipo': 'conta_corrente', 'instituicao': 'Itaú'})
        put(db, 'accounts', {'_id': other, 'tipo': 'conta_corrente', 'instituicao': 'Santander'})
        for key in ('itau', 'santander'):
            put(db, 'account_balance_snapshots', {'account_key': key, 'amount': Decimal128('100.00'),
                'as_of': day_start(date(2026, 10, 1)), 'reference_kind': 'civil_day'})
        for fields in ({'account_id': bank}, {'account_id': bank, 'account_profile_id': 'santander'}):
            put(db, 'provisional_transactions', {'_id': ObjectId(), 'status': 'confirmed',
                'data_evento': day_start(date(2026, 10, 2)), 'valor': Decimal128('-10.00'), **fields})
        rows = self.positions(db)
        self.assertEqual(rows['itau']['balance'], 90)
        self.assertEqual(rows['santander']['balance'], 90)
        flows = self.flows(db, '2026-10')
        self.assertEqual(flows['account:itau'], '-10.00')
        self.assertEqual(flows['account:santander'], '-10.00')

    def test_competence_edit_preserves_closed_snapshot_and_requires_reconference(self):
        fixture = test_month_close.MonthCloseTests(); fixture.setUp(); fixture.complete()
        db = fixture.db; original = close_month('2026-09', Version(expected_revision=fixture.get()['revision']), db)
        tx = db.transactions.docs[-1]
        update_transaction_classification(str(tx['_id']), TransactionClassificationUpdate(
            competencia='2026-10', expected_token=source_token(tx)), db)
        current = get_checklist('2026-09', db)
        self.assertEqual(current['status'], 'closed'); self.assertTrue(current['source_changed'])
        self.assertEqual(current['last_close'], original['last_close'])
        self.assertEqual(current['last_close']['totals']['expenses'], '50.00')
        self.assertEqual(current['totals']['expenses'], '0.00')

    def test_payment_audit_failure_rolls_back_occurrence_and_agenda(self):
        db, item = self.installment(); tx = expense(date(2026, 2, 1)); put(db, 'transactions', tx)
        db.audit_log = FailsOnce(); before = copy.deepcopy(db.commitments.docs)
        with patch('app.main.mongo_transaction', lambda scoped: rollback_fake(scoped)):
            with self.assertRaises(RuntimeError): payment(db, item, tx, request_id=uuid4())
        self.assertEqual(db.commitments.docs, before)


if __name__ == '__main__': unittest.main()
