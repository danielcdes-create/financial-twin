"""Durable upload identities: an ACK belongs to immutable bytes and one purpose."""
from datetime import datetime, timezone
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from app.atomic import mongo_transaction, session_database


def bind_upload(db, event_id, kind, sha256, context=None):
    if event_id is None:
        return None
    identity = str(event_id)
    key='upload:'+identity
    document = {'_id': key, 'event_id': identity, 'kind': 'capture_upload', 'purpose':kind, 'sha256': sha256,'context':context or {},
                'status': 'awaiting_upload', 'created_at': datetime.now(timezone.utc)}
    # The already reserved command namespace avoids creating a collection at
    # the first capture on financial clusters with a collection limit.
    current = db.batch_commands.find_one({'_id': key})
    if not current:
        try:
            db.batch_commands.insert_one(document)
            current = document
        except DuplicateKeyError:
            current = db.batch_commands.find_one({'_id': key})
    if not current or current.get('purpose') != kind or current['sha256'] != sha256 or current.get('context',{})!=(context or {}):
        raise HTTPException(409, 'Esta tentativa já está vinculada a outro arquivo ou finalidade. Preserve o original e use uma nova tentativa.')
    return current.get('result') if current.get('status') == 'completed' else None


def complete_upload(db, event_id, kind, sha256, result, context=None):
    acknowledgement = {**result, 'file_sha256': sha256}
    if event_id is None:
        return acknowledgement
    identity = str(event_id)
    acknowledgement.update({'event_id': identity, 'operation_id': identity})
    if isinstance(acknowledgement.get('processing'), dict):
        processing = acknowledgement['processing']
        acknowledgement['processing'] = {**processing, 'persisted_event_id': processing.get('event_id'), 'event_id': identity}
    if acknowledgement.get('persisted') is not True:
        return acknowledgement
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        previous = bind_upload(writing, identity, kind, sha256,context)
        if previous is not None:
            return previous
        updated=writing.batch_commands.update_one({'_id': 'upload:'+identity, 'purpose': kind, 'sha256': sha256, 'status': 'awaiting_upload'},
            {'$set': {'status': 'completed', 'result': acknowledgement, 'completed_at': datetime.now(timezone.utc)}})
        if updated is not None and updated.matched_count!=1:
            raise HTTPException(409,'A confirmação desta tentativa mudou. Reenvie o arquivo original com a mesma identidade.')
    return acknowledgement
