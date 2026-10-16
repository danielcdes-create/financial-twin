"""One audited write for commitment metadata and scoped amount or due date.

Imported plans and obligations stay as imported. The overlay and the linked
statement rows change together. Amount and date never fan out beyond the
chosen open charges.
"""
import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from app.auth import require_session
from pymongo.errors import DuplicateKeyError

from app.atomic import mongo_transaction, session_database
from app.commitments import as_date
from app.economic_policy import amount
from app import future_commitments
from app.future_commitments import (
    FutureDelete, digest, editable, future_obligations_view, load, obligation_series_key,
    unpaid, view, write,
)

Nature = Literal['COMPRA', 'IMPOSTO_TRIBUTO', 'TARIFA_ENCARGO', 'ESTORNO', 'RENDIMENTO', 'TRANSFERENCIA']
Pay = Literal['cartao', 'debito', 'pix', 'boleto', 'paypal', 'outro']
FrequencyName = Literal['semanal', 'mensal', 'trimestral', 'semestral', 'anual']

_CLEARABLE_PROFILE_FIELDS = (
    'display_name', 'natureza', 'categoria', 'subcategoria',
    'plataforma', 'metodo_pagamento', 'payment_method',
    'account_id', 'card_id', 'card_label',
)


class ProfileWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    source: Literal['managed', 'installment', 'obligation']
    identity: str = Field(min_length=1, max_length=200)
    expected_revision: int | None = Field(default=None, ge=0)
    display_name: str | None = Field(default=None, max_length=160)
    natureza: Nature | None = None
    categoria: str | None = Field(default=None, max_length=120)
    subcategoria: str | None = Field(default=None, max_length=120)
    plataforma: str | None = Field(default=None, max_length=160)
    metodo_pagamento: str | None = Field(default=None, max_length=100)
    payment_method: Pay | None = None
    account_id: str | None = Field(default=None, max_length=80)
    card_id: str | None = Field(default=None, max_length=80)
    card_label: str | None = Field(default=None, max_length=160)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    due_date: date | None = None
    frequency: FrequencyName | None = None
    scope: Literal['next', 'future'] | None = None


class StatementProfileWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    transaction_id: str = Field(min_length=1, max_length=80)
    vendedor: str | None = Field(default=None, max_length=160)
    natureza: Nature | None = None
    categoria: str | None = Field(default=None, max_length=120)
    subcategoria: str | None = Field(default=None, max_length=120)
    plataforma: str | None = Field(default=None, max_length=160)
    metodo_pagamento: str | None = Field(default=None, max_length=100)


def _blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _provided_fields(body) -> set[str]:
    return set(getattr(body, 'model_fields_set', getattr(body, '__fields_set__', set())))


def _cleared_profile_fields(body: ProfileWrite) -> set[str]:
    provided = _provided_fields(body)
    cleared = {field for field in _CLEARABLE_PROFILE_FIELDS if field in provided and _blank(getattr(body, field, None))}
    if 'card_id' in cleared and 'card_label' not in provided and _blank(body.card_label):
        cleared.add('card_label')
    return cleared


def _same(left, right) -> bool:
    return str(left) == str(right)


def _tx(db, identity):
    if not identity:
        return None
    for document in db.transactions.find({}):
        if _same(document.get('_id'), identity):
            return document
    return None


def _visible_card(db, profile: dict) -> dict:
    """A typed card name is the label. An id is resolved to that card's name."""
    raw = profile.get('card_id')
    label = profile.get('card_label')
    if isinstance(raw, str) and raw.strip() and not ObjectId.is_valid(raw.strip()):
        return {**profile, 'card_id': None, 'card_label': label or raw.strip()}
    if raw and not label:
        from app.installment_labels import card_labels
        found = card_labels(db, [raw]).get(str(raw))
        if found:
            return {**profile, 'card_label': found}
    return profile


def _paint(document, profile: dict, cleared: set[str] | tuple[str, ...] = ()) -> tuple[dict, dict]:
    """Metadata only. Description, amount and event date stay unless the caller sets them."""
    changes = {}
    unsets = {}
    cleared_set = set(cleared or ())
    mapping = {
        'display_name': 'vendedor',
        'natureza': 'natureza',
        'categoria': 'categoria',
        'subcategoria': 'subcategoria',
        'plataforma': 'plataforma',
        'metodo_pagamento': 'metodo_pagamento',
        'account_id': 'account_id',
        'card_id': 'card_id',
        'card_label': 'card_label',
    }
    previous_category = document.get('categoria')
    for source, target in mapping.items():
        value = profile.get(source)
        if not _blank(value):
            text = value.strip() if isinstance(value, str) else value
            if document.get(target) != text:
                changes[target] = text
        elif source in cleared_set and source not in {'display_name', 'account_id'}:
            if source == 'subcategoria':
                if document.get('subcategoria') not in (None, ''):
                    changes['subcategoria'] = ''
            elif document.get(target) is not None:
                unsets[target] = ''
    if changes.get('categoria') and changes['categoria'] != previous_category and _blank(profile.get('subcategoria')):
        if document.get('subcategoria') not in (None, ''):
            changes['subcategoria'] = ''
    if changes.get('vendedor'):
        changes['merchant_name'] = changes['vendedor']
    return changes, unsets


def paint_movement(db, document, profile: dict, cleared: set[str] | tuple[str, ...] = ()):
    if not document:
        return {}
    changes, unsets = _paint(document, profile, cleared)
    if not changes and not unsets:
        return {}
    changes['updated_at'] = datetime.now(timezone.utc)
    update = {'$set': changes}
    if unsets:
        update['$unset'] = unsets
    db.transactions.update_one({'_id': document['_id']}, update)
    return {
        **{key: value for key, value in changes.items() if key != 'updated_at'},
        **{key: None for key in unsets},
    }


def _signed(current, new_amount: Decimal) -> Decimal:
    value = amount(current)
    magnitude = abs(new_amount)
    if value is not None and value < 0:
        return -magnitude
    return magnitude


def _stamp(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 15, 0, tzinfo=timezone.utc)


def _store_override(db, key: str, profile: dict, rows: dict | None, label: str | None, cleared: set[str] | tuple[str, ...] = ()):
    stored = db.future_schedule_overrides.find_one({'_id': key}) or {}
    now = datetime.now(timezone.utc)
    cleared_set = set(cleared or ())
    merged_profile = dict(stored.get('profile') or {})
    if 'categoria' in profile and not _blank(profile.get('categoria')) and profile.get('categoria') != merged_profile.get('categoria') and _blank(profile.get('subcategoria')):
        merged_profile.pop('subcategoria', None)
    for field in cleared_set:
        merged_profile.pop(field, None)
    for k, v in profile.items():
        if not _blank(v):
            merged_profile[k] = v
    replacement = {
        'profile': merged_profile,
        'rows': stored.get('rows') or {},
        'payment_links': stored.get('payment_links') or [],
        'revision': int(stored.get('revision') or 0) + 1,
        'updated_at': now,
    }
    if rows is not None:
        replacement['rows'] = rows
    unsets = {}
    if label:
        replacement['label'] = label
    elif 'display_name' in cleared_set:
        if stored.get('label') is not None:
            unsets['label'] = ''
    elif stored.get('label'):
        replacement['label'] = stored['label']
    if stored:
        update = {'$set': replacement}
        if unsets:
            update['$unset'] = unsets
        db.future_schedule_overrides.update_one({'_id': key}, update)
    else:
        db.future_schedule_overrides.insert_one({'_id': key, **replacement})
    return replacement['revision']


def _patch_row(db, source, identity, row_id, new_amount: Decimal | None, due: date | None, cancel=False):
    preview = view(db, source, identity)
    row = next((item for item in preview['rows'] if item['row_id'] == row_id), None)
    if not row or not row['editable']:
        return False
    original = load(db, source, identity)
    parts = original.get('installments', []) if source == 'installment' else [original]
    key = f'{source}:{identity}'
    stored = db.future_schedule_overrides.find_one({'_id': key}) or {}
    patches = dict(stored.get('rows') or {})
    current_amount = Decimal(str(row['amount']))
    patches[str(row_id)] = {
        'original_token': digest(parts[row_id - 1]),
        'amount': str(new_amount if new_amount is not None else current_amount),
        'due_date': due.isoformat() if due else row['due_date'],
        'cancelled': cancel,
    }
    _store_override(db, key, stored.get('profile') or {}, patches, stored.get('label'))
    return True


def _statement_ids(part) -> list:
    found = []
    for key in ('open_statement_id', 'transaction_id'):
        if part.get(key):
            found.append(part[key])
    link = part.get('payment_link') or {}
    if link.get('transaction_id'):
        found.append(link['transaction_id'])
    return found


def _apply_amount_to_statement(db, identities, new_amount: Decimal | None, due: date | None):
    touched = []
    for identity in identities:
        document = _tx(db, identity)
        if not document:
            continue
        changes = {}
        if new_amount is not None:
            changes['valor'] = Decimal128(_signed(document.get('valor'), new_amount))
        if due is not None:
            changes['data_evento'] = _stamp(due)
        if changes:
            changes['updated_at'] = datetime.now(timezone.utc)
            db.transactions.update_one({'_id': document['_id']}, {'$set': changes})
            touched.append(str(document['_id']))
    return touched


def _open_installment_rows(db, identity):
    return [row for row in view(db, 'installment', identity)['rows'] if row['editable']]


def _series_rows(db, identity):
    documents = [row for row in db.obligations.find({}) if obligation_series_key(row) == identity]
    if not documents and ObjectId.is_valid(identity):
        one = next((row for row in db.obligations.find({}) if _same(row.get('_id'), identity)), None)
        if one:
            documents = [row for row in db.obligations.find({}) if obligation_series_key(row) == obligation_series_key(one)]
    chosen = []
    for document in documents:
        current = future_commitments.effective_document(db, 'obligation', document)
        if current.get('future_excluded') or current.get('status') == 'cancelada':
            continue
        if not unpaid(db, 'obligation', document, document) or not editable(db, 'obligation', document, document):
            continue
        chosen.append(document)
    chosen.sort(key=lambda row: (str(row.get('data_vencimento') or ''), str(row.get('_id'))))
    return chosen


def _profile_dict(body: ProfileWrite) -> dict:
    return {
        'display_name': None if _blank(body.display_name) else body.display_name.strip(),
        'natureza': body.natureza,
        'categoria': None if _blank(body.categoria) else body.categoria.strip(),
        'subcategoria': None if _blank(body.subcategoria) else body.subcategoria.strip(),
        'plataforma': None if _blank(body.plataforma) else body.plataforma.strip(),
        'metodo_pagamento': None if _blank(body.metodo_pagamento) else body.metodo_pagamento.strip(),
        'payment_method': body.payment_method,
        'account_id': None if _blank(body.account_id) else body.account_id.strip(),
        'card_id': None if _blank(body.card_id) else body.card_id.strip(),
        'card_label': None if _blank(body.card_label) else body.card_label.strip(),
    }


def _linked_ids_for(db, source, identity) -> list:
    found = []
    if source == 'managed':
        document = db.commitments.find_one({'commitment_id': identity}) or {}
        if document.get('purchase_transaction_id'):
            found.append(document['purchase_transaction_id'])
        for link in document.get('linked_transactions') or []:
            if link.get('transaction_id'):
                found.append(link['transaction_id'])
        return found
    if source == 'installment':
        plan = load(db, 'installment', identity)
        for part in plan.get('installments') or []:
            found.extend(_statement_ids(part))
        stored = db.future_schedule_overrides.find_one({'_id': f'installment:{identity}'}) or {}
        for link in stored.get('payment_links') or []:
            if link.get('transaction_id'):
                found.append(link['transaction_id'])
        return found
    for document in _series_rows(db, identity) + [row for row in db.obligations.find({}) if obligation_series_key(row) == identity]:
        found.extend(_statement_ids(document))
        stored = db.future_schedule_overrides.find_one({'_id': f"obligation:{document['_id']}"}) or {}
        for link in stored.get('payment_links') or []:
            if link.get('transaction_id'):
                found.append(link['transaction_id'])
    return found


def _targets_for_transaction(db, transaction_id: str):
    targets = []
    for commitment in db.commitments.find({}):
        if _same(commitment.get('purchase_transaction_id'), transaction_id):
            targets.append(('managed', commitment['commitment_id']))
        for link in commitment.get('linked_transactions') or []:
            if _same(link.get('transaction_id'), transaction_id):
                targets.append(('managed', commitment['commitment_id']))
    for plan in db.installment_plans.find({}):
        for part in plan.get('installments') or []:
            if any(_same(identity, transaction_id) for identity in _statement_ids(part)):
                targets.append(('installment', str(plan['_id'])))
    for document in db.obligations.find({}):
        if any(_same(identity, transaction_id) for identity in _statement_ids(document)):
            targets.append(('obligation', obligation_series_key(document)))
    for override in db.future_schedule_overrides.find({}):
        for link in override.get('payment_links') or []:
            if _same(link.get('transaction_id'), transaction_id):
                source, _, identity = str(override['_id']).partition(':')
                if source == 'obligation':
                    original = next((row for row in db.obligations.find({}) if _same(row.get('_id'), identity)), None)
                    targets.append(('obligation', obligation_series_key(original) if original else identity))
                elif source in ('installment', 'managed'):
                    targets.append((source, identity))
    unique = []
    for item in targets:
        if item not in unique:
            unique.append(item)
    return unique


def _require_revision(db, body: ProfileWrite):
    if body.expected_revision is None:
        return
    if body.source == 'managed':
        document = db.commitments.find_one({'commitment_id': body.identity}) or {}
        if int(document.get('revision') or 1) != body.expected_revision:
            raise HTTPException(409, 'O compromisso mudou. Reabra antes de salvar.')
        return
    if body.source == 'installment':
        stored = db.future_schedule_overrides.find_one({'_id': f'installment:{body.identity}'}) or {}
        if int(stored.get('revision') or 0) != body.expected_revision:
            raise HTTPException(409, 'O compromisso mudou. Reabra antes de salvar.')
        return
    documents = [row for row in db.obligations.find({}) if obligation_series_key(row) == body.identity]
    revisions = [int((db.future_schedule_overrides.find_one({'_id': f"obligation:{row['_id']}"}) or {}).get('revision') or 0) for row in documents]
    if revisions and body.expected_revision not in revisions:
        raise HTTPException(409, 'O compromisso mudou. Reabra antes de salvar.')


def apply_commitment_profile(db, body: ProfileWrite):
    """Save one profile. Metadata reaches every linked statement row. Amount and date stay inside scope."""
    request_digest = digest({'profile': True, 'body': body.model_dump(mode='json')})
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            previous = writing.commitment_profile_commands.find_one({'_id': str(body.request_id)})
            if previous:
                if previous.get('request_digest') != request_digest:
                    raise HTTPException(409, 'Identificador já usado para outra alteração.')
                return {**previous['result'], 'duplicate_request': True}
            if body.source == 'installment':
                load(writing, 'installment', body.identity)
            elif body.source == 'managed':
                if not writing.commitments.find_one({'commitment_id': body.identity}):
                    raise HTTPException(404, 'Compromisso não encontrado.')
            elif not _series_rows(writing, body.identity) and not any(obligation_series_key(row) == body.identity for row in writing.obligations.find({})):
                raise HTTPException(404, 'Compromisso não encontrado.')
            cleared = _cleared_profile_fields(body)
            profile = _visible_card(writing, _profile_dict(body))
            if (body.amount is not None or body.due_date is not None) and body.source != 'managed' and body.scope is None:
                raise HTTPException(422, 'Escolha a próxima parcela em aberto ou todas as futuras.')
            _require_revision(writing, body)
            linked = []
            seen = set()
            for identity in _linked_ids_for(writing, body.source, body.identity):
                if str(identity) in seen:
                    continue
                seen.add(str(identity))
                document = _tx(writing, identity)
                if document:
                    paint_movement(writing, document, profile, cleared)
                    linked.append(str(document['_id']))
            amount_ids = []
            if body.source == 'managed':
                document = writing.commitments.find_one({'commitment_id': body.identity})
                sets = {'updated_at': datetime.now(timezone.utc)}
                unsets = {}
                previous_category = document.get('category') or document.get('categoria')
                if profile.get('display_name'):
                    if not document.get('original_description'):
                        sets['original_description'] = document.get('name') or ''
                    sets['name'] = profile['display_name']
                    sets['display_name'] = profile['display_name']
                elif 'display_name' in cleared and document.get('display_name'):
                    if document.get('original_description'):
                        sets['name'] = document['original_description']
                    unsets['display_name'] = ''
                for key, field in (('categoria', 'category'), ('plataforma', 'billing_platform'), ('payment_method', 'payment_method'), ('account_id', 'account_id'), ('card_id', 'card_id')):
                    if profile.get(key):
                        sets[field] = profile[key]
                    elif key in cleared and key != 'payment_method':
                        sets[field] = None
                        if key == 'categoria' and 'categoria' in document:
                            unsets['categoria'] = ''
                        if key == 'plataforma' and 'plataforma' in document:
                            unsets['plataforma'] = ''
                if profile.get('card_label'):
                    sets['card_label'] = profile['card_label']
                elif 'card_label' in cleared and 'card_label' in document:
                    unsets['card_label'] = ''
                for key in ('natureza', 'subcategoria', 'metodo_pagamento'):
                    if profile.get(key):
                        sets[key] = profile[key]
                    elif key in cleared:
                        unsets[key] = ''
                if profile.get('categoria') and profile['categoria'] != previous_category and _blank(profile.get('subcategoria')):
                    unsets['subcategoria'] = ''
                    sets.pop('subcategoria', None)
                if body.frequency:
                    sets['frequency'] = body.frequency
                if body.amount is not None:
                    sets['amount'] = Decimal128(body.amount)
                if body.due_date is not None:
                    sets['next_due_date'] = body.due_date.isoformat() if isinstance(document.get('next_due_date'), str) else _stamp(body.due_date)
                    if not document.get('next_due_date') or as_date(document['next_due_date']) != body.due_date:
                        sets['schedule_anchor_day'] = body.due_date.day
                        sets['schedule_anchor_end'] = body.due_date.day == calendar.monthrange(body.due_date.year, body.due_date.month)[1]
                update_doc = {'$set': sets, '$inc': {'revision': 1}}
                if unsets:
                    update_doc['$unset'] = unsets
                writing.commitments.update_one({'_id': document['_id']}, update_doc)
                if body.scope and document.get('kind') == 'parcelamento' and (body.amount is not None or body.due_date is not None):
                    opens = []
                    for link in document.get('linked_transactions') or []:
                        due = str(link.get('due_date') or '')[:10]
                        if due and due >= future_commitments.today().isoformat() and link.get('transaction_id'):
                            opens.append(link)
                    opens.sort(key=lambda link: link.get('due_date') or '')
                    chosen = opens if body.scope == 'future' else opens[:1]
                    amount_ids = _apply_amount_to_statement(writing, [link['transaction_id'] for link in chosen], body.amount, body.due_date)
            elif body.source == 'installment':
                label = profile.get('display_name')
                _store_override(writing, f"installment:{body.identity}", profile, None, label, cleared)
                if body.scope and (body.amount is not None or body.due_date is not None):
                    rows = _open_installment_rows(writing, body.identity)
                    chosen = rows if body.scope == 'future' else rows[:1]
                    plan = load(writing, 'installment', body.identity)
                    for row in chosen:
                        _patch_row(writing, 'installment', body.identity, row['row_id'], body.amount, body.due_date)
                        part = plan['installments'][row['row_id'] - 1]
                        amount_ids.extend(_apply_amount_to_statement(writing, _statement_ids(part), body.amount, body.due_date))
            else:
                documents = [row for row in writing.obligations.find({}) if obligation_series_key(row) == body.identity]
                opens = _series_rows(writing, body.identity)
                for document in documents:
                    _store_override(writing, f"obligation:{document['_id']}", profile, None, profile.get('display_name'), cleared)
                if body.scope and (body.amount is not None or body.due_date is not None):
                    chosen = opens if body.scope == 'future' else opens[:1]
                    for document in chosen:
                        _patch_row(writing, 'obligation', str(document['_id']), 1, body.amount, body.due_date)
                        amount_ids.extend(_apply_amount_to_statement(writing, _statement_ids(document), body.amount, body.due_date))
            result = {'persisted': True, 'source': body.source, 'identity': body.identity, 'linked': linked, 'amount_rows': amount_ids, 'duplicate_request': False}
            writing.audit_log.insert_one({'event': 'commitment_profile_synced', 'source': body.source, 'identity': body.identity,
                'linked': linked, 'amount_rows': amount_ids, 'created_at': datetime.now(timezone.utc)})
            writing.commitment_profile_commands.insert_one({'_id': str(body.request_id), 'request_digest': request_digest, 'result': result, 'created_at': datetime.now(timezone.utc)})
            return result
    except DuplicateKeyError as exc:
        raise HTTPException(409, 'Outra alteração foi concluída ao mesmo tempo. Reabra e confira novamente.') from exc


router = APIRouter(prefix='/v1/commitment-profile', dependencies=[Depends(require_session)])


def _database(request: Request):
    if request.app.state.db is None:
        raise HTTPException(503, 'Banco indisponível.')
    return request.app.state.db


@router.put('')
def save_profile(body: ProfileWrite, db=Depends(_database)):
    return apply_commitment_profile(db, body)


@router.post('/from-statement')
def save_profile_from_statement(body: StatementProfileWrite, db=Depends(_database)):
    return sync_statement_metadata(db, body)


def sync_statement_metadata(db, body: StatementProfileWrite):
    """Metadata typed on one statement row. Amount and date of that row stay put."""
    targets = _targets_for_transaction(db, body.transaction_id)
    if not targets:
        return {'persisted': True, 'updated': 0, 'linked': []}
    provided = _provided_fields(body)
    fields = {}
    for key in ('vendedor', 'natureza', 'categoria', 'subcategoria', 'plataforma', 'metodo_pagamento'):
        if key in provided:
            fields['display_name' if key == 'vendedor' else key] = getattr(body, key)
    profile = ProfileWrite(
        request_id=body.request_id,
        source=targets[0][0],
        identity=targets[0][1],
        **fields,
        scope=None,
        amount=None,
        due_date=None,
    )
    saved = apply_commitment_profile(db, profile)
    return {**saved, 'updated': len(saved.get('linked') or [])}


def close_imported_open(db, source: Literal['installment', 'obligation'], identity: str):
    """Cut open future charges. Paid statement rows and paid schedule rows stay."""
    if source == 'installment':
        token = view(db, 'installment', identity)['preview_token']
        return write(db, 'installment', identity, FutureDelete(request_id=uuid4(), preview_token=token), True)
    closed = []
    for document in _series_rows(db, identity):
        item_id = str(document['_id'])
        preview = view(db, 'obligation', item_id)
        if not preview['editable_count']:
            continue
        closed.append(write(db, 'obligation', item_id, FutureDelete(request_id=uuid4(), preview_token=preview['preview_token']), True))
    remaining = future_obligations_view(db)
    return {'persisted': True, 'source': source, 'identity': identity, 'closed': len(closed), 'still_open': [item['id'] for item in remaining['items'] if item.get('series_key') == identity]}
