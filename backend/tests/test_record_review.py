import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.capture_batches import BatchCommit, BatchReview, commit, preview
from app.main import ProvisionalUpdate, review_records, update_provisional
from test_architecture_failures import FailsOnce, rollback_fake
from test_capture_batches import BatchCollection, BatchDB
from test_ingestion import Cursor


class RecordCursor(Cursor):
    def sort(self, order):
        rows = list(self)
        for key, direction in reversed(order):
            rows.sort(key=lambda row: row.get(key), reverse=direction < 0)
        return RecordCursor(rows)

    def limit(self, count): return RecordCursor(self[:count])


class RecordCollection(BatchCollection):
    def _matches(self, document, query):
        for key, wanted in query.items():
            if key == '$and':
                if not all(self._matches(document, part) for part in wanted): return False
            elif key == '$or':
                if not any(self._matches(document, part) for part in wanted): return False
            elif key == '$text':
                term = wanted['$search'].casefold()
                if not any(term in str(document.get(field) or '').casefold() for field in
                           ('merchant_raw', 'categoria_sugerida', 'estabelecimento_original', 'categoria', 'tipo')): return False
            elif isinstance(wanted, dict) and ('$exists' in wanted or '$nin' in wanted):
                if '$exists' in wanted and (key in document) != wanted['$exists']: return False
                if '$nin' in wanted and document.get(key) in wanted['$nin']: return False
            elif not super()._matches(document, {key: wanted}): return False
        return True

    def find(self, query, projection=None):
        return RecordCursor(copy.deepcopy(row) for row in self.docs if self._matches(row, query))


class RecordDB(BatchDB):
    def __missing__(self, name):
        self[name] = RecordCollection()
        return self[name]


class RecordReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = RecordDB()
        self.account = ObjectId()
        self.db.accounts.docs.append({'_id': self.account, 'tipo': 'conta_corrente', 'instituicao': 'Itaú'})
        self.batch_id = uuid4()
        self.line = {'row_id': 1, 'date': '2026-10-01', 'amount': '-12.59',
                     'description': 'Mixpão Padaria', 'kind': 'expense', 'bank_ref': None}
        self.db.capture_batches.docs.append({'_id': str(self.batch_id), 'batch_id': str(self.batch_id),
            'status': 'draft', 'sha256': 'a' * 64, 'storage_key': 'synthetic', 'complete': True, 'rows': [self.line]})
        body = BatchReview(account_ref='ledger:' + str(self.account), rows=[self.line])
        commit(self.batch_id, BatchCommit(**body.model_dump(),
            preview_token=preview(self.batch_id, body, self.db)['preview_token']), self.db)
        # Test collections need real ObjectIds for the stable cursor contract.
        self.capture = self.db.provisional_transactions.docs[0]
        self.capture['_id'] = ObjectId()
        self.ledger = {'_id': ObjectId(), 'data_evento': datetime(2026, 9, 30, 15, tzinfo=timezone.utc),
            'valor': Decimal128('-23.45'), 'estabelecimento_original': 'Mercado', 'account_id': self.account}
        self.db.transactions.docs.append(self.ledger)

    def listing(self, status='todos', **changes):
        args = {'limit': 50, 'offset': 0, 'cursor': None, 'search': None, 'category': None,
                'account_id': None, 'review_status': status, 'date_from': None, 'date_to': None, 'db': self.db}
        return review_records(**{**args, **changes})

    def old_capture(self):
        self.capture.pop('status_revisao', None)
        self.capture.pop('revisado_em', None)

    def test_confirmed_statement_and_legacy_share_review_filters(self):
        pending = self.listing('pendente')
        validated = self.listing('validado')
        self.assertEqual(pending['total'], 1)
        self.assertEqual(validated['total'], 1)
        self.assertEqual(validated['items'][0]['movement_source'], 'capture')
        self.assertEqual(validated['items'][0]['status_revisao'], 'validado')
        self.assertEqual(self.listing()['total'], 2)
        self.assertEqual(len(self.db.transactions.docs), 1)

    def test_old_audited_statement_is_validated_without_any_write(self):
        self.old_capture()
        before = {name: copy.deepcopy(collection.docs) for name, collection in self.db.items()}
        row = self.listing('validado')['items'][0]
        self.assertEqual(row['status_revisao'], 'validado')
        self.assertEqual(row['review_origin'], 'statement_batch_review')
        self.assertEqual(self.listing('pendente')['total'], 1)
        for name, documents in before.items(): self.assertEqual(self.db[name].docs, documents)

    def test_historical_review_requires_source_commit_audit_and_unedited_row(self):
        changes = [
            ('capture', {'revision': 2}),
            ('capture', {'merchant_raw': 'Outro vendedor'}),
            ('capture', {'valor': Decimal128('-15.00')}),
            ('capture', {'updated_at': self.capture['confirmed_at'] + timedelta(seconds=1)}),
            ('batch', {'status': 'draft'}),
            ('batch', {'result': {'inserted': []}}),
            ('audit', {'inserted': []}),
            ('audit', {'created_at': self.capture['confirmed_at'] + timedelta(seconds=1)}),
        ]
        self.old_capture()
        originals = [copy.deepcopy(self.capture), copy.deepcopy(self.db.capture_batches.docs[0]),
                     copy.deepcopy(self.db.audit_log.docs[0])]
        for name, mutation in changes:
            with self.subTest(source=name, mutation=mutation):
                self.capture.clear(); self.capture.update(copy.deepcopy(originals[0]))
                self.db.capture_batches.docs[0] = copy.deepcopy(originals[1])
                self.db.audit_log.docs[0] = copy.deepcopy(originals[2])
                target = {'capture': self.capture, 'batch': self.db.capture_batches.docs[0],
                          'audit': self.db.audit_log.docs[0]}[name]
                target.update(mutation)
                self.assertEqual(self.listing('validado')['total'], 0)
                self.assertEqual(self.listing('pendente')['total'], 2)

    def test_explicit_pending_overrides_old_audited_statement(self):
        self.capture['status_revisao'] = 'pendente'
        self.assertEqual(self.listing('validado')['total'], 0)
        self.assertEqual(self.listing('pendente')['total'], 2)

    def test_review_excludes_unconfirmed_cancelled_and_unverified_captures(self):
        for status in ('provisional', 'enriched', 'cancelled'):
            self.db.provisional_transactions.docs.append({**self.capture, '_id': ObjectId(),
                'provisional_id': status, 'status': status})
        unknown = {**self.capture, '_id': ObjectId(), 'provisional_id': 'unknown', 'revision': 2}
        unknown.pop('status_revisao'); unknown.pop('revisado_em'); unknown.pop('capture_batch_id')
        self.db.provisional_transactions.docs.append(unknown)
        self.assertEqual(self.listing()['total'], 3)
        self.assertEqual(self.listing('validado')['total'], 1)
        self.assertEqual(self.listing('pendente')['total'], 2)

    def test_offset_and_cursor_pages_cover_both_sources_without_duplicates(self):
        first = self.listing(limit=1)
        second = self.listing(limit=1, offset=1)
        next_page = self.listing(limit=1, cursor=first['next_cursor'])
        self.assertEqual(first['items'][0]['provisional_id'], self.capture['provisional_id'])
        self.assertEqual(second['items'][0]['id'], str(self.ledger['_id']))
        self.assertEqual(next_page['items'], second['items'])
        self.assertFalse(next_page['has_more'])

    def test_search_and_account_filters_include_confirmed_statement(self):
        self.old_capture()
        self.assertEqual(self.listing('validado', search='Padaria')['total'], 1)
        self.assertEqual(self.listing('validado', search='Mercado')['total'], 0)
        self.assertEqual(self.listing('validado', account_id=str(self.account))['total'], 1)

    def test_edit_reopens_confirmed_capture_and_explicit_validation_is_atomic(self):
        result = update_provisional(self.capture['provisional_id'],
            ProvisionalUpdate(expected_revision=1, detalhe='Conferir vendedor'), self.db)
        self.assertEqual(result['item']['status_revisao'], 'pendente')
        self.assertNotIn('revisado_em', self.capture)
        self.assertEqual(self.capture['status'], 'confirmed')
        self.assertEqual(self.listing('pendente')['total'], 2)
        result = update_provisional(self.capture['provisional_id'],
            ProvisionalUpdate(expected_revision=2, detalhe='Vendedor conferido', validate_review=True), self.db)
        self.assertEqual(result['item']['status_revisao'], 'validado')
        self.assertEqual(self.capture['detalhe'], 'Vendedor conferido')
        self.assertIn('revisado_em', self.capture)
        self.assertEqual(len(self.db.transactions.docs), 1)

    def test_noop_preserves_validation_and_status_only_change_checks_revision(self):
        result = update_provisional(self.capture['provisional_id'],
            ProvisionalUpdate(expected_revision=1, tipo='compra'), self.db)
        self.assertFalse(result['changed'])
        self.assertEqual(self.capture['revision'], 1)
        update_provisional(self.capture['provisional_id'],
            ProvisionalUpdate(expected_revision=1, validate_review=False), self.db)
        with self.assertRaises(HTTPException) as failure:
            update_provisional(self.capture['provisional_id'],
                ProvisionalUpdate(expected_revision=1, validate_review=True), self.db)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.capture['status_revisao'], 'pendente')

    def test_capture_review_validation_rolls_back_with_edit_on_audit_failure(self):
        before = copy.deepcopy(self.capture)
        self.db['audit_log'] = FailsOnce()
        with patch('app.main.mongo_transaction', rollback_fake):
            with self.assertRaises(RuntimeError):
                update_provisional(self.capture['provisional_id'],
                    ProvisionalUpdate(expected_revision=1, detalhe='Mudança', validate_review=True), self.db)
        self.assertEqual(self.db.provisional_transactions.docs[0], before)


if __name__ == '__main__': unittest.main()
