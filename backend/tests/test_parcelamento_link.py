import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitments import CommitmentIn, as_date
from app.economic_policy import effects, eligible_payment
from app.main import create_commitment
from app.parcelamento_link import (
    align_purchase_to_first_installment,
    backfill_recent_parcelamento_purchases,
    ensure_parcelamento_purchase,
    purchase_fingerprint,
    refresh_parcelamento_purchase,
    repair_unparsed_installments,
)
from app.transaction_service import TransactionService, enrich_notification_event
from test_ingestion import FakeDB


def plan(**changes):
    item = {
        'commitment_id': 'plan-1',
        'kind': 'parcelamento',
        'status': 'ativo',
        'name': 'LOJA EXEMPLO',
        'amount': '25.00',
        'installment_count': 4,
        'next_installment_number': 1,
        'purchase_date': datetime(2026, 10, 12, tzinfo=timezone.utc),
        'next_due_date': datetime(2026, 11, 12, tzinfo=timezone.utc),
        'payment_method': 'pix',
        'frequency': 'mensal',
        'linked_transactions': [],
    }
    item.update(changes)
    return item


def notification(event_id='notice-1', text=None, captured=None):
    captured = captured or datetime(2026, 10, 12, 23, 50, tzinfo=timezone.utc)
    return {
        'event_id': event_id,
        'source_type': 'android_notification',
        'source_provider': 'com.example.bank',
        'captured_at': captured,
        'raw_payload': {'text': text or 'Olá! Compra parcelada aprovada de R$ 100,00 em 4x em LOJA EXEMPLO no dia 12/10 às 20:40 no cartão Exemplo.'},
        'parsed_payload': {},
    }


class ParcelamentoLinkTests(unittest.TestCase):
    def test_parser_reads_installment_purchase_and_keeps_cash_purchase(self):
        parsed = enrich_notification_event(notification())
        payload = parsed['parsed_payload']
        self.assertEqual(payload['merchant_raw'], 'LOJA EXEMPLO')
        self.assertEqual(payload['amount'], '100.00')
        self.assertEqual(payload['installment_count'], 4)
        self.assertEqual(payload['event_type'], 'card_purchase')
        self.assertEqual(payload['payment_instrument_hint'], 'Exemplo')
        self.assertEqual(parsed['parser_version'], 'android-notification-br-v3')
        cash = enrich_notification_event(notification(text='Compra aprovada de R$ 10,00 em PADARIA no dia 09/10 às 14:50'))
        self.assertEqual(cash['parsed_payload']['merchant_raw'], 'PADARIA')
        self.assertNotIn('installment_count', cash['parsed_payload'])
        self.assertEqual(cash['parser_version'], 'android-notification-br-v2')
        conflict = notification()
        conflict['parsed_payload'] = {'event_type': 'financial_notification'}
        self.assertEqual(enrich_notification_event(conflict)['parsed_payload'], {'event_type': 'financial_notification'})

    def test_commitment_purchase_is_one_confirmed_line_and_does_not_settle(self):
        db = FakeDB()
        item = plan()
        first = ensure_parcelamento_purchase(db, item)
        again = ensure_parcelamento_purchase(db, item)
        self.assertEqual(first, again)
        self.assertEqual(len(db.provisional_transactions.docs), 1)
        row = db.provisional_transactions.docs[0]
        self.assertEqual(row['valor'].to_decimal(), Decimal('-25.00'))
        self.assertEqual(row['status'], 'confirmed')
        self.assertTrue(row['parcelamento_purchase'])
        self.assertEqual(row['installment_count'], 4)
        self.assertEqual(row['installment_number'], 1)
        self.assertEqual(row['detalhe'], 'Parcela 1 de 4')
        self.assertEqual(item['linked_transactions'], [])
        self.assertFalse(eligible_payment(row, True))
        self.assertEqual(effects(row, True), {'role': 'expense', 'income': Decimal('0'), 'expenses': Decimal('25.00')})
        obligation = plan(kind='obrigacao', commitment_id='tax-1', installment_count=None, next_installment_number=None)
        self.assertIsNone(ensure_parcelamento_purchase(db, obligation))
        self.assertEqual(len(db.provisional_transactions.docs), 1)

    def test_create_commitment_materializes_the_purchase_inside_the_same_write(self):
        db = FakeDB()
        body = CommitmentIn(
            kind='parcelamento', name='LOJA EXEMPLO', purchase_date=date(2026, 10, 12),
            next_due_date=date(2026, 11, 12), amount=Decimal('25.00'), frequency='mensal',
            installment_count=4, next_installment_number=1, payment_method='pix',
            renewal_status='nao_aplicavel', creation_id=uuid4(),
        )
        result = create_commitment(body, db)
        self.assertTrue(result['purchase_provisional_id'])
        self.assertEqual(len(db.provisional_transactions.docs), 1)
        self.assertEqual(db.commitments.docs[0]['linked_transactions'], [])
        self.assertEqual(db.provisional_transactions.docs[0]['parcelamento_commitment_id'], result['commitment_id'])

    def test_capture_opens_the_plan_and_a_repeat_does_not_add_another_line(self):
        db = FakeDB()
        card = ObjectId()
        db.accounts.docs.append({'_id': card, 'tipo': 'cartao_credito', 'instituicao': 'Exemplo', 'apelido': 'Cartão Exemplo', 'status': 'ativo'})
        service = TransactionService(db)
        first = service.process(notification())
        self.assertEqual(first['status'], 'provisional')
        self.assertEqual(len(db.commitments.docs), 1)
        saved = db.commitments.docs[0]
        self.assertEqual(Decimal(saved['amount']), Decimal('25.00'))
        self.assertEqual(as_date(saved['purchase_date']).isoformat(), '2026-10-12')
        self.assertEqual(as_date(saved['next_due_date']).isoformat(), '2026-11-12')
        self.assertEqual(saved['purchase_provisional_id'], first['provisional_id'])
        self.assertEqual(db.provisional_transactions.docs[0]['valor'].to_decimal(), Decimal('-25.00'))
        self.assertEqual(saved['linked_transactions'], [])
        self.assertEqual(str(saved['card_id']), str(card))
        second = service.process(notification(event_id='notice-2', text='Compra parcelada aprovada de R$ 100,50 em 4x em LOJA EXEMPLO no dia 12/10 às 21:00 no cartão Exemplo.'))
        self.assertEqual(second['status'], 'matched')
        self.assertEqual(len(db.provisional_transactions.docs), 1)
        self.assertEqual(len(db.commitments.docs), 1)
        self.assertEqual(len(db.transaction_evidence.docs), 1)
        distant = service.process(notification(event_id='notice-3', text='Compra parcelada aprovada de R$ 180,00 em 4x em LOJA EXEMPLO no dia 12/10 às 21:10 no cartão Exemplo.'))
        self.assertEqual(distant['status'], 'provisional')
        self.assertEqual(len(db.provisional_transactions.docs), 2)
        self.assertEqual(len(db.commitments.docs), 1)
        self.assertNotEqual(db.provisional_transactions.docs[1]['provisional_id'], saved['purchase_provisional_id'])

    def test_missing_card_keeps_the_purchase_until_the_plan_adopts_it(self):
        db = FakeDB()
        service = TransactionService(db)
        result = service.process(notification())
        self.assertEqual(result['status'], 'provisional')
        self.assertEqual(db.commitments.docs, [])
        self.assertEqual(db.review_queue.docs[0]['reason'], 'parcelamento_needs_card')
        self.assertTrue(db.provisional_transactions.docs[0]['parcelamento_pending_commitment'])
        item = plan(payment_method='cartao', card_id=str(ObjectId()))
        ensure_parcelamento_purchase(db, item)
        self.assertEqual(len(db.provisional_transactions.docs), 1)
        self.assertEqual(db.provisional_transactions.docs[0]['status'], 'confirmed')
        self.assertFalse(db.provisional_transactions.docs[0]['parcelamento_pending_commitment'])
        self.assertEqual(item['purchase_provisional_id'], result['provisional_id'])

    def test_unparsed_notification_attaches_to_the_existing_plan(self):
        db = FakeDB()
        event = notification()
        event['processing_status'] = 'needs_parse'
        db.raw_events.docs.append(event)
        db.review_queue.docs.append({'review_id': 'review-1', 'event_id': event['event_id'], 'status': 'pending', 'reason': 'event_needs_parse'})
        db.commitments.docs.append(plan())
        other = plan(commitment_id='other', name='OUTRA LOJA')
        db.commitments.docs.append(other)
        counts = repair_unparsed_installments(db)
        self.assertEqual(counts, {'attached': 1, 'skipped': 0})
        self.assertEqual(len(db.provisional_transactions.docs), 1)
        self.assertEqual(len(db.commitments.docs), 2)
        self.assertIsNone(other.get('purchase_provisional_id'))
        self.assertEqual(db.raw_events.docs[0]['processing_status'], 'matched')
        self.assertEqual(db.review_queue.docs[0]['status'], 'resolved')
        self.assertEqual(db.commitments.docs[0]['linked_transactions'], [])
        db.commitments.docs.append(plan(commitment_id='twin', name='LOJA EXEMPLO'))
        pending = notification(event_id='notice-ambiguous')
        pending['processing_status'] = 'needs_parse'
        db.raw_events.docs.append(pending)
        db.review_queue.docs.append({'review_id': 'review-2', 'event_id': pending['event_id'], 'status': 'pending', 'reason': 'event_needs_parse'})
        again = repair_unparsed_installments(db)
        self.assertEqual(again['attached'], 0)
        self.assertEqual(pending['processing_status'], 'needs_parse')

    def test_backfill_skips_history_before_the_cutoff_and_is_idempotent(self):
        db = FakeDB()
        db.commitments.docs.append(plan())
        db.commitments.docs.append(plan(commitment_id='old', purchase_date=datetime(2026, 9, 1, tzinfo=timezone.utc)))
        db.commitments.docs.append(plan(commitment_id='done', status='concluido'))
        first = backfill_recent_parcelamento_purchases(db)
        second = backfill_recent_parcelamento_purchases(db)
        self.assertEqual(first['inserted'], 1)
        self.assertEqual(first['before_cutoff'], 1)
        self.assertEqual(second['inserted'], 0)
        self.assertEqual(len(db.provisional_transactions.docs), 1)

    def test_editing_the_plan_updates_only_an_untouched_purchase(self):
        db = FakeDB()
        item = plan()
        ensure_parcelamento_purchase(db, item)
        previous = dict(item)
        item['amount'] = '30.00'
        refresh_parcelamento_purchase(db, previous, item)
        self.assertEqual(db.provisional_transactions.docs[0]['valor'].to_decimal(), Decimal('-30.00'))
        edited = dict(item)
        edited['amount'] = '40.00'
        db.provisional_transactions.docs[0]['purchase_fingerprint'] = 'edited-on-the-statement'
        refresh_parcelamento_purchase(db, item, edited)
        self.assertEqual(db.provisional_transactions.docs[0]['valor'].to_decimal(), Decimal('-30.00'))
        self.assertNotEqual(purchase_fingerprint(edited), item['purchase_fingerprint'])

    def test_a_total_already_on_the_statement_becomes_the_first_installment(self):
        db = FakeDB()
        item = plan()
        item['purchase_provisional_id'] = 'old-total'
        item['purchase_fingerprint'] = 'total-fingerprint'
        db.commitments.docs.append(item)
        db.provisional_transactions.docs.append({
            'provisional_id': 'old-total', 'parcelamento_purchase': True, 'status': 'confirmed',
            'valor': Decimal128('-100.00'), 'detalhe': 'Compra parcelada em 4x', 'revision': 1,
        })
        first = align_purchase_to_first_installment(db)
        second = align_purchase_to_first_installment(db)
        self.assertEqual(first, {'corrected': 1, 'unchanged': 0, 'skipped': 0})
        self.assertEqual(second['corrected'], 0)
        self.assertEqual(second['unchanged'], 1)
        self.assertEqual(db.provisional_transactions.docs[0]['valor'].to_decimal(), Decimal('-25.00'))
        self.assertEqual(db.provisional_transactions.docs[0]['detalhe'], 'Parcela 1 de 4')
        self.assertEqual(item['linked_transactions'], [])
        db.provisional_transactions.docs[0]['valor'] = Decimal128('-80.00')
        self.assertEqual(align_purchase_to_first_installment(db)['skipped'], 1)
        self.assertEqual(db.provisional_transactions.docs[0]['valor'].to_decimal(), Decimal('-80.00'))

    def test_statement_detail_carries_the_plan_total_and_count(self):
        from app.parcelamento_link import decorate_purchase_links
        db = FakeDB()
        item = plan()
        item['purchase_provisional_id'] = 'p1'
        db.commitments.docs.append(item)
        row = {'provisional_id': 'p1', 'movement_source': 'capture', 'parcelamento_purchase': True, 'installment_count': 4}
        decorate_purchase_links(db, [row])
        self.assertEqual(row['parcelamento'], {'count': 4, 'number': 1, 'installment_amount': 25.0, 'total': 100.0})
        self.assertEqual(row['commitment']['purchase'], True)


if __name__ == '__main__':
    unittest.main()
