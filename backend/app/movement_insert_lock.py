"""Serialize posting scans and writes across batches and existing ingestion workers."""
from pymongo.database import Database
from app.atomic import _SessionDatabase, session_args


def lock_movement_insert(db, session=None):
    if isinstance(db, _SessionDatabase): session, db = db.session, db.db
    if isinstance(db, Database):
        if session is not None and getattr(session, '_movement_insert_locked', False) and getattr(session, 'in_transaction', True):
            return
        db.capture_batch_locks.update_one({'_id': 'movement-insert'}, {'$inc': {'revision': 1}},
            upsert=True, **session_args(session))
        if session is not None:
            try:
                setattr(session, '_movement_insert_locked', True)
            except AttributeError:
                pass
