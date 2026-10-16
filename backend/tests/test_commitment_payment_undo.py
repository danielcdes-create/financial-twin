import copy
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId
from app.commitments import CommitmentIn, stored_document
from app.main import CommitmentPaymentSwap, CommitmentPaymentUnlink, swap_commitment_payment, unlink_commitment_payment
from app.source_tokens import source_token
from test_domain_ledger_fixes import DomainCollection, expense, payment, put


FIELDS = ("next_due_date", "next_installment_number", "status", "remaining_charges", "schedule_anchor_day", "schedule_anchor_end")


class UndoCollection(DomainCollection):
    def update_one(self, query, update, upsert=False):
        update = copy.deepcopy(update)
        positional = {}
        removed = []
        for key in list((update.get('$set') or {})):
            if '.$.' in key:
                positional[key.split('.', 2)[2]] = update['$set'].pop(key)
        for key in list((update.get('$unset') or {})):
            if '.$.' in key:
                removed.append(key.split('.', 2)[2])
                update['$unset'].pop(key)
        pull = update.pop('$pull', None)
        row = next((item for item in self.docs if self._matches(item, query)), None)
        result = super().update_one(query, update, upsert)
        if result.matched_count == 0 or row is None:
            return result
        if pull:
            for key, spec in pull.items():
                row[key] = [item for item in row.get(key, []) if not all(item.get(field) == expected for field, expected in spec.items())]
        if positional or removed:
            id_field = 'transaction_id' if 'linked_transactions.transaction_id' in query else 'provisional_id'
            identity = query.get('linked_transactions.' + id_field)
            element = next(item for item in row.get('linked_transactions', []) if item.get(id_field) == identity)
            element.update(positional)
            for field in removed:
                element.pop(field, None)
        return result if result is not None else SimpleNamespace(matched_count=1)


class UndoDB(dict):
    def __getattr__(self, key):
        return self[key]

    def __missing__(self, key):
        self[key] = UndoCollection()
        return self[key]


def agenda(item):
    return {field: copy.deepcopy(item.get(field)) for field in FIELDS}


def installment():
    db = UndoDB()
    body = CommitmentIn(kind='parcelamento', name='Compra sintética', purchase_date=date(2026, 1, 1),
        next_due_date=date(2026, 1, 31), amount=Decimal('29.90'), frequency='mensal',
        installment_count=3, next_installment_number=1, payment_method='pix')
    item = {'_id': ObjectId(), **stored_document(body)}
    put(db, 'commitments', item)
    return db, item


class PaymentUndoTests(unittest.TestCase):
    def test_immediate_undo_restores_the_agenda_and_keeps_the_statement_row(self):
        db, item = installment()
        before = agenda(item)
        tx = expense(date(2026, 2, 1), '-29.90', descricao_original='Lancamento sintetico')
        put(db, 'transactions', tx)
        original = copy.deepcopy({key: tx.get(key) for key in ('_id', 'valor', 'fluxo', 'descricao_original', 'classification')})
        payment(db, item, tx)
        linked = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertEqual(linked['next_installment_number'], 2)
        self.assertNotEqual(linked['next_due_date'], before['next_due_date'])
        self.assertIn('schedule_before', linked['linked_transactions'][0])
        request_id = uuid4()
        body = CommitmentPaymentUnlink(request_id=request_id, expected_revision=linked['revision'], transaction_id=str(tx['_id']))
        first = unlink_commitment_payment(item['commitment_id'], body, db)
        restored = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertTrue(first['agenda_restored'])
        self.assertIsNone(first['warning'])
        self.assertEqual(agenda(restored), before)
        self.assertEqual(restored['linked_transactions'], [])
        self.assertEqual(restored['revision'], linked['revision'] + 1)
        kept = {key: db.transactions.docs[0].get(key) for key in original}
        self.assertEqual(kept, original)
        replay = unlink_commitment_payment(item['commitment_id'], body, db)
        self.assertEqual(replay['request_id'], str(request_id))
        again = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertEqual(agenda(again), before)
        self.assertEqual(again['revision'], restored['revision'])
        self.assertEqual(len(db.audit_log.docs), 2)

    def test_later_link_keeps_the_agenda_and_warns(self):
        db, item = installment()
        first_tx, second_tx = expense(date(2026, 2, 1)), expense(date(2026, 2, 28))
        put(db, 'transactions', first_tx)
        put(db, 'transactions', second_tx)
        payment(db, item, first_tx)
        current = db.commitments.find_one({'commitment_id': item['commitment_id']})
        payment(db, current, second_tx)
        final = db.commitments.find_one({'commitment_id': item['commitment_id']})
        moved = agenda(final)
        result = unlink_commitment_payment(item['commitment_id'], CommitmentPaymentUnlink(
            request_id=uuid4(), expected_revision=final['revision'], transaction_id=str(first_tx['_id'])), db)
        self.assertFalse(result['agenda_restored'])
        self.assertIn('vínculo mais recente', result['warning'])
        kept = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertEqual(agenda(kept), moved)
        self.assertEqual([row['transaction_id'] for row in kept['linked_transactions']], [str(second_tx['_id'])])

    def test_swap_replaces_the_movement_inside_the_same_link(self):
        db, item = installment()
        current_tx, replacement = expense(date(2026, 2, 1), '-29.90'), expense(date(2026, 2, 3), '-31.00', descricao_original='Outro lancamento')
        put(db, 'transactions', current_tx)
        put(db, 'transactions', replacement)
        payment(db, item, current_tx)
        linked = db.commitments.find_one({'commitment_id': item['commitment_id']})
        before = agenda(linked)
        due = linked['linked_transactions'][0]['due_date']
        result = swap_commitment_payment(item['commitment_id'], CommitmentPaymentSwap(
            request_id=uuid4(), expected_revision=linked['revision'], expected_source_token=source_token(replacement),
            transaction_id=str(current_tx['_id']), replacement_transaction_id=str(replacement['_id'])), db)
        swapped = db.commitments.find_one({'commitment_id': item['commitment_id']})
        self.assertEqual(agenda(swapped), before)
        self.assertEqual(result['next_installment_number'], before['next_installment_number'])
        self.assertFalse(result['agenda_restored'])
        self.assertEqual(len(swapped['linked_transactions']), 1)
        link = swapped['linked_transactions'][0]
        self.assertEqual(link['transaction_id'], str(replacement['_id']))
        self.assertEqual(link['due_date'], due)
        self.assertEqual(link['settled_next'], True)
        self.assertEqual({row['_id'] for row in db.transactions.docs}, {current_tx['_id'], replacement['_id']})
        self.assertEqual(db.audit_log.docs[-1]['event'], 'commitment_payment_swapped')


if __name__ == '__main__':
    unittest.main()
