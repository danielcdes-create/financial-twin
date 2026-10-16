"""Explicit deletion from active movements, with atomic retained originals."""
import hashlib
from datetime import datetime, timezone
from typing import Literal

from bson import ObjectId, json_util
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.atomic import mongo_transaction, session_database
from app.movement_insert_lock import lock_movement_insert

Source = Literal['ledger', 'capture']


def initialize_deletion_storage(db):
    # Reserve the archive namespace before a user starts a financial transaction.
    # Otherwise the first deletion may fail at the shared Atlas collection limit.
    db.deleted_movements.create_index([('deleted_at', -1)], name='deleted_movement_history')


class DeleteMovement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')


def identity_query(source, identity):
    if source == 'ledger':
        if not ObjectId.is_valid(identity):
            raise HTTPException(404, 'Registro não encontrado.')
        return {'_id': ObjectId(identity)}
    return {'provisional_id': identity}


def token(document):
    return hashlib.sha256(json_util.dumps(document, sort_keys=True).encode()).hexdigest()


def blockers(db, source, identity, document):
    key = 'transaction_id' if source == 'ledger' else 'provisional_id'
    reasons = []
    if db.commitments.find_one({f'linked_transactions.{key}': identity}):
        reasons.append('Este registro é um pagamento vinculado a um compromisso.')
    reference = f'{source}:{identity}'
    if db.refund_claims.find_one({'_id': reference}) or db.financial_cases.find_one({'$or': [
        {'source_refs': reference}, {'refund_ref': reference},
    ]}):
        reasons.append('Este registro está vinculado a um caso financeiro ou reembolso.')
    ids = [document['_id'], identity]
    if db.reconciliations.find_one({'$or': [
        {'origem.entity': 'transactions' if source == 'ledger' else 'provisional_transactions', 'origem.id': {'$in': ids}},
        {'destino.entity': 'transactions' if source == 'ledger' else 'provisional_transactions', 'destino.id': {'$in': ids}},
        {key: {'$in': ids}},
    ]}):
        reasons.append('Este registro participa de uma conciliação de pagamento.')
    if source == 'ledger' and db.provisional_transactions.find_one({'status': 'confirmed', '$or': [
        {'transfer.source_transaction_id': identity}, {'transfer.destination_transaction_id': identity},
        {'linked_transaction_id': {'$in': ids}},
    ]}):
        reasons.append('Este registro está vinculado a uma captura ou transferência confirmada.')
    evidence_query={'transaction_id':document['_id']} if source=='ledger' else {'provisional_id':identity}
    if db.transaction_evidence.find_one({**evidence_query,'status':{'$ne':'reversed'}}):
        reasons.append('Este registro sustenta evidências vinculadas; desfaça o vínculo antes de excluir.')
    return reasons


def preview_deletion(db, source: Source, identity: str):
    collection = db.transactions if source == 'ledger' else db.provisional_transactions
    document = collection.find_one(identity_query(source, identity))
    if not document or (source == 'capture' and document.get('status') not in {'provisional', 'enriched', 'confirmed'}):
        raise HTTPException(404, 'Registro não encontrado no extrato.')
    reasons = blockers(db, source, identity, document)
    merchant = db.merchants.find_one({'_id': document['merchant_id']}) if document.get('merchant_id') else None
    amount = document.get('valor', 0)
    when = document.get('data_evento')
    return {'preview_token': token(document), 'can_delete': not reasons, 'blockers': reasons,
        'summary': {'name': (merchant or {}).get('nome_padrao') or document.get('vendedor') or document.get('merchant_raw')
            or document.get('estabelecimento_original') or document.get('descricao_original') or 'Movimentação',
            'amount': str(amount.to_decimal() if hasattr(amount, 'to_decimal') else amount),
            'date': when.isoformat() if hasattr(when, 'isoformat') else str(when or '')}}


def delete_movement(db, source: Source, identity: str, body: DeleteMovement):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        lock_movement_insert(writing)
        archive_id = f'{source}:{identity}'
        previous = writing.deleted_movements.find_one({'_id': archive_id})
        if previous:
            if previous['preview_token'] != body.preview_token:
                raise HTTPException(409, 'A confirmação não corresponde ao registro excluído.')
            return {'deleted': True, 'source': source, 'identity': identity, 'duplicate': True}
        collection = writing.transactions if source == 'ledger' else writing.provisional_transactions
        query = identity_query(source, identity)
        document = collection.find_one(query)
        if not document:
            raise HTTPException(404, 'Registro não encontrado.')
        if token(document) != body.preview_token:
            raise HTTPException(409, 'O registro mudou. Reabra a confirmação antes de excluir.')
        if source == 'capture' and document.get('status') not in {'provisional', 'enriched', 'confirmed'}:
            raise HTTPException(409, 'A captura já foi decidida. Recarregue o extrato.')
        reasons = blockers(writing, source, identity, document)
        if reasons:
            raise HTTPException(409, ' '.join(reasons) + ' Revise o vínculo antes de excluir.')
        now = datetime.now(timezone.utc)
        descriptor = writing.transaction_descriptor_index.find_one({'_id': document['_id']}) if source == 'ledger' else None
        marker = writing.schema_migrations.find_one({'_id': 'descriptor_index_v1'}) if source == 'ledger' else None
        active_count = writing.transactions.count_documents({}) if marker and marker.get('complete') else None
        writing.deleted_movements.insert_one({'_id': archive_id, 'source': source, 'identity': identity,
            'document': document, 'preview_token': body.preview_token, 'deleted_at': now,
            'reason': 'user_deleted_from_statement', 'descriptor_index': descriptor})
        if source == 'ledger':
            collection.delete_one(query)
            writing.transaction_descriptor_index.delete_one({'_id': document['_id']})
            if marker and marker.get('complete'):
                if descriptor and marker.get('scanned') == active_count:
                    writing.schema_migrations.update_one({'_id': marker['_id'], 'complete': True, 'scanned': active_count}, {'$inc': {'scanned': -1}})
                else:
                    # An already incomplete index must not accidentally look complete
                    # after the active ledger shrinks to its old coverage count.
                    writing.schema_migrations.update_one({'_id': marker['_id']}, {'$set': {'complete': False}})
        else:
            collection.update_one(query, {'$set': {'status': 'cancelled', 'cancelled_at': now,
                'updated_at': now, 'revision': document.get('revision', 1) + 1}})
        # Durable event identity is retained, so retrying an old capture cannot recreate it.
        if document.get('event_id'):
            writing.raw_events.update_one({'event_id': document['event_id']},
                {'$set': {'processing_status': 'cancelled', 'linked_transaction_id': None}})
        review_query = {'candidate_transaction_id': document['_id']} if source == 'ledger' else {
            '$or': [{'provisional_id': identity}, {'candidate_provisional_id': identity}]}
        writing.review_queue.update_many({'status': 'pending', **review_query}, {'$set': {
            'status': 'resolved', 'decision': 'movement_deleted', 'resolution_reason': 'user_deleted_from_statement',
            'resolved_at': now}})
        writing.audit_log.insert_one({'event': 'movement_deleted', 'movement_source': source,
            'movement_identity': identity, 'archive_id': archive_id, 'created_at': now,
            'source': 'mobile_manual', 'before': document})
        return {'deleted': True, 'source': source, 'identity': identity, 'duplicate': False}
