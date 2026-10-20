"""Atomic business writes on MongoDB; lightweight fake DBs remain testable."""
from contextlib import contextmanager

from pymongo.database import Database
from pymongo.errors import OperationFailure

DEFAULT_CURSOR_BATCH_SIZE = 2000


class AtomicUnavailable(Exception):
    pass


def with_batch_size(cursor, size: int = DEFAULT_CURSOR_BATCH_SIZE):
    """Set a large batchSize on MongoDB cursors to avoid extra getMore roundtrips."""
    if cursor is not None and not isinstance(cursor, (list, tuple, dict, set)) and hasattr(cursor, "batch_size"):
        try:
            batched = cursor.batch_size(size)
            if not hasattr(batched, "_mock_name") or "batch_size" in getattr(cursor, "__dict__", {}):
                return batched
        except Exception:
            return cursor
    return cursor


class _SessionCollection:
    def __init__(self, collection, session):
        self.collection = collection
        self.session = session

    def __getattr__(self, name):
        method = getattr(self.collection, name)
        if name not in {
            "distinct", "find", "find_one", "count_documents", "aggregate",
            "insert_one", "insert_many", "update_one", "update_many",
            "replace_one", "delete_one", "bulk_write", "find_one_and_update",
        }:
            return method

        def query(*args, **kwargs):
            result = method(*args, session=self.session, **kwargs)
            if name == "find":
                return with_batch_size(result, DEFAULT_CURSOR_BATCH_SIZE)
            return result

        return query


class _SessionDatabase:
    def __init__(self, db, session):
        self.db = db
        self.session = session

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _SessionCollection(getattr(self.db, name), self.session)

    def __getitem__(self, name):
        return _SessionCollection(self.db[name], self.session)


def session_database(db, session):
    return _SessionDatabase(db, session) if session is not None else db


@contextmanager
def mongo_transaction(db):
    if not isinstance(db, Database):
        yield None
        return
    with db.client.start_session() as session:
        try:
            with session.start_transaction():
                yield session
        except OperationFailure as exc:
            if exc.code == 20:
                raise AtomicUnavailable("MongoDB precisa operar como replica set para decisões atômicas.") from exc
            raise


def session_args(session):
    return {"session": session} if session is not None else {}


@contextmanager
def mongo_snapshot(db):
    """Read-only context without opening a transaction (avoids commitTransaction RTT on GET routes)."""
    if not isinstance(db, Database):
        yield db, "best_effort"
        return
    yield db, "snapshot"
