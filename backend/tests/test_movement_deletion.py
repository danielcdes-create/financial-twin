import copy
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.movement_deletion import DeleteMovement, preview_deletion, delete_movement
from app.analytics import calculate_financial_totals
from app.transaction_service import TransactionService
from app.main import app
from app.insights import Undo, undo_evidence
from test_ingestion import Collection, FakeDB, event
from test_architecture_failures import rollback_fake, FailsOnce


def values(document, path):
    if not path: return [document]
    if isinstance(document, list): return [v for item in document for v in values(item, path)]
    if not isinstance(document, dict): return [None]
    return values(document.get(path[0]), path[1:])


class DeletionCollection(Collection):
    def insert_one(self, document):
        return super().insert_one(copy.deepcopy(document))

    def _matches(self, document, query):
        for key, wanted in query.items():
            if key == '$or':
                if not any(self._matches(document, q) for q in wanted): return False
                continue
            candidates = values(document, key.split('.'))
            if isinstance(wanted, dict) and '$in' in wanted:
                if not any(value in wanted['$in'] for value in candidates): return False
            elif isinstance(wanted, dict) and '$ne' in wanted:
                if any(value == wanted['$ne'] for value in candidates): return False
            elif not any(value == wanted or (isinstance(value, list) and wanted in value) for value in candidates): return False
        return True

    def update_many(self, query, update):
        for row in list(self.find(query)): self.update_one({'_id': row['_id']}, update)

    def delete_one(self, query):
        row = self.find_one(query)
        if row: self.docs.remove(row)


class DeletionDB(FakeDB):
    def __missing__(self, key):
        self[key] = DeletionCollection(); return self[key]


class MovementDeletionTests(unittest.TestCase):
    def setUp(self):
        self.db = DeletionDB()
        self.stamp = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        self.row = {'_id': ObjectId(), 'data_evento': self.stamp, 'valor': Decimal128('-12.59'),
            'fluxo': 'saida', 'classification': {'migration_reason': 'despesa_real'}, 'categoria': 'Alimentação'}
        self.identity = str(self.row['_id'])
        self.db.transactions.docs.append(copy.deepcopy(self.row))

    def body(self, source='ledger', identity=None):
        preview = preview_deletion(self.db, source, identity or self.identity)
        return DeleteMovement(preview_token=preview['preview_token'])

    def test_deleting_one_duplicate_preserves_original_and_other_row(self):
        other = {**self.row, '_id': ObjectId()}
        self.db.transactions.docs.append(other)
        self.assertEqual(calculate_financial_totals(self.db.transactions.docs)['expenses'], Decimal128('25.18').to_decimal())
        body = self.body()
        delete_movement(self.db, 'ledger', self.identity, body)
        self.assertEqual(self.db.transactions.docs, [other])
        self.assertEqual(self.db.deleted_movements.docs[0]['document'], self.row)
        self.assertEqual(calculate_financial_totals(self.db.transactions.docs)['expenses'], Decimal128('12.59').to_decimal())
        self.assertTrue(delete_movement(self.db, 'ledger', self.identity, body)['duplicate'])
        self.assertEqual(len(self.db.audit_log.docs), 1)

    def test_capture_cancelled_and_event_retry_does_not_recreate(self):
        service = TransactionService(self.db)
        payload = event('delete-retry', 'manual', self.stamp, 'Outra loja')
        service.process(payload)
        row = self.db.provisional_transactions.docs[0]
        row.update({'status': 'confirmed', 'revision': 3})
        identity = row['provisional_id']; original = copy.deepcopy(row)
        delete_movement(self.db, 'capture', identity, self.body('capture', identity))
        self.assertEqual(row['status'], 'cancelled')
        self.assertEqual(row['revision'], 4)
        self.assertEqual(self.db.deleted_movements.docs[0]['document'], original)
        self.assertTrue(service.process(payload)['duplicate'])
        self.assertEqual(len(self.db.provisional_transactions.docs), 1)
        self.assertFalse(preview_active(self.db))

    def test_deletion_preserves_descriptor_and_updates_coverage_once(self):
        descriptor={'_id':self.row['_id'],'key':'loja sintética'}
        self.db.transaction_descriptor_index.docs.append(copy.deepcopy(descriptor))
        self.db.schema_migrations.docs.append({'_id':'descriptor_index_v1','complete':True,'scanned':1})
        body=self.body();delete_movement(self.db,'ledger',self.identity,body)
        self.assertEqual(self.db.deleted_movements.docs[0]['descriptor_index'],descriptor)
        self.assertFalse(self.db.transaction_descriptor_index.docs)
        self.assertEqual(self.db.schema_migrations.docs[0]['scanned'],0)
        delete_movement(self.db,'ledger',self.identity,body)
        self.assertEqual(self.db.schema_migrations.docs[0]['scanned'],0)

    def test_deletion_does_not_make_incomplete_descriptor_coverage_look_complete(self):
        self.db.schema_migrations.docs.append({'_id':'descriptor_index_v1','complete':True,'scanned':0})
        delete_movement(self.db,'ledger',self.identity,self.body())
        self.assertFalse(self.db.schema_migrations.docs[0]['complete'])

    def test_stale_confirmation_cannot_delete_edited_record(self):
        body = self.body(); self.db.transactions.docs[0]['categoria'] = 'Saúde'
        with self.assertRaises(HTTPException) as failure: delete_movement(self.db, 'ledger', self.identity, body)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(len(self.db.transactions.docs), 1)
        self.assertFalse(self.db.deleted_movements.docs)

    def test_new_link_after_preview_blocks_deletion(self):
        body = self.body()
        self.db.commitments.docs.append({'linked_transactions': [{'transaction_id': self.identity}]})
        with self.assertRaises(HTTPException) as failure: delete_movement(self.db, 'ledger', self.identity, body)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertFalse(self.db.deleted_movements.docs)

    def test_reconciliation_transfer_and_refund_links_block(self):
        for collection, link in [
            ('reconciliations', {'origem': {'entity': 'transactions', 'id': self.row['_id']}}),
            ('provisional_transactions', {'status': 'confirmed', 'transfer': {'source_transaction_id': self.identity}}),
            ('refund_claims', {'_id': 'ledger:' + self.identity}),
            ('financial_cases', {'source_refs': ['ledger:' + self.identity]}),
        ]:
            with self.subTest(collection=collection):
                self.db[collection].docs.append(link)
                self.assertFalse(preview_deletion(self.db, 'ledger', self.identity)['can_delete'])
                self.db[collection].docs.clear()

    def test_audit_failure_rolls_back_archive_and_deletion(self):
        body = self.body()
        self.db['audit_log'] = FailsOnce()
        self.db.deleted_movements.docs = []
        with patch('app.movement_deletion.mongo_transaction', rollback_fake):
            with self.assertRaises(RuntimeError): delete_movement(self.db, 'ledger', self.identity, body)
        self.assertEqual(self.db.transactions.docs, [self.row])
        self.assertEqual(self.db.deleted_movements.docs, [])

    def test_evidence_history_requires_explicit_unlink_before_deletion(self):
        evidence = {'_id': ObjectId(), 'transaction_id': self.row['_id'], 'event_id': 'support'}
        self.db.transaction_evidence.docs.append(evidence)
        self.db.source_records.docs.append({'_id': 'original-source'})
        self.db.raw_events.docs.append({'_id': 'raw', 'event_id': 'support', 'processing_status': 'matched'})
        self.db.review_queue.docs.append({'_id': 'review', 'status': 'pending', 'candidate_transaction_id': self.row['_id']})
        self.assertFalse(preview_deletion(self.db,'ledger',self.identity)['can_delete'])
        with self.assertRaises(HTTPException) as error:delete_movement(self.db, 'ledger', self.identity, self.body())
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(self.db.transactions.docs,[self.row])
        self.assertEqual(self.db.transaction_evidence.docs, [evidence])
        self.assertEqual(len(self.db.source_records.docs), 1)
        self.assertEqual(self.db.raw_events.docs[0]['processing_status'], 'matched')
        self.assertEqual(self.db.review_queue.docs[0]['status'], 'pending')
        # An explicitly reversed link remains as history and permits deletion.
        evidence['status']='reversed'
        delete_movement(self.db, 'ledger', self.identity, self.body())
        self.assertEqual(self.db.transaction_evidence.docs,[evidence])
        self.assertEqual(self.db.review_queue.docs[0]['status'],'resolved')

    def test_undo_evidence_cannot_resurrect_deleted_capture(self):
        identity = 'removed-capture'
        self.db.deleted_movements.docs.append({'_id': 'capture:' + identity})
        self.db.transaction_evidence.docs.append({'event_id': 'evidence', 'target_type': 'provisional',
            'provisional_id': identity, 'revision': 1})
        with self.assertRaises(HTTPException) as failure:
            undo_evidence('evidence', Undo(request_id=uuid4(), expected_revision=1, reason='Conferência de vínculo'), self.db)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_preview_and_delete_require_session_when_protected(self):
        with patch('app.auth.get_settings', return_value=SimpleNamespace(environment='production', app_password='protected')):
            http = TestClient(app)
            path = '/v1/movements/ledger/' + self.identity
            self.assertEqual(http.get(path + '/deletion').status_code, 401)
            self.assertEqual(http.request('DELETE', path, json={'preview_token': 'a' * 64}).status_code, 401)


def preview_active(db):
    return list(db.provisional_transactions.find({'status': {'$in': ['provisional', 'enriched', 'confirmed']}}))
