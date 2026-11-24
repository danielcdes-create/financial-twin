"""Unit and contract tests for app/atomic.py, app/movement_insert_lock.py, and app/pagination.py."""
import base64
import json
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from bson import ObjectId
from fastapi import HTTPException
from pymongo.database import Database
from pymongo.errors import OperationFailure

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from app.atomic import (
    DEFAULT_CURSOR_BATCH_SIZE,
    AtomicUnavailable,
    _SessionCollection,
    _SessionDatabase,
    mongo_snapshot,
    mongo_transaction,
    session_args,
    session_database,
    with_batch_size,
)
from app.movement_insert_lock import lock_movement_insert
from app.pagination import page
from test_ingestion import Collection


class StubSession:
    def __init__(self, *, raise_on_start: Exception | None = None):
        self.raise_on_start = raise_on_start
        self.in_transaction = False
        self.started_transactions = 0
        self.ended = False
        self._movement_insert_locked = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.in_transaction = False
        self.ended = True
        return False

    @contextmanager
    def start_transaction(self):
        if self.raise_on_start is not None:
            raise self.raise_on_start
        self.started_transactions += 1
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False


class StubMongoDatabase(Database):
    """Subclass of pymongo.database.Database with zero network activity for testing session/transaction hooks."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or (lambda: StubSession())
        self.started_sessions: list[StubSession] = []
        self._client = SimpleNamespace(start_session=self._start_session)
        self.capture_batch_locks = MagicMock()
        self.collections: dict[str, MagicMock] = {}

    @property
    def client(self):
        return self._client

    def _start_session(self):
        sess = self._session_factory()
        self.started_sessions.append(sess)
        return sess

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self.collections:
            col = MagicMock()
            cursor = SimpleNamespace(batch_applied=None)

            def _batch_size(sz):
                cursor.batch_applied = sz
                return cursor

            cursor.batch_size = _batch_size
            col.find.return_value = cursor
            col.index_name = f"idx_{name}"
            self.collections[name] = col
        return self.collections[name]

    def __getitem__(self, name: str):
        return getattr(self, name)


class CountingCollection(Collection):
    def __init__(self, docs=None):
        super().__init__(docs)
        self.count_calls = 0

    def count_documents(self, q=None, **kw):
        self.count_calls += 1
        return super().count_documents(q, **kw)


class AtomicAndPaginationTests(unittest.TestCase):
    def test_with_batch_size_handles_cursors_collections_errors_and_mocks(self):
        for literal in (None, [1, 2], (1, 2), {"a": 1}, {1, 2}):
            self.assertIs(with_batch_size(literal), literal)

        class RealCursor:
            def __init__(self):
                self.size = None

            def batch_size(self, sz):
                self.size = sz
                return self

        cur = RealCursor()
        self.assertIs(with_batch_size(cur), cur)
        self.assertEqual(cur.size, DEFAULT_CURSOR_BATCH_SIZE)

        class BrokenCursor:
            def batch_size(self, _sz):
                raise RuntimeError("cursor already closed")

        broken = BrokenCursor()
        self.assertIs(with_batch_size(broken), broken)

        # Unconfigured MagicMock should not replace iterable mock with child mock
        bare_mock = MagicMock()
        self.assertIs(with_batch_size(bare_mock), bare_mock)

    def test_session_database_and_session_collection_inject_session_and_batch_size(self):
        stub_db = StubMongoDatabase()
        fake_session = StubSession()

        self.assertIs(session_database(stub_db, None), stub_db)
        self.assertEqual(session_args(None), {})
        self.assertEqual(session_args(fake_session), {"session": fake_session})

        wrapped = session_database(stub_db, fake_session)
        self.assertIsInstance(wrapped, _SessionDatabase)
        with self.assertRaises(AttributeError):
            _ = wrapped._private_attr

        col_attr = wrapped.transactions
        col_item = wrapped["transactions"]
        self.assertIsInstance(col_attr, _SessionCollection)
        self.assertIsInstance(col_item, _SessionCollection)

        # Non-session attribute passes straight through without wrapping
        self.assertEqual(col_attr.index_name, "idx_transactions")

        # Session-aware methods inject session=fake_session, and find() applies batch_size(2000)
        cursor = col_attr.find({"fluxo": "saida"})
        stub_db.transactions.find.assert_called_once_with({"fluxo": "saida"}, session=fake_session)
        self.assertEqual(cursor.batch_applied, DEFAULT_CURSOR_BATCH_SIZE)

        col_item.update_one({"_id": 1}, {"$set": {"ok": True}}, upsert=True)
        stub_db.transactions.update_one.assert_called_once_with(
            {"_id": 1}, {"$set": {"ok": True}}, session=fake_session, upsert=True
        )

    def test_mongo_transaction_and_snapshot_semantics_and_error_translation(self):
        # 1. Fake DB: yields None and best_effort without opening sessions
        fake_db = SimpleNamespace()
        with mongo_transaction(fake_db) as sess:
            self.assertIsNone(sess)
        with mongo_snapshot(fake_db) as (snap_db, consistency):
            self.assertIs(snap_db, fake_db)
            self.assertEqual(consistency, "best_effort")

        # 2. Real Database subclass: starts session & transaction, resets _movement_insert_locked in finally
        real_db = StubMongoDatabase()
        with mongo_snapshot(real_db) as (snap_db, consistency):
            self.assertIs(snap_db, real_db)
            self.assertEqual(consistency, "snapshot")
        self.assertEqual(len(real_db.started_sessions), 0)

        with mongo_transaction(real_db) as sess:
            self.assertIsInstance(sess, StubSession)
            self.assertTrue(sess.in_transaction)
            sess._movement_insert_locked = True
        self.assertEqual(len(real_db.started_sessions), 1)
        self.assertTrue(real_db.started_sessions[0].ended)
        self.assertFalse(real_db.started_sessions[0]._movement_insert_locked)

        # 3. OperationFailure(code=20) -> AtomicUnavailable (and finally still resets lock flag)
        standalone_db = StubMongoDatabase(
            session_factory=lambda: StubSession(
                raise_on_start=OperationFailure("IllegalOperation: replica set required", code=20)
            )
        )
        with self.assertRaises(AtomicUnavailable):
            with mongo_transaction(standalone_db):
                pass
        self.assertFalse(standalone_db.started_sessions[0]._movement_insert_locked)

        # 4. Non-20 OperationFailure (e.g. code=112 WriteConflict) is re-raised directly
        conflict_db = StubMongoDatabase(
            session_factory=lambda: StubSession(
                raise_on_start=OperationFailure("WriteConflict", code=112)
            )
        )
        with self.assertRaises(OperationFailure) as ctx:
            with mongo_transaction(conflict_db):
                pass
        self.assertEqual(ctx.exception.code, 112)

    def test_lock_movement_insert_runs_once_per_active_transaction_and_resets_after(self):
        # Fake DB is a no-op
        fake_db = SimpleNamespace(capture_batch_locks=MagicMock())
        lock_movement_insert(fake_db)
        fake_db.capture_batch_locks.update_one.assert_not_called()

        # Database subclass inside mongo_transaction: locks once, skips duplicates while in_transaction=True
        stub_db = StubMongoDatabase()
        with mongo_transaction(stub_db) as sess:
            writing = session_database(stub_db, sess)
            lock_movement_insert(writing)
            lock_movement_insert(writing)
            lock_movement_insert(stub_db, session=sess)
            self.assertTrue(sess._movement_insert_locked)
            self.assertEqual(stub_db.capture_batch_locks.update_one.call_count, 1)
            stub_db.capture_batch_locks.update_one.assert_called_once_with(
                {"_id": "movement-insert"},
                {"$inc": {"revision": 1}},
                upsert=True,
                session=sess,
            )

        # After mongo_transaction exits, _movement_insert_locked is cleared so next transaction acquires lock again
        with mongo_transaction(stub_db) as sess2:
            lock_movement_insert(session_database(stub_db, sess2))
            self.assertEqual(stub_db.capture_batch_locks.update_one.call_count, 2)

        # Standalone Database call without session omits session kwarg
        lock_movement_insert(stub_db, session=None)
        self.assertEqual(stub_db.capture_batch_locks.update_one.call_count, 3)
        stub_db.capture_batch_locks.update_one.assert_called_with(
            {"_id": "movement-insert"},
            {"$inc": {"revision": 1}},
            upsert=True,
        )

    def test_pagination_across_ties_epoch_fallbacks_total_optimization_and_cursor_validation(self):
        t_hi = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        t_tie = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

        oid_1 = ObjectId("66f900000000000000000001")
        oid_2 = ObjectId("66f900000000000000000002")
        oid_3 = ObjectId("66f900000000000000000003")
        oid_4 = ObjectId("66f900000000000000000004")
        oid_5 = ObjectId("66f900000000000000000005")

        # Seed 5 matching docs: 1 newest, 2 tied at t_tie, 1 with None date, 1 with missing date, plus 1 filtered out
        col = CountingCollection([
            {"_id": oid_1, "status": "open"},  # missing date -> _EPOCH
            {"_id": oid_2, "status": "open", "updated_at": None},  # None date -> _EPOCH
            {"_id": oid_3, "status": "open", "updated_at": t_tie},
            {"_id": oid_4, "status": "open", "updated_at": t_tie},
            {"_id": oid_5, "status": "open", "updated_at": t_hi},
            {"_id": ObjectId(), "status": "closed", "updated_at": t_hi},
        ])

        # Single-page optimization: when all 5 fit in limit=10 and cursor=None, count_documents is NOT called
        all_rows, single_info = page(col, {"status": "open"}, "updated_at", limit=10, cursor=None)
        self.assertEqual(len(all_rows), 5)
        self.assertEqual(single_info["total"], 5)
        self.assertFalse(single_info["has_more"])
        self.assertIsNone(single_info["next_cursor"])
        self.assertEqual(col.count_calls, 0)

        # Multi-page traversal with limit=2 (Page 1 -> Page 2 -> Page 3), including timestamp tie & _EPOCH tie!
        p1, info1 = page(col, {"status": "open"}, "updated_at", limit=2, cursor=None)
        self.assertEqual([r["_id"] for r in p1], [oid_5, oid_4])
        self.assertTrue(info1["has_more"])
        self.assertEqual(info1["total"], 5)
        self.assertEqual(col.count_calls, 1)

        p2, info2 = page(col, {"status": "open"}, "updated_at", limit=2, cursor=info1["next_cursor"])
        self.assertEqual([r["_id"] for r in p2], [oid_3, oid_2])
        self.assertTrue(info2["has_more"])
        self.assertEqual(info2["total"], 5)

        # info2["next_cursor"] anchors on oid_2 whose updated_at is None (_EPOCH fallback!)
        p3, info3 = page(col, {"status": "open"}, "updated_at", limit=2, cursor=info2["next_cursor"])
        self.assertEqual([r["_id"] for r in p3], [oid_1])
        self.assertFalse(info3["has_more"])
        self.assertIsNone(info3["next_cursor"])
        self.assertEqual(info3["total"], 5)

        # Invalid base64 / malformed cursor -> 422
        for bad_cursor in ("%%%not-base64%%%", base64.urlsafe_b64encode(b'{"bad":1}').decode()):
            with self.assertRaises(HTTPException) as err:
                page(col, {"status": "open"}, "updated_at", limit=2, cursor=bad_cursor)
            self.assertEqual(err.exception.status_code, 422)

        # Mismatched filter signature -> 422
        with self.assertRaises(HTTPException) as err_filter:
            page(col, {"status": "closed"}, "updated_at", limit=2, cursor=info1["next_cursor"])
        self.assertEqual(err_filter.exception.status_code, 422)

        # Mismatched sort field -> 422
        with self.assertRaises(HTTPException) as err_field:
            page(col, {"status": "open"}, "created_at", limit=2, cursor=info1["next_cursor"])
        self.assertEqual(err_field.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
