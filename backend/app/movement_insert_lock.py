"""Serialize posting scans and writes across batches and existing ingestion workers."""
from pymongo.database import Database
from app.atomic import _SessionDatabase, session_args


def lock_movement_insert(db, session=None):
    if isinstance(db, _SessionDatabase): session, db = db.session, db.db
    if isinstance(db, Database):
        db.capture_batch_locks.update_one({'_id': 'movement-insert'}, {'$inc': {'revision': 1}},
            upsert=True, **session_args(session))
