"""Independent review of status, mixed-source pages and the cursor contract."""
import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from bson import ObjectId
from fastapi import HTTPException
from app.main import ProvisionalUpdate, decode_movement_cursor, movements, update_provisional
import test_record_review as fixtures


class IndependentRecordReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecordReviewTests()
        self.fixture.setUp()

    def test_review_cursor_is_bound_to_each_filter(self):
        first = self.fixture.listing(limit=1)
        changes = [
            {'status': 'validado'}, {'search': 'Padaria'}, {'category': 'Alimentação'},
            {'account_id': str(self.fixture.account)}, {'date_from': date(2026, 9, 30)},
            {'date_to': date(2026, 10, 1)},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(HTTPException) as failure:
                self.fixture.listing(limit=1, cursor=first['next_cursor'], **change)
            self.assertEqual(failure.exception.status_code, 422)

    def test_same_filter_cursor_can_change_page_size(self):
        first = self.fixture.listing(limit=1)
        following = self.fixture.listing(limit=10, offset=25, cursor=first['next_cursor'])
        self.assertEqual(following['total'], 2)
        self.assertEqual(following['offset'], 0)
        self.assertEqual([row['id'] for row in following['items']], [str(self.fixture.ledger['_id'])])

    def test_ties_across_sources_paginate_once_in_descending_order(self):
        stamp = self.fixture.capture['data_evento']
        self.fixture.ledger['data_evento'] = stamp
        for index in range(1, 5):
            capture = copy.deepcopy(self.fixture.capture)
            capture.update({'_id': ObjectId(), 'provisional_id': 'tie-capture-' + str(index)})
            self.fixture.db.provisional_transactions.docs.append(capture)
            ledger = copy.deepcopy(self.fixture.ledger)
            ledger['_id'] = ObjectId()
            self.fixture.db.transactions.docs.append(ledger)
        identities = []
        cursor = None
        while True:
            page = self.fixture.listing(limit=1, cursor=cursor)
            self.assertEqual(page['total'], 10)
            identities.extend((row['movement_source'], row.get('provisional_id') or row['id']) for row in page['items'])
            if not page['has_more']:
                break
            cursor = page['next_cursor']
        self.assertEqual(len(identities), 10)
        self.assertEqual(len(set(identities)), 10)
        self.assertEqual([source for source, _ in identities], ['capture'] * 5 + ['ledger'] * 5)

    def test_original_movement_cursor_contract_is_preserved(self):
        page = movements(limit=1, offset=0, cursor_mode=True, cursor=None, search=None,
            category=None, account_id=None, date_from=None, date_to=None, db=self.fixture.db)
        stamp, source, _ = decode_movement_cursor(page['next_cursor'])
        self.assertEqual(source, 'capture')
        self.assertEqual(stamp, self.fixture.capture['data_evento'])

    def test_historical_source_proof_rejects_currency_flow_and_account_drift(self):
        self.fixture.old_capture()
        original = copy.deepcopy(self.fixture.capture)
        for field, value in [('moeda', 'USD'), ('fluxo', 'entrada'),
                             ('account_id', ObjectId()), ('account_profile_id', 'inter')]:
            with self.subTest(field=field):
                self.fixture.capture.clear()
                self.fixture.capture.update(copy.deepcopy(original))
                self.fixture.capture[field] = value
                self.assertEqual(self.fixture.listing('validado')['total'], 0)
                self.assertEqual(self.fixture.listing('pendente')['total'], 2)

    def test_historical_noop_returns_effective_validation_without_a_write(self):
        self.fixture.old_capture()
        original = copy.deepcopy(self.fixture.capture)
        audits_before = len(self.fixture.db.audit_log.docs)
        result = update_provisional(self.fixture.capture['provisional_id'],
            ProvisionalUpdate(expected_revision=1, tipo='compra'), self.fixture.db)
        self.assertFalse(result['changed'])
        self.assertEqual(result['item']['status_revisao'], 'validado')
        self.assertEqual(self.fixture.capture, original)
        self.assertEqual(len(self.fixture.db.audit_log.docs), audits_before)


if __name__ == '__main__':
    unittest.main()
