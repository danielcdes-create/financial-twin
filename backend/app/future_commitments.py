"""Future-only corrections: imported originals and actual payments stay intact."""
import copy
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from bson import ObjectId, json_util
from bson.decimal128 import Decimal128
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError
from app.auth import require_session
from app.atomic import mongo_snapshot, mongo_transaction, session_database
from app.commitment_estimates import effective_commitments
from app.commitments import COMMITMENT_CUTOFF, as_date, schedule_from
from app.economic_policy import amount, eligible_payment
from app.financial_time import local_date, today
from app.source_tokens import require_source_token

Source = Literal['installment', 'obligation', 'managed']
router = APIRouter(prefix='/v1/future-commitments', dependencies=[Depends(require_session)])

def database(request: Request):
    if request.app.state.db is None: raise HTTPException(503, 'Banco indisponível.')
    return request.app.state.db

def digest(value):
    return hashlib.sha256(json_util.dumps(value, sort_keys=True).encode()).hexdigest()

def load(db, source, identity):
    if source == 'managed':
        document = db.commitments.find_one({'commitment_id': identity})
    else:
        if not ObjectId.is_valid(identity): raise HTTPException(404, 'Compromisso não encontrado.')
        document = db['installment_plans' if source == 'installment' else 'obligations'].find_one({'_id': ObjectId(identity)})
    if not document: raise HTTPException(404, 'Compromisso não encontrado.')
    return document

def due(part):
    value = part.get('data_vencimento')
    return as_date(value) if value else None

def unpaid(db, source, document, part, linked=None):
    if source == 'installment':
        return document.get('status') == 'ativo' and part.get('status') == 'projetada' and not part.get('transaction_id') and not part.get('bill_id')
    if document.get('status') != 'futura': return False
    if (amount(document.get('valor_pago')) or Decimal('0')) != 0: return False
    if linked is not None: return document['_id'] not in linked
    return not db.reconciliations.find_one({'$or': [
        {'origem.entity': 'obligations', 'origem.id': document['_id']},
        {'destino.entity': 'obligations', 'destino.id': document['_id']}, {'obligation_id': document['_id']}]})

def editable(db, source, document, part, linked=None):
    if not unpaid(db, source, document, part, linked): return False
    if source == 'installment':
        exact = due(part)
        month = str(part.get('competencia') or '')
        if exact: return exact >= today()
        try: date.fromisoformat(month + '-01')
        except ValueError: return False
        return month >= today().strftime('%Y-%m')
    return bool(due(document) and due(document) >= today())

def obligation_links(db, documents):
    ids = [document['_id'] for document in documents]
    if not ids: return set()
    links = list(db.reconciliations.find({'$or': [
        {'origem.entity': 'obligations', 'origem.id': {'$in': ids}},
        {'destino.entity': 'obligations', 'destino.id': {'$in': ids}}, {'obligation_id': {'$in': ids}}]}).limit(10001))
    if len(links) > 10000: raise HTTPException(409, 'Vínculos demais para conferir com segurança.')
    result = set()
    for link in links:
        if link.get('obligation_id'): result.add(link['obligation_id'])
        for endpoint in (link.get('origem', {}), link.get('destino', {})):
            if endpoint.get('entity') == 'obligations' and endpoint.get('id'): result.add(endpoint['id'])
    return result

def effective_document(db, source, document, override=None, linked=None):
    result = copy.deepcopy(document)
    if override is None: override = db.future_schedule_overrides.find_one({'_id': f'{source}:{document["_id"]}'})
    if not override: return result
    if source == 'installment' and isinstance(override.get('label'), str) and override['label'].strip():
        result['descricao_original'] = document.get('descricao') or ''
        result['descricao'] = override['label'].strip()
    profile = override.get('profile') if isinstance(override.get('profile'), dict) else None
    if profile:
        result['profile'] = {key: value for key, value in profile.items() if value not in (None, '')}
        for key in ('categoria', 'subcategoria', 'natureza', 'plataforma', 'metodo_pagamento', 'payment_method', 'account_id', 'card_id', 'card_label'):
            if profile.get(key):
                result[key] = profile[key]
        if profile.get('display_name'):
            result['display_name'] = profile['display_name']
    patches = override.get('rows', {})
    original_parts = document.get('installments', []) if source == 'installment' else [document]
    for index, original in enumerate(original_parts, 1):
        patch = patches.get(str(index))
        if not patch: continue
        # A subsequent importer cannot apply a stale correction to a paid/different row.
        if digest(original) != patch['original_token'] or not unpaid(db, source, document, original, linked):
            result['future_correction_needs_review'] = True
            continue
        target = result['installments'][index - 1] if source == 'installment' else result
        if patch['cancelled']:
            target['status'] = 'cancelada'; target['future_excluded'] = True
        else:
            target['valor' if source == 'installment' else 'saldo_aberto'] = Decimal128(patch['amount'])
            if patch['due_date']:
                target['data_vencimento'] = datetime.fromisoformat(patch['due_date']).replace(tzinfo=timezone.utc)
                target['competencia'] = patch['due_date'][:7]
            target['future_edited'] = True
    _mark_payment_links(result, source, override.get('payment_links') or [], int(override.get('revision') or 0))
    return result

def _part_due(part):
    value = due(part)
    return value.isoformat() if value else ''

def _public_link(link):
    return {key: link.get(key) for key in ('transaction_id', 'provisional_id', 'source_kind', 'due_date', 'paid_date', 'actual_amount', 'expected_amount', 'row_id') if link.get(key) not in (None, '')}

def _matching_link(links, part, row_id):
    day = _part_due(part)
    for link in links or []:
        if day and str(link.get('due_date') or '')[:10] == day:
            return link
        if link.get('row_id') == row_id and not day and str(link.get('due_date') or '')[:7] == str(part.get('competencia') or '')[:7]:
            return link
    return None

def _mark_payment_links(result, source, links, revision=0):
    marked = False
    if source == 'installment':
        for index, part in enumerate(result.get('installments') or [], 1):
            match = _matching_link(links, part, index)
            if match:
                part['payment_link'] = _public_link(match)
                marked = True
    else:
        match = _matching_link(links, result, 1)
        if match:
            result['payment_link'] = _public_link(match)
            marked = True
    if marked and revision: result['link_revision'] = revision

def overlay_link_names(db, transaction_ids, capture_ids):
    """Names of imported charges already tied to these movements. Read only."""
    wanted = set(transaction_ids or []) | set(capture_ids or [])
    if not wanted: return {}
    clauses = []
    if transaction_ids: clauses.append({'payment_links.transaction_id': {'$in': list(transaction_ids)}})
    if capture_ids: clauses.append({'payment_links.provisional_id': {'$in': list(capture_ids)}})
    query = clauses[0] if len(clauses) == 1 else {'$or': clauses}
    found = {}
    for override in db.future_schedule_overrides.find(query):
        for link in override.get('payment_links') or []:
            key = link.get('transaction_id') or link.get('provisional_id')
            if key in wanted:
                found[key] = {'id': str(override.get('_id') or ''), 'name': link.get('name') or 'Compromisso importado', 'kind': 'importado'}
    return found

def effective_documents(db, source, documents):
    documents = list(documents)
    if not documents: return []
    overrides = {item['_id']: item for item in db.future_schedule_overrides.find({'_id': {'$in': [f'{source}:{row["_id"]}' for row in documents]}})}
    linked = obligation_links(db, [row for row in documents if f'{source}:{row["_id"]}' in overrides]) if source == 'obligation' else None
    return [effective_document(db, source, document, overrides.get(f'{source}:{document["_id"]}', {}), linked) for document in documents]

def view(db, source, identity):
    original = load(db, source, identity)
    override = None if source == 'managed' else db.future_schedule_overrides.find_one({'_id': f'{source}:{identity}'})
    token = digest({'original': original, 'override': override, 'civil_day': today().isoformat()})
    if source == 'managed':
        original = effective_commitments(db, [original])[0]
        token = digest({'original': original, 'civil_day': today().isoformat()})
        rows = [{'row_id': i, **row, 'editable': True} for i, row in enumerate(schedule_from(original, today(), 600), 1)]
        return {'source': source, 'identity': identity, 'name': original['name'], 'preview_token': token, 'rows': rows,
                'editable_count': sum(row['editable'] for row in rows), 'revision': original.get('revision', 1)}
    current = effective_document(db, source, original, override)
    parts = original.get('installments', []) if source == 'installment' else [original]
    effective_parts = current.get('installments', []) if source == 'installment' else [current]
    rows = []
    for i, (part, actual) in enumerate(zip(parts, effective_parts), 1):
        value = amount(actual.get('valor') if source == 'installment' else actual.get('saldo_aberto'))
        allowed = unpaid(db, source, original, part) and editable(db, source, current, actual) and not actual.get('future_excluded') and not actual.get('payment_link') and value is not None and value > 0
        rows.append({'row_id': i, 'number': part.get('numero') or part.get('parcela_numero'),
                     'due_date': due(actual).isoformat() if due(actual) else None, 'month': actual.get('competencia'),
                     'amount': str(value) if value is not None else None, 'status': actual.get('status'),
                     'editable': allowed, 'excluded': bool(actual.get('future_excluded')),
                     'reason': '' if allowed else 'Histórico, vencida, vinculada a pagamento ou já excluída: preservada.'})
    return {'source': source, 'identity': identity, 'name': current.get('descricao') or f'{current.get("tipo", "Obrigação")} · {current.get("competencia", "")}',
            'preview_token': token, 'rows': rows, 'editable_count': sum(row['editable'] for row in rows),
            'needs_review': bool(current.get('future_correction_needs_review'))}

class FutureChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    row_id: int = Field(ge=1, le=600)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    due_date: date | None = None
    cancelled: bool = False

class FutureWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    rows: list[FutureChange] = Field(min_length=1, max_length=600)

class FutureDelete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')

class FutureLabelWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    name: str = Field(min_length=1, max_length=160)

@router.get('/obligations')
def future_obligations(db=Depends(database)):
    with mongo_snapshot(db) as (reading, consistency):
        return {**future_obligations_view(reading), 'read_consistency': consistency}

def obligation_series_key(document):
    """Group a financing or bill series from the external key the import already stored."""
    key = str(document.get('external_key') or '').strip()
    if key:
        parts = key.split('|')
        last = parts[-1]
        month = len(last) == 7 and last[4:5] == '-' and last[:4].isdigit() and last[5:].isdigit()
        if (last.startswith('parcela-') and last[8:].isdigit()) or month:
            parts = parts[:-1]
        if parts:
            return '|'.join(parts)
    return f"obligation|{document.get('tipo') or 'obrigacao'}|{document.get('origem_id')}|{str(document.get('descricao') or '').strip()}"

def future_obligations_view(db):
    candidates = list(db.obligations.find({'status': 'futura'}).sort('data_vencimento', 1).limit(1001))
    if len(candidates) > 1000: raise HTTPException(409, 'Há mais de mil obrigações; restrinja o cadastro antes de gerenciar.')
    items = []
    linked = obligation_links(db, candidates)
    for original, current in zip(candidates, effective_documents(db, 'obligation', candidates)):
        due_date = due(current)
        # Overdue and still unlinked stays on the list. Editing it remains limited to a due on or after today.
        if due_date is None or due_date < COMMITMENT_CUTOFF: continue
        if not unpaid(db, 'obligation', original, original, linked) or current.get('future_excluded'): continue
        profile = current.get('profile') if isinstance(current.get('profile'), dict) else {}
        items.append({'id': str(original['_id']), 'name': profile.get('display_name') or current.get('descricao') or f'{current.get("tipo", "Obrigação")} · {current.get("competencia", "")}',
                      '_statement_ids': [value for value in (original.get('transaction_id'), original.get('open_statement_id')) if value],
                      'due_date': due(current).isoformat(), 'amount': str(amount(current.get('saldo_aberto'))), 'needs_review': bool(current.get('future_correction_needs_review')),
                      'series_key': obligation_series_key(original), 'external_key': original.get('external_key'),
                      'tipo': original.get('tipo'), 'parcela_numero': original.get('parcela_numero'),
                      'raw_name': original.get('descricao') or '',
                      'display_name': profile.get('display_name') or '',
                      'categoria': profile.get('categoria') or '',
                      'subcategoria': profile.get('subcategoria') or '',
                      **({k: profile[k] for k in ('natureza', 'plataforma', 'metodo_pagamento', 'payment_method', 'account_id', 'card_id', 'card_label') if profile.get(k)}),
                      'linked_purchase': profile.get('linked_purchase') or None,
                      **({'payment_link': current['payment_link']} if current.get('payment_link') else {}),
                      **({'link_revision': current['link_revision']} if current.get('link_revision') else {})})
    from app.installment_labels import linked_statement_snapshots
    groups = {item['id']: list(item.get('_statement_ids') or []) for item in items}
    for item in items:
        link = item.get('payment_link') or {}
        if link.get('transaction_id'):
            groups[item['id']].append(link['transaction_id'])
    snapshots = linked_statement_snapshots(db, groups)
    for item in items:
        item.pop('_statement_ids', None)
        snapshot = snapshots.get(item['id']) or {}
        if not snapshot:
            continue
        item['linked_purchase'] = snapshot
        raw = item.get('raw_name') or ''
        if snapshot.get('vendedor') and item.get('display_name') in {None, '', raw}:
            item['display_name'] = snapshot['vendedor']
            if item.get('name') in {None, '', raw}:
                item['name'] = snapshot['vendedor']
        if snapshot.get('categoria') and not item.get('categoria'):
            item['categoria'] = snapshot['categoria']
        if snapshot.get('subcategoria') and not item.get('subcategoria'):
            item['subcategoria'] = snapshot['subcategoria']
    return {'items': sorted(items, key=lambda item: (item['due_date'], item['id']))}

@router.put('/profile')
def save_profile(body: dict, db=Depends(database)):
    from app.commitment_profile import ProfileWrite, apply_commitment_profile
    return apply_commitment_profile(db, ProfileWrite.model_validate(body))

@router.post('/profile/from-statement')
def save_profile_from_statement(body: dict, db=Depends(database)):
    from app.commitment_profile import StatementProfileWrite, sync_statement_metadata
    return sync_statement_metadata(db, StatementProfileWrite.model_validate(body))

@router.get('/{source}/{identity}')
def preview(source: Source, identity: str, db=Depends(database)):
    with mongo_snapshot(db) as (reading, consistency): return {**view(reading, source, identity), 'read_consistency': consistency}

def write(db, source, identity, body, deleting=False):
    request_digest = digest({'source': source, 'identity': identity, 'deleting': deleting, 'body': body.model_dump(mode='json')})
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            previous = writing.future_commitment_commands.find_one({'_id': str(body.request_id)})
            if previous:
                if previous['request_digest'] != request_digest: raise HTTPException(409, 'Identificador já usado para outra alteração.')
                return {**previous['result'], 'duplicate_request': True}
            reviewed = view(writing, source, identity)
            if reviewed['preview_token'] != body.preview_token: raise HTTPException(409, 'O compromisso mudou. Reabra e confira novamente.')
            original = copy.deepcopy(load(writing, source, identity))
            now = datetime.now(timezone.utc)
            undo_snapshot = None
            if source == 'managed':
                if not deleting: raise HTTPException(422, 'Use o formulário Gerenciar para editar este cadastro.')
                if not reviewed['editable_count']: raise HTTPException(409, 'Não há cobranças futuras que possam ser excluídas.')
                revision_before = int(original.get('revision') or 1)
                undo_snapshot = {'kind': 'managed', 'revision_after': revision_before + 1,
                    'previous_deleted_at': original.get('future_deleted_at'), 'previous_cutoff': _plain_date(original.get('future_cutoff')), 'restored': False}
                updated = writing.commitments.update_one({'_id': original['_id'], 'revision': original.get('revision', {'$exists': False})},
                    {'$set': {'future_deleted_at': now, 'future_cutoff': today().isoformat(), 'updated_at': now}, '$inc': {'revision': 1}})
                if updated is not None and updated.matched_count == 0: raise HTTPException(409, 'O compromisso mudou. Reabra antes de excluir.')
                affected = reviewed['editable_count']
            else:
                available = {row['row_id']: row for row in reviewed['rows']}
                edits = [FutureChange(row_id=row['row_id'], amount=row['amount'], due_date=row['due_date'], cancelled=True) for row in reviewed['rows'] if row['editable']] if deleting else body.rows
                if not edits or len({row.row_id for row in edits}) != len(edits): raise HTTPException(422, 'Selecione parcelas futuras sem repetir linhas.')
                key = f'{source}:{identity}'
                stored = writing.future_schedule_overrides.find_one({'_id': key})
                if deleting:
                    undo_snapshot = {'kind': 'override', 'inserted': stored is None, 'revision_after': (stored or {}).get('revision', 0) + 1,
                        'previous': None if stored is None else {'rows': copy.deepcopy(stored.get('rows') or {}), 'label': stored.get('label')}, 'restored': False}
                patches = copy.deepcopy((stored or {}).get('rows', {}))
                parts = original.get('installments', []) if source == 'installment' else [original]
                for change in edits:
                    row = available.get(change.row_id)
                    if not row or not row['editable']: raise HTTPException(409, 'Uma parcela não é futura ou já tem pagamento. Nenhuma alteração foi gravada.')
                    if change.due_date and change.due_date < today(): raise HTTPException(422, 'O vencimento deve ser hoje ou uma data futura.')
                    patches[str(change.row_id)] = {'original_token': digest(parts[change.row_id - 1]), 'amount': str(change.amount),
                        'due_date': change.due_date.isoformat() if change.due_date else row['due_date'], 'cancelled': change.cancelled}
                replacement = {**({'label': stored['label']} if stored and stored.get('label') else {}),
                    'rows': patches, 'revision': (stored or {}).get('revision', 0) + 1, 'updated_at': now}
                if stored:
                    changed = writing.future_schedule_overrides.update_one({'_id': key, 'revision': stored['revision']}, {'$set': replacement})
                    if changed is not None and changed.matched_count == 0: raise HTTPException(409, 'O cronograma mudou. Reabra antes de salvar.')
                else: writing.future_schedule_overrides.insert_one({'_id': key, **replacement})
                affected = len(edits)
            result = {'persisted': True, 'source': source, 'identity': identity, 'affected_count': affected, 'duplicate_request': False,
                      **({'delete_request_id': str(body.request_id)} if deleting else {})}
            writing.audit_log.insert_one({'event': 'future_commitment_deleted' if deleting else 'future_commitment_edited',
                'source': source, 'identity': identity, 'original': original, 'before_view': reviewed, 'review': body.model_dump(mode='json'), 'created_at': now})
            writing.future_commitment_commands.insert_one({'_id': str(body.request_id), 'request_digest': request_digest, 'result': result,
                'undo_snapshot': undo_snapshot, 'created_at': now})
            return result
    except DuplicateKeyError as exc: raise HTTPException(409, 'Outra alteração foi concluída ao mesmo tempo. Reabra e confira novamente.') from exc

def _plain_date(value):
    if isinstance(value, datetime): return value.date().isoformat()
    if isinstance(value, date): return value.isoformat()
    return value

class FutureUndo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    delete_request_id: UUID

def undo_future(db, source, identity, body: FutureUndo):
    """Reverse one future-override delete. Linked payments and source rows stay untouched."""
    request_digest = digest({'undo': True, 'source': source, 'identity': identity, 'body': body.model_dump(mode='json')})
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            replay = writing.future_commitment_commands.find_one({'_id': str(body.request_id)})
            if replay:
                if replay.get('request_digest') != request_digest: raise HTTPException(409, 'Identificador já usado para outra alteração.')
                return {**replay['result'], 'duplicate_request': True}
            command = writing.future_commitment_commands.find_one({'_id': str(body.delete_request_id)})
            snapshot = (command or {}).get('undo_snapshot')
            if not command or command.get('result', {}).get('source') != source or command.get('result', {}).get('identity') != identity or not snapshot:
                raise HTTPException(409, 'Não há exclusão futura desta série para desfazer.')
            if snapshot.get('restored'): raise HTTPException(409, 'Esta exclusão futura já foi desfeita.')
            now = datetime.now(timezone.utc)
            if snapshot['kind'] == 'managed':
                document = load(writing, source, identity)
                if int(document.get('revision') or 1) != snapshot['revision_after']:
                    raise HTTPException(409, 'O cadastro mudou depois da exclusão. A reversão não foi aplicada.')
                sets = {'updated_at': now}
                unsets = {}
                if snapshot.get('previous_deleted_at') is None: unsets['future_deleted_at'] = ''
                else: sets['future_deleted_at'] = snapshot['previous_deleted_at']
                if snapshot.get('previous_cutoff') is None: unsets['future_cutoff'] = ''
                else: sets['future_cutoff'] = snapshot['previous_cutoff']
                update = {'$set': sets, '$inc': {'revision': 1}}
                if unsets: update['$unset'] = unsets
                writing.commitments.update_one({'_id': document['_id'], 'revision': snapshot['revision_after']}, update)
            else:
                key = f'{source}:{identity}'
                stored = writing.future_schedule_overrides.find_one({'_id': key})
                if not stored or stored.get('revision') != snapshot['revision_after']:
                    raise HTTPException(409, 'O cronograma mudou depois da exclusão. A reversão não foi aplicada.')
                if snapshot['inserted']:
                    writing.future_schedule_overrides.delete_one({'_id': key, 'revision': snapshot['revision_after']})
                else:
                    previous = snapshot['previous'] or {}
                    replacement = {'rows': copy.deepcopy(previous.get('rows') or {}), 'revision': stored.get('revision', 0) + 1, 'updated_at': now}
                    if previous.get('label'): replacement['label'] = previous['label']
                    writing.future_schedule_overrides.update_one({'_id': key, 'revision': snapshot['revision_after']},
                        {'$set': replacement, **({} if previous.get('label') else {'$unset': {'label': ''}})})
            restored = {**snapshot, 'restored': True}
            result = {'restored': True, 'source': source, 'identity': identity, 'duplicate_request': False}
            writing.future_commitment_commands.update_one({'_id': str(body.delete_request_id)}, {'$set': {'undo_snapshot': restored}})
            writing.future_commitment_commands.insert_one({'_id': str(body.request_id), 'request_digest': request_digest, 'result': result, 'created_at': now})
            writing.audit_log.insert_one({'event': 'future_commitment_delete_undone', 'source': source, 'identity': identity,
                'delete_request_id': str(body.delete_request_id), 'created_at': now})
            return result
    except DuplicateKeyError as exc: raise HTTPException(409, 'Outra alteração foi concluída ao mesmo tempo. Reabra e confira novamente.') from exc

class ImportedPaymentLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    expected_source_token: str = Field(min_length=16, max_length=128)
    due_date: date | None = None
    competence: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$')
    transaction_id: str | None = None
    provisional_id: str | None = None

class ImportedPaymentUnlink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    expected_revision: int = Field(ge=1)
    transaction_id: str | None = None
    provisional_id: str | None = None

def _movement_for_link(db, body):
    if bool(body.transaction_id) == bool(body.provisional_id):
        raise HTTPException(422, 'Informe o lançamento do extrato ou da captura confirmada.')
    if body.transaction_id:
        if not ObjectId.is_valid(body.transaction_id): raise HTTPException(422, 'Movimentação inválida.')
        document = db.transactions.find_one({'_id': ObjectId(body.transaction_id)})
        return document, 'transaction_id', body.transaction_id, 'transaction'
    document = db.provisional_transactions.find_one({'provisional_id': body.provisional_id, 'status': 'confirmed'})
    if document and document.get('linked_transaction_id'):
        raise HTTPException(409, 'Esta captura já corresponde a uma movimentação importada.')
    return document, 'provisional_id', body.provisional_id, 'confirmed_capture'

def _charge_for_link(db, source, identity, due_date, competence):
    if source == 'managed': raise HTTPException(422, 'O cadastro gerenciado usa o vínculo do próprio compromisso.')
    if bool(due_date) == bool(competence): raise HTTPException(422, 'Informe o vencimento da parcela.')
    original = load(db, source, identity)
    current = effective_document(db, source, original)
    parts = list(enumerate(current.get('installments') or [], 1)) if source == 'installment' else [(1, current)]
    wanted = due_date.isoformat() if due_date else ''
    def matches(part):
        if wanted: return _part_due(part) == wanted
        return not _part_due(part) and str(part.get('competencia') or '')[:7] == competence
    chosen = next(((index, part) for index, part in parts if matches(part)), None)
    if not chosen: raise HTTPException(404, 'Não há parcela nesse vencimento.')
    row_id, part = chosen
    source_part = original['installments'][row_id - 1] if source == 'installment' else original
    if part.get('payment_link') or part.get('future_excluded') or not unpaid(db, source, original, source_part):
        raise HTTPException(409, 'Esta cobrança já tem pagamento ou não pode ser vinculada.')
    return original, current, row_id, part

def link_imported_payment(db, source, identity, body: ImportedPaymentLink):
    """Tie one statement movement to one imported charge. The imported plan stays unchanged."""
    request_digest = digest({'payment': True, 'source': source, 'identity': identity, 'body': body.model_dump(mode='json')})
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            previous = writing.future_commitment_commands.find_one({'_id': str(body.request_id)})
            if previous:
                if previous.get('request_digest') != request_digest: raise HTTPException(409, 'Identificador já usado para outra alteração.')
                return {**previous['result'], 'duplicate_request': True}
            original, current, row_id, part = _charge_for_link(writing, source, identity, body.due_date, body.competence)
            movement, link_key, source_id, source_kind = _movement_for_link(writing, body)
            if not movement: raise HTTPException(404, 'Movimentação não encontrada.')
            require_source_token(movement, body.expected_source_token, 'A movimentação mudou. Confira os dados antes de vincular.')
            if not eligible_payment(movement, bool(body.provisional_id)) or not movement.get('data_evento'):
                raise HTTPException(422, 'Selecione uma despesa realizada, não um pagamento de fatura ou transferência.')
            if writing.commitments.find_one({f'linked_transactions.{link_key}': source_id}) or overlay_link_names(writing, [source_id] if link_key == 'transaction_id' else [], [source_id] if link_key == 'provisional_id' else []):
                raise HTTPException(409, 'Esta movimentação já foi vinculada a um compromisso.')
            key = f'{source}:{identity}'
            stored = writing.future_schedule_overrides.find_one({'_id': key}) or {}
            links = list(stored.get('payment_links') or [])
            stamp = body.due_date.isoformat() if body.due_date else body.competence
            if any(str(link.get('due_date') or '')[:10] == stamp or link.get(link_key) == source_id for link in links):
                raise HTTPException(409, 'Esta cobrança já está vinculada.')
            paid = local_date(movement['data_evento'])
            expected = amount(part.get('valor') if source == 'installment' else part.get('saldo_aberto'))
            link = {link_key: source_id, 'source_kind': source_kind, 'due_date': stamp, 'row_id': row_id,
                'paid_date': paid.isoformat(), 'expected_amount': str(expected or ''), 'actual_amount': str(abs(amount(movement.get('valor')))),
                'original_description': movement.get('descricao_original') or movement.get('merchant_raw') or '',
                'name': str(current.get('descricao') or current.get('tipo') or 'Compromisso')[:160], 'linked_at': datetime.now(timezone.utc).isoformat()}
            links.append(link)
            now = datetime.now(timezone.utc)
            revision = int(stored.get('revision') or 0) + 1
            replacement = {'payment_links': links, 'revision': revision, 'updated_at': now}
            if stored.get('_id'):
                changed = writing.future_schedule_overrides.update_one({'_id': key, 'revision': stored.get('revision', 0)}, {'$set': replacement})
                if changed is not None and getattr(changed, 'matched_count', 1) == 0: raise HTTPException(409, 'O compromisso mudou. Reabra a conciliação.')
            else:
                writing.future_schedule_overrides.insert_one({'_id': key, 'rows': {}, **replacement})
            profile = stored.get('profile') if isinstance(stored.get('profile'), dict) else {}
            if profile:
                from app.commitment_profile import paint_movement
                paint_movement(writing, movement, profile)
            result = {'persisted': True, 'source': source, 'identity': identity, 'revision': revision, 'payment': _public_link(link), 'duplicate_request': False}
            writing.audit_log.insert_one({'event': 'imported_payment_linked', 'source': source, 'identity': identity, link_key: source_id,
                'due_date': stamp, 'created_at': now})
            writing.future_commitment_commands.insert_one({'_id': str(body.request_id), 'request_digest': request_digest, 'result': result, 'created_at': now})
            return result
    except DuplicateKeyError as exc: raise HTTPException(409, 'Outra alteração foi concluída ao mesmo tempo. Reabra e confira novamente.') from exc

def unlink_imported_payment(db, source, identity, body: ImportedPaymentUnlink):
    request_digest = digest({'payment_unlink': True, 'source': source, 'identity': identity, 'body': body.model_dump(mode='json')})
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            previous = writing.future_commitment_commands.find_one({'_id': str(body.request_id)})
            if previous:
                if previous.get('request_digest') != request_digest: raise HTTPException(409, 'Identificador já usado para outra alteração.')
                return {**previous['result'], 'duplicate_request': True}
            if source == 'managed': raise HTTPException(422, 'O cadastro gerenciado usa o desfazer do próprio compromisso.')
            if bool(body.transaction_id) == bool(body.provisional_id):
                raise HTTPException(422, 'Informe o lançamento vinculado.')
            load(writing, source, identity)
            key = f'{source}:{identity}'
            stored = writing.future_schedule_overrides.find_one({'_id': key})
            if not stored or int(stored.get('revision') or 0) != body.expected_revision:
                raise HTTPException(409, 'O compromisso mudou. Reabra antes de desvincular.')
            link_key = 'transaction_id' if body.transaction_id else 'provisional_id'
            source_id = body.transaction_id or body.provisional_id
            links = [link for link in (stored.get('payment_links') or []) if link.get(link_key) != source_id]
            if len(links) == len(stored.get('payment_links') or []): raise HTTPException(404, 'Este vínculo não está nesta cobrança.')
            now = datetime.now(timezone.utc)
            revision = int(stored.get('revision') or 0) + 1
            changed = writing.future_schedule_overrides.update_one({'_id': key, 'revision': stored.get('revision', 0)},
                {'$set': {'payment_links': links, 'revision': revision, 'updated_at': now}})
            if changed is not None and getattr(changed, 'matched_count', 1) == 0: raise HTTPException(409, 'O compromisso mudou. Reabra antes de desvincular.')
            result = {'persisted': True, 'source': source, 'identity': identity, 'revision': revision, 'duplicate_request': False}
            writing.audit_log.insert_one({'event': 'imported_payment_unlinked', 'source': source, 'identity': identity, link_key: source_id, 'created_at': now})
            writing.future_commitment_commands.insert_one({'_id': str(body.request_id), 'request_digest': request_digest, 'result': result, 'created_at': now})
            return result
    except DuplicateKeyError as exc: raise HTTPException(409, 'Outra alteração foi concluída ao mesmo tempo. Reabra e confira novamente.') from exc

@router.post('/{source}/{identity}/payment')
def payment(source: Source, identity: str, body: ImportedPaymentLink, db=Depends(database)):
    return link_imported_payment(db, source, identity, body)

@router.post('/{source}/{identity}/payment-unlink')
def payment_unlink(source: Source, identity: str, body: ImportedPaymentUnlink, db=Depends(database)):
    return unlink_imported_payment(db, source, identity, body)

@router.put('/{source}/{identity}')
def update(source: Source, identity: str, body: FutureWrite, db=Depends(database)):
    return write(db, source, identity, body)

@router.delete('/{source}/{identity}')
def delete(source: Source, identity: str, body: FutureDelete, db=Depends(database)):
    return write(db, source, identity, body, True)

@router.post('/{source}/{identity}/undo')
def undo(source: Source, identity: str, body: FutureUndo, db=Depends(database)):
    return undo_future(db, source, identity, body)

@router.patch('/{source}/{identity}/label')
def update_label(source: Source, identity: str, body: FutureLabelWrite, db=Depends(database)):
    if source != 'installment': raise HTTPException(422, 'O nome editável está disponível para parcelamentos importados.')
    request_digest = digest({'source': source, 'identity': identity, 'label': body.model_dump(mode='json')})
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            previous = writing.future_commitment_commands.find_one({'_id': str(body.request_id)})
            if previous:
                if previous['request_digest'] != request_digest: raise HTTPException(409, 'Identificador já usado para outra alteração.')
                return {**previous['result'], 'duplicate_request': True}
            reviewed = view(writing, source, identity)
            if reviewed['preview_token'] != body.preview_token: raise HTTPException(409, 'O parcelamento mudou. Reabra e confira novamente.')
            original = load(writing, source, identity)
            key = f'{source}:{identity}'
            stored = writing.future_schedule_overrides.find_one({'_id': key})
            now = datetime.now(timezone.utc)
            name = body.name.strip()
            replacement = {**(stored or {}), 'label': name, 'revision': (stored or {}).get('revision', 0) + 1, 'updated_at': now}
            replacement.pop('_id', None)
            if stored:
                changed = writing.future_schedule_overrides.update_one({'_id': key, 'revision': stored['revision']}, {'$set': replacement})
                if changed is not None and changed.matched_count == 0: raise HTTPException(409, 'O parcelamento mudou. Reabra e confira novamente.')
            else:
                writing.future_schedule_overrides.insert_one({'_id': key, **replacement})
            result = {'persisted': True, 'source': source, 'identity': identity, 'name': name}
            writing.audit_log.insert_one({'event': 'future_commitment_label_updated', 'source': source,
                'identity': identity, 'original': original, 'before_view': reviewed, 'after_name': name, 'created_at': now})
            writing.future_commitment_commands.insert_one({'_id': str(body.request_id), 'request_digest': request_digest,
                'result': result, 'created_at': now})
            return result
    except DuplicateKeyError as exc: raise HTTPException(409, 'Outra alteração foi concluída ao mesmo tempo. Reabra e confira novamente.') from exc
