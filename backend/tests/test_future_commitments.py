import copy
import sys
import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from uuid import uuid4
from unittest.mock import patch
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.future_commitments import FutureWrite, FutureDelete, FutureUndo, ImportedPaymentLink, ImportedPaymentUnlink, view, write, undo_future, effective_document, effective_documents, future_obligations_view, link_imported_payment, unlink_imported_payment
from app.commitment_projection import monthly_projection
from app.source_tokens import source_token
from app.commitments import CommitmentIn, stored_document, schedule, schedule_from
from test_capture_batches import BatchDB
from test_architecture_failures import rollback_fake, FailsOnce

class FutureCommitmentTests(unittest.TestCase):
    def setUp(self):
        clock = patch('app.future_commitments.today', return_value=date(2026, 10, 1)); clock.start(); self.addCleanup(clock.stop)
        self.db = BatchDB(); self.id = str(ObjectId())
        self.plan = {'_id': ObjectId(self.id), 'descricao': 'Amazon sintética', 'status': 'ativo', 'numero_parcelas': 4,
            'installments': [{'numero': 1, 'competencia': '2026-08', 'valor': Decimal128('10.00'), 'status': 'paga', 'transaction_id': ObjectId()},
                {'numero': 2, 'competencia': '2026-09', 'valor': Decimal128('10.00'), 'status': 'projetada'},
                {'numero': 3, 'competencia': '2026-10', 'valor': Decimal128('10.00'), 'status': 'projetada'},
                {'numero': 4, 'competencia': '2026-11', 'valor': Decimal128('10.00'), 'status': 'projetada'}]}
        self.db.installment_plans.docs.append(copy.deepcopy(self.plan))
        for name in ['future_schedule_overrides', 'future_commitment_commands', 'audit_log', 'transactions', 'reconciliations']: self.db[name]
    def edit(self, **row):
        return FutureWrite(request_id=uuid4(), preview_token=view(self.db, 'installment', self.id)['preview_token'],
            rows=[{'row_id': 3, 'amount': '12.59', 'due_date': '2026-10-20', **row}])
    def removal(self, source='installment', identity=None):
        return FutureDelete(request_id=uuid4(), preview_token=view(self.db, source, identity or self.id)['preview_token'])

    def test_only_unpaid_current_or_future_month_without_guessing_day(self):
        rows = view(self.db, 'installment', self.id)['rows']
        self.assertEqual([row['editable'] for row in rows], [False, False, True, True])
        self.assertIsNone(rows[2]['due_date'])
    def test_exact_edit_changes_effective_view_not_original_or_ledger(self):
        write(self.db, 'installment', self.id, self.edit())
        actual = effective_document(self.db, 'installment', self.db.installment_plans.docs[0])
        self.assertEqual(actual['installments'][2]['valor'].to_decimal(), Decimal128('12.59').to_decimal())
        self.assertEqual(actual['installments'][2]['data_vencimento'], datetime(2026, 10, 20, tzinfo=timezone.utc))
        self.assertEqual(self.db.installment_plans.docs[0], self.plan)
        self.assertFalse(self.db.transactions.docs)
    def test_delete_all_preserves_paid_and_past_forecasts(self):
        write(self.db, 'installment', self.id, self.removal(), True)
        actual = effective_documents(self.db, 'installment', [self.plan])[0]
        self.assertEqual([part['status'] for part in actual['installments']], ['paga', 'projetada', 'cancelada', 'cancelada'])
        self.assertEqual(self.db.installment_plans.docs[0], self.plan)
    def test_one_future_can_be_excluded_without_excluding_the_rest(self):
        write(self.db, 'installment', self.id, self.edit(cancelled=True))
        actual = effective_document(self.db, 'installment', self.plan)
        self.assertEqual(actual['installments'][2]['status'], 'cancelada')
        self.assertEqual(actual['installments'][3]['status'], 'projetada')
    def test_paid_past_and_forged_rows_cannot_be_modified(self):
        for row_id in [1, 2, 99]:
            with self.assertRaises(HTTPException): write(self.db, 'installment', self.id, self.edit(row_id=row_id))
        self.assertFalse(self.db.future_schedule_overrides.docs)
    def test_faturada_or_linked_projection_is_protected(self):
        part = self.db.installment_plans.docs[0]['installments'][2]
        for field, value in [('status', 'faturada'), ('transaction_id', ObjectId()), ('bill_id', ObjectId())]:
            old = copy.deepcopy(part); part[field] = value
            self.assertFalse(view(self.db, 'installment', self.id)['rows'][2]['editable'])
            part.clear(); part.update(old)
    def test_new_source_or_new_correction_invalidates_previous_preview(self):
        body = self.edit(); self.db.installment_plans.docs[0]['installments'][2]['valor'] = Decimal128('11.00')
        with self.assertRaises(HTTPException) as failure: write(self.db, 'installment', self.id, body)
        self.assertEqual(failure.exception.status_code, 409)
        body = self.edit(); competing = self.edit(amount='13.99'); write(self.db, 'installment', self.id, competing)
        with self.assertRaises(HTTPException): write(self.db, 'installment', self.id, body)
    def test_lost_reply_same_command_is_idempotent_but_modified_command_rejected(self):
        body = self.edit(); write(self.db, 'installment', self.id, body)
        self.assertTrue(write(self.db, 'installment', self.id, body)['duplicate_request'])
        self.assertEqual(len(self.db.audit_log.docs), 1)
        changed = body.model_copy(update={'preview_token': 'b' * 64})
        with self.assertRaises(HTTPException): write(self.db, 'installment', self.id, changed)
    def test_audit_failure_rolls_back_correction_and_command(self):
        body = self.edit(); self.db['audit_log'] = FailsOnce()
        with patch('app.future_commitments.mongo_transaction', rollback_fake):
            with self.assertRaises(RuntimeError): write(self.db, 'installment', self.id, body)
        self.assertFalse(self.db.future_schedule_overrides.docs); self.assertFalse(self.db.future_commitment_commands.docs)
    def test_corrections_do_not_disappear_when_month_passes(self):
        write(self.db, 'installment', self.id, self.edit(cancelled=True))
        with patch('app.future_commitments.today', return_value=date(2026, 12, 1)):
            self.assertEqual(effective_document(self.db, 'installment', self.plan)['installments'][2]['status'], 'cancelada')
    def test_reimported_paid_source_overrides_old_forecast_correction(self):
        write(self.db, 'installment', self.id, self.edit(cancelled=True))
        new = copy.deepcopy(self.plan); new['installments'][2]['status'] = 'paga'
        actual = effective_document(self.db, 'installment', new)
        self.assertEqual(actual['installments'][2]['status'], 'paga'); self.assertTrue(actual['future_correction_needs_review'])
    def test_invalid_past_due_date_or_fractional_cents_are_rejected(self):
        with self.assertRaises(HTTPException): write(self.db, 'installment', self.id, self.edit(due_date='2026-09-30'))
        with self.assertRaises(ValidationError): self.edit(amount='12.591')
    def obligation(self, **changes):
        identity = str(ObjectId()); original = {'_id': ObjectId(identity), 'status': 'futura', 'tipo': 'financiamento',
            'data_vencimento': datetime(2026, 11, 11, tzinfo=timezone.utc), 'saldo_aberto': Decimal128('3700.25'), 'valor_pago': Decimal128('0'), **changes}
        self.db.obligations.docs.append(original); return identity, original
    def test_future_obligation_can_change_cash_projection_without_changing_paid_amount(self):
        identity, original = self.obligation(); untouched = copy.deepcopy(original)
        body = FutureWrite(request_id=uuid4(), preview_token=view(self.db, 'obligation', identity)['preview_token'], rows=[{'row_id': 1, 'amount': '3600.13', 'due_date': '2026-11-15'}])
        write(self.db, 'obligation', identity, body)
        actual = effective_documents(self.db, 'obligation', [original])[0]
        self.assertEqual(actual['saldo_aberto'].to_decimal(), Decimal128('3600.13').to_decimal())
        self.assertEqual(original, untouched); self.assertEqual(actual['valor_pago'], original['valor_pago'])
    def test_paid_or_reconciled_future_obligation_is_protected(self):
        identity, original = self.obligation(valor_pago=Decimal128('10'))
        self.assertFalse(view(self.db, 'obligation', identity)['rows'][0]['editable'])
        original['valor_pago'] = Decimal128('0')
        self.db.reconciliations.docs.append({'obligation_id': original['_id']})
        self.assertFalse(view(self.db, 'obligation', identity)['rows'][0]['editable'])
    def test_managed_future_deletion_keeps_paid_links_and_overdue_occurrences(self):
        item = stored_document(CommitmentIn(kind='assinatura', name='Serviço', purchase_date='2026-01-01', next_due_date='2026-09-25', amount='29.90', frequency='mensal', payment_method='pix', renewal_status='automatica'))
        item['_id'] = ObjectId(); item['linked_transactions'] = [{'transaction_id': 'paid-original', 'actual_amount': '27.90'}]
        self.db.commitments.docs.append(item)
        write(self.db, 'managed', item['commitment_id'], self.removal('managed', item['commitment_id']), True)
        actual = self.db.commitments.docs[0]
        self.assertEqual(actual['linked_transactions'], item['linked_transactions'])
        self.assertEqual([row['due_date'] for row in schedule(actual, 12)], ['2026-09-25'])
        self.assertFalse(self.db.transactions.docs)

    def test_corrected_calendar_controls_future_management_after_month_changes(self):
        identity, original = self.obligation()
        body = FutureWrite(request_id=uuid4(), preview_token=view(self.db, 'obligation', identity)['preview_token'], rows=[{'row_id':1,'amount':'3600.13','due_date':'2026-10-20'}])
        write(self.db, 'obligation', identity, body)
        with patch('app.future_commitments.today', return_value=date(2026,11,1)):
            listed = future_obligations_view(self.db)['items']
            self.assertEqual([item['due_date'] for item in listed], ['2026-10-20'])
            self.assertFalse(view(self.db,'obligation',identity)['rows'][0]['editable'])
        # Postponing is still a future forecast after the original date passes.
        with patch('app.future_commitments.today', return_value=date(2026,10,1)):
            body = FutureWrite(request_id=uuid4(), preview_token=view(self.db,'obligation',identity)['preview_token'],rows=[{'row_id':1,'amount':'3600.13','due_date':'2027-02-15'}])
            write(self.db,'obligation',identity,body)
        with patch('app.future_commitments.today', return_value=date(2026,12,1)):
            self.assertEqual(future_obligations_view(self.db)['items'][0]['due_date'],'2027-02-15')
            self.assertTrue(view(self.db,'obligation',identity)['rows'][0]['editable'])

    def test_old_weekly_subscription_can_be_deleted_without_history_consuming_limit(self):
        item=stored_document(CommitmentIn(kind='assinatura',name='Antiga',purchase_date='2010-01-01',next_due_date='2010-01-01',amount='9.90',frequency='semanal',payment_method='pix',renewal_status='automatica'))
        item['_id']=ObjectId(); self.db.commitments.docs.append(item)
        preview=view(self.db,'managed',item['commitment_id'])
        self.assertEqual(preview['editable_count'],600)
        self.assertEqual(schedule_from(item,date(2026,10,1),5)[0]['due_date'],'2026-10-02')
        write(self.db,'managed',item['commitment_id'],self.removal('managed',item['commitment_id']),True)
        self.assertFalse(schedule_from(self.db.commitments.docs[0],date(2026,10,1)))

    def test_seeking_does_not_invent_charges_after_manual_renewal_or_installment_end(self):
        item=stored_document(CommitmentIn(kind='assinatura',name='Manual',purchase_date='2020-01-01',next_due_date='2020-01-31',amount='10.01',frequency='mensal',payment_method='pix',renewal_status='manual'))
        self.assertFalse(schedule_from(item,date(2026,10,1)))
        item.update(kind='parcelamento',installment_count=12,next_installment_number=1,renewal_status='nao_aplicavel')
        self.assertFalse(schedule_from(item,date(2026,10,1)))

    def test_seeking_preserves_original_month_anchor_and_installment_number(self):
        item=stored_document(CommitmentIn(kind='parcelamento',name='Janeiro',purchase_date='2026-01-31',next_due_date='2026-01-31',amount='10.01',frequency='mensal',payment_method='pix',installment_count=12,next_installment_number=1))
        rows=schedule_from(item,date(2026,2,28),2)
        self.assertEqual([(row['due_date'],row['installment_number']) for row in rows],[('2026-02-28',2),('2026-03-31',3)])

    def test_series_key_groups_imported_occurrences_without_rewriting_them(self):
        def add(external_key=None, **changes):
            identity = str(ObjectId())
            document = {'_id': ObjectId(identity), 'status': 'futura', 'tipo': 'financiamento', 'origem_id': 'mortgage',
                'data_vencimento': datetime(2026, 11, 11, tzinfo=timezone.utc), 'saldo_aberto': Decimal128('10.00'),
                'valor_pago': Decimal128('0'), **changes}
            if external_key: document['external_key'] = external_key
            self.db.obligations.docs.append(document)
            return document
        for number in (1, 2, 3): add(f'obligation|financiamento-imobiliario|parcela-{number}')
        add('obligation|seguro|parcela-1', tipo='seguro', origem_id='policy')
        left = add(origem_id='mortgage-a', descricao='financiamento')
        right = add(origem_id='mortgage-b', descricao='financiamento')
        stored = copy.deepcopy(self.db.obligations.docs)
        grouped = {}
        for item in future_obligations_view(self.db)['items']:
            grouped.setdefault(item['series_key'], []).append(item['id'])
        self.assertEqual(len(grouped['obligation|financiamento-imobiliario']), 3)
        self.assertEqual(len(grouped['obligation|seguro']), 1)
        self.assertNotEqual(
            next(item['series_key'] for item in future_obligations_view(self.db)['items'] if item['id'] == str(left['_id'])),
            next(item['series_key'] for item in future_obligations_view(self.db)['items'] if item['id'] == str(right['_id'])))
        self.assertEqual(self.db.obligations.docs, stored)
        for document in self.db.obligations.docs: self.assertNotIn('series_key', document)

    def test_undo_restores_future_installments_and_keeps_the_paid_row(self):
        body = self.removal()
        write(self.db, 'installment', self.id, body, True)
        undo_future(self.db, 'installment', self.id, FutureUndo(request_id=uuid4(), delete_request_id=body.request_id))
        actual = effective_documents(self.db, 'installment', [self.plan])[0]
        self.assertEqual([part['status'] for part in actual['installments']], ['paga', 'projetada', 'projetada', 'projetada'])
        self.assertEqual(self.db.installment_plans.docs[0], self.plan)
        self.assertTrue(self.db.installment_plans.docs[0]['installments'][0]['transaction_id'])
        with self.assertRaises(HTTPException) as failure:
            undo_future(self.db, 'installment', self.id, FutureUndo(request_id=uuid4(), delete_request_id=body.request_id))
        self.assertEqual(failure.exception.status_code, 409)

    def test_undo_managed_cutoff_restores_schedule_and_keeps_links(self):
        item = stored_document(CommitmentIn(kind='assinatura', name='Serviço', purchase_date='2026-01-01', next_due_date='2026-10-25', amount='29.90', frequency='mensal', payment_method='pix', renewal_status='automatica'))
        item['_id'] = ObjectId()
        links = [{'transaction_id': 'paid-original', 'actual_amount': '27.90', 'due_date': '2026-09-25'}]
        item['linked_transactions'] = links
        self.db.commitments.docs.append(item)
        body = self.removal('managed', item['commitment_id'])
        write(self.db, 'managed', item['commitment_id'], body, True)
        self.assertFalse(schedule_from(self.db.commitments.docs[0], date(2026, 10, 1)))
        undo_future(self.db, 'managed', item['commitment_id'], FutureUndo(request_id=uuid4(), delete_request_id=body.request_id))
        restored = self.db.commitments.docs[0]
        self.assertEqual(restored['linked_transactions'], links)
        self.assertNotIn('future_cutoff', restored)
        self.assertTrue(schedule_from(restored, date(2026, 10, 1)))
        with self.assertRaises(HTTPException) as failure:
            undo_future(self.db, 'managed', item['commitment_id'], FutureUndo(request_id=uuid4(), delete_request_id=body.request_id))
        self.assertEqual(failure.exception.status_code, 409)

    def test_imported_payment_link_settles_the_charge_without_rewriting_the_plan(self):
        identity, original = self.obligation()
        untouched = copy.deepcopy(original)
        movement = {'_id': ObjectId(), 'data_evento': datetime(2026, 10, 13, 15, tzinfo=timezone.utc), 'valor': Decimal128('-3700.25'),
            'fluxo': 'saida', 'moeda': 'BRL', 'classification': {'migration_reason': 'despesa_real'}, 'descricao_original': 'Parcela sintetica'}
        self.db.transactions.docs.append(movement)
        body = ImportedPaymentLink(request_id=uuid4(), expected_source_token=source_token(movement), due_date=date(2026, 11, 11), transaction_id=str(movement['_id']))
        saved = link_imported_payment(self.db, 'obligation', identity, body)
        self.assertTrue(saved['persisted'])
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(self.db.obligations.docs[self.db.obligations.docs.index(original)], untouched)
        listed = future_obligations_view(self.db)['items']
        self.assertEqual(listed[0]['payment_link']['transaction_id'], str(movement['_id']))
        self.assertEqual(listed[0]['link_revision'], 1)
        self.assertFalse(any(entry['source_ref'] == 'obligation:' + identity for row in monthly_projection(self.db, 2, date(2026, 10, 1))['items'] for entry in row['items']))
        again = link_imported_payment(self.db, 'obligation', identity, body)
        self.assertTrue(again['duplicate_request'])
        self.assertEqual(len(self.db.future_schedule_overrides.docs[0]['payment_links']), 1)
        with self.assertRaises(HTTPException) as blocked:
            link_imported_payment(self.db, 'obligation', identity, ImportedPaymentLink(request_id=uuid4(), expected_source_token=source_token(movement), due_date=date(2026, 11, 11), transaction_id=str(movement['_id'])))
        self.assertEqual(blocked.exception.status_code, 409)
        unlink_imported_payment(self.db, 'obligation', identity, ImportedPaymentUnlink(request_id=uuid4(), expected_revision=1, transaction_id=str(movement['_id'])))
        self.assertFalse(future_obligations_view(self.db)['items'][0].get('payment_link'))
        self.assertTrue(any(entry['source_ref'] == 'obligation:' + identity for row in monthly_projection(self.db, 2, date(2026, 10, 1))['items'] for entry in row['items']))
        self.assertEqual(original, untouched)
