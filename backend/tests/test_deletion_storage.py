import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pymongo.errors import OperationFailure

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.main import mongo_write_conflict
from app.movement_deletion import initialize_deletion_storage
from app.synthetic_testing import require_synthetic_target


class DeletionStorageTests(unittest.TestCase):
    def test_archive_is_reserved_without_financial_document_writes(self):
        db = Mock()
        initialize_deletion_storage(db)
        db.deleted_movements.create_index.assert_called_once_with(
            [('deleted_at', -1)], name='deleted_movement_history')
        self.assertEqual(len(db.mock_calls), 1)

    def test_collection_quota_has_actionable_response(self):
        error = OperationFailure('Cannot create namespace: too many collections', 8000)
        response = mongo_write_conflict(None, error)
        self.assertEqual(response.status_code, 503)
        self.assertIn('limite de cole', response.body.decode())
        self.assertIn('abortada', response.body.decode())

    def test_other_atlas_errors_are_not_reported_as_quota(self):
        response = mongo_write_conflict(None, OperationFailure('authentication failure', 8000))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('limite de cole', response.body.decode())

    def test_cloud_synthetic_databases_require_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            for uri in ('mongodb+srv://example.invalid/db', 'mongodb://example.invalid/db',
                        'mongodb://localhost.example.invalid/db', 'mongodb://127.0.0.1:27028,other.invalid/db'):
                with self.subTest(uri=uri), self.assertRaises(RuntimeError):
                    require_synthetic_target(uri)

    def test_local_replicas_and_authenticated_loopback_are_allowed(self):
        with patch.dict(os.environ, {}, clear=True):
            for uri in ('mongodb://127.0.0.1:27028/?replicaSet=auditfixes',
                        'mongodb://localhost/db', 'mongodb://[::1]:27017/db',
                        'mongodb://user:password@localhost:27017/db'):
                with self.subTest(uri=uri):
                    require_synthetic_target(uri)

    def test_deliberate_remote_run_can_opt_in(self):
        with patch.dict(os.environ, {'FIN_ALLOW_REMOTE_TEST_DATABASE': '1'}, clear=True):
            require_synthetic_target('mongodb+srv://example.invalid/db')
