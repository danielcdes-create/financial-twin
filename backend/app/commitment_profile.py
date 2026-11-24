"""One audited write for commitment metadata and scoped amount or due date.

Imported plans and obligations stay as imported. The overlay and the linked
statement rows change together. Amount and date never fan out beyond the
chosen open charges.
"""
import calendar
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from app.account_profiles import legacy_accounts_by_profile
from app.auth import require_session
from pymongo.errors import DuplicateKeyError

from app.atomic import mongo_transaction, session_database
from app.movement_insert_lock import lock_movement_insert
from app.commitments import advance, as_date, schedule
from app.economic_policy import amount
from app import future_commitments
from app.future_commitments import (
    FutureDelete, digest, editable, future_obligations_view, load, obligation_series_key,
    unpaid, view, write,
)
from app.platforms import platform_identity

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
    if hasattr(body, 'model_fields_set'):
        return set(body.model_fields_set)
    return set(getattr(body, '__fields_set__', set()))


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
    text = str(identity)
    if ObjectId.is_valid(text):
        found = db.transactions.find_one({'_id': ObjectId(text)})
        if found is not None:
            return found
    found = db.transactions.find_one({'_id': identity})
    if found is not None:
        return found
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


_TX_PAYMENT_METHODS = frozenset({
    'cartao_credito', 'cartao_debito', 'pix', 'conta_corrente',
    'debito_conta', 'transferencia', 'dinheiro', 'boleto',
    'sem_parar', 'outro', 'desconhecido',
})
_PAYMENT_METHOD_SHORTHAND = {
    'cartao': 'cartao_credito',
    'credito': 'cartao_credito',
    'debito': 'cartao_debito',
    'paypal': 'outro',
}
_TX_NATURES = frozenset({
    'COMPRA', 'IMPOSTO_TRIBUTO', 'TARIFA_ENCARGO',
    'ESTORNO', 'RENDIMENTO', 'TRANSFERENCIA',
})
_FORBIDDEN_TX_FIELDS = (
    'vendedor', 'merchant_name', 'plataforma',
    'card_id', 'card_label', 'commitment_label',
)


def _norm_merchant_key(value: str) -> str:
    plain = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii').casefold()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', plain)).strip()


def _resolve_or_create_merchant(db, seller_name: str, now: datetime) -> dict:
    clean = seller_name.strip()
    exact = db.merchants.find_one({'nome_padrao': clean})
    if exact:
        return exact
    key = _norm_merchant_key(clean)
    for mdoc in db.merchants.find({}):
        if _norm_merchant_key(mdoc.get('nome_padrao') or '') == key:
            return mdoc
    norm_alias = re.sub(r'\s+', ' ', re.sub(r'[^A-Z0-9 ]', ' ', clean.upper())).strip() or clean.upper()
    merchant = {
        '_id': ObjectId(),
        'external_key': f'merchant-user-{uuid4()}',
        'schema_version': 2,
        'created_at': now,
        'updated_at': now,
        'tipo_entidade': 'marca',
        'nome_padrao': clean,
        'aliases': [
            {
                'valor': clean,
                'normalizado': norm_alias,
                'status': 'confirmado',
                'source_record_ids': [],
            }
        ],
    }
    db.merchants.insert_one(merchant)
    return merchant


def _resolve_or_create_platform_merchant(db, platform_name: str, now: datetime) -> dict:
    ident = platform_identity(platform_name)
    canonical = ident[1] if ident else platform_name.strip()
    found = db.merchants.find_one({'external_key': ident[0]}) if ident else None
    if not found:
        found = db.merchants.find_one({'nome_padrao': canonical})
    if not found:
        key = _norm_merchant_key(canonical)
        for mdoc in db.merchants.find({}):
            if _norm_merchant_key(mdoc.get('nome_padrao') or '') == key:
                found = mdoc
                break
    if found:
        return found
    norm_alias = re.sub(r'\s+', ' ', re.sub(r'[^A-Z0-9 ]', ' ', canonical.upper())).strip() or canonical.upper()
    platform = {
        '_id': ObjectId(),
        'external_key': ident[0] if ident else f'platform-user-{uuid4()}',
        'schema_version': 2,
        'created_at': now,
        'updated_at': now,
        'tipo_entidade': 'marca',
        'nome_padrao': canonical,
        'aliases': [
            {
                'valor': canonical,
                'normalizado': norm_alias,
                'status': 'confirmado',
                'source_record_ids': [],
            }
        ],
    }
    db.merchants.insert_one(platform)
    return platform


def _resolve_tx_account_oid(db, raw_account) -> ObjectId | None:
    if isinstance(raw_account, ObjectId):
        return raw_account
    if not isinstance(raw_account, str) or not raw_account.strip():
        return None
    text = raw_account.strip()
    if text.startswith('profile:'):
        prof_key = text.removeprefix('profile:').strip()
        leg = legacy_accounts_by_profile(db).get(prof_key)
        return leg['_id'] if isinstance(leg, dict) and isinstance(leg.get('_id'), ObjectId) else None
    if ObjectId.is_valid(text):
        return ObjectId(text)
    leg = legacy_accounts_by_profile(db).get(text)
    return leg['_id'] if isinstance(leg, dict) and isinstance(leg.get('_id'), ObjectId) else None


def _normalize_tx_payment_method(raw_pm) -> str | None:
    if _blank(raw_pm) or not isinstance(raw_pm, str):
        return None
    key = raw_pm.strip().lower()
    mapped = _PAYMENT_METHOD_SHORTHAND.get(key, key)
    return mapped if mapped in _TX_PAYMENT_METHODS else None


def _paint(
    db,
    document: dict,
    profile: dict,
    cleared: set[str] | tuple[str, ...] = (),
    *,
    now: datetime | None = None,
) -> tuple[dict, dict]:
    """Metadata only, strictly obeying canonical `transactions` $jsonSchema."""
    changes: dict = {}
    unsets: dict = {}
    cleared_set = set(cleared or ())
    stamp = now or datetime.now(timezone.utc)

    for legacy_key in _FORBIDDEN_TX_FIELDS:
        if legacy_key in document:
            unsets[legacy_key] = ''

    seller_val = profile.get('display_name') if not _blank(profile.get('display_name')) else profile.get('vendedor')
    if not _blank(seller_val):
        merchant = _resolve_or_create_merchant(db, str(seller_val).strip(), stamp)
        if document.get('merchant_id') != merchant['_id']:
            changes['merchant_id'] = merchant['_id']
    elif 'vendedor' in cleared_set and document.get('merchant_id') is not None:
        unsets['merchant_id'] = ''

    plat_val = profile.get('plataforma')
    if not _blank(plat_val):
        platform = _resolve_or_create_platform_merchant(db, str(plat_val).strip(), stamp)
        if document.get('platform_id') != platform['_id']:
            changes['platform_id'] = platform['_id']
    elif 'plataforma' in cleared_set and document.get('platform_id') is not None:
        unsets['platform_id'] = ''

    previous_category = document.get('categoria')
    if not _blank(profile.get('categoria')):
        cat = str(profile['categoria']).strip()
        if cat and document.get('categoria') != cat:
            changes['categoria'] = cat

    if not _blank(profile.get('subcategoria')):
        sub = str(profile['subcategoria']).strip()
        if document.get('subcategoria') != sub:
            changes['subcategoria'] = sub
    elif 'subcategoria' in cleared_set or (
        changes.get('categoria') and changes['categoria'] != previous_category and _blank(profile.get('subcategoria'))
    ):
        if document.get('subcategoria') not in (None, '') or 'subcategoria' in document:
            unsets['subcategoria'] = ''

    if not _blank(profile.get('natureza')):
        nat = str(profile['natureza']).strip()
        if nat in _TX_NATURES and document.get('natureza') != nat:
            changes['natureza'] = nat
    elif 'natureza' in cleared_set and document.get('natureza') is not None:
        unsets['natureza'] = ''

    raw_pm = profile.get('metodo_pagamento')
    if not _blank(raw_pm):
        norm_pm = _normalize_tx_payment_method(raw_pm)
        if norm_pm and document.get('metodo_pagamento') != norm_pm:
            changes['metodo_pagamento'] = norm_pm
    elif 'metodo_pagamento' in cleared_set and document.get('metodo_pagamento') is not None:
        unsets['metodo_pagamento'] = ''

    if not _blank(profile.get('account_id')):
        acc_oid = _resolve_tx_account_oid(db, profile.get('account_id'))
        if isinstance(acc_oid, ObjectId) and document.get('account_id') != acc_oid:
            changes['account_id'] = acc_oid

    return changes, unsets


def paint_movement(db, document, profile: dict, cleared: set[str] | tuple[str, ...] = ()):
    if not document or document.get('provisional_id'):
        return {}
    now = datetime.now(timezone.utc)
    changes, unsets = _paint(db, document, profile, cleared, now=now)
    if not changes and not unsets:
        return {}
    changes['updated_at'] = now
    update: dict = {'$set': changes}
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


def _ym_tuple(ym: str) -> tuple[int, int] | None:
    if len(ym) == 7 and ym[4] == '-' and ym[:4].isdigit() and ym[5:].isdigit():
        m = int(ym[5:])
        if 1 <= m <= 12:
            return int(ym[:4]), m
    return None


def _obligation_ym(document: dict) -> str:
    raw_due = document.get('data_vencimento') or document.get('due_date')
    if raw_due:
        try:
            return as_date(raw_due).strftime('%Y-%m')
        except Exception:
            pass
    return str(document.get('competencia') or '')[:7]


def _scoped_future_due(target_due: date | None, index: int, first_ym: str, row_ym: str) -> date | None:
    if target_due is None:
        return None
    if index == 0:
        return target_due
    start_pair = _ym_tuple(first_ym)
    row_pair = _ym_tuple(row_ym)
    if start_pair and row_pair:
        delta = (row_pair[0] * 12 + row_pair[1]) - (start_pair[0] * 12 + start_pair[1])
        steps = delta if delta > 0 else index
    else:
        steps = index
    return advance(target_due, 'mensal', steps)


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


def _open_statement_ids(part) -> list:
    if part.get('payment_link') or part.get('transaction_id') or part.get('bill_id'):
        return []
    if part.get('status') in {'paga', 'liquidada', 'cancelada'}:
        return []
    return [part['open_statement_id']] if part.get('open_statement_id') else []


def _is_settled_or_protected_statement(db, document) -> bool:
    tx_id = str(document.get('_id'))
    if (not isinstance(db, dict) or 'refund_claims' in db) and hasattr(db, 'refund_claims'):
        if db.refund_claims.find_one({'_id': f'ledger:{tx_id}'}):
            return True
    if (not isinstance(db, dict) or 'reconciliations' in db) and hasattr(db, 'reconciliations'):
        if db.reconciliations.find_one({'transaction_id': document.get('_id')}) or db.reconciliations.find_one({'transaction_id': tx_id}):
            return True
    for commitment in db.commitments.find({}):
        for link in commitment.get('linked_transactions') or []:
            if _same(link.get('transaction_id'), tx_id) and (link.get('paid_date') or link.get('settled_next')):
                return True
    return False


def _apply_amount_to_statement(db, identities, new_amount: Decimal | None, due: date | None):
    touched = []
    for identity in identities:
        document = _tx(db, identity)
        if not document or _is_settled_or_protected_statement(db, document):
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
            lock_movement_insert(writing)
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
                open_sched = schedule(document, 2, include_base=True) if document.get('status') == 'ativo' and document.get('kind') == 'parcelamento' else []
                single_next_override = bool(body.scope == 'next' and len(open_sched) > 1 and (body.amount is not None or body.due_date is not None))
                if single_next_override:
                    first_occ = open_sched[0]
                    base_due_key = first_occ.get('base_due_date') or first_occ['due_date']
                    overrides = dict(document.get('installment_overrides') or {}) if isinstance(document.get('installment_overrides'), dict) else {}
                    existing_patch = dict(overrides.get(base_due_key) or {}) if isinstance(overrides.get(base_due_key), dict) else {}
                    if body.amount is not None:
                        existing_patch['amount'] = str(body.amount)
                    if body.due_date is not None:
                        existing_patch['due_date'] = body.due_date.isoformat()
                    existing_patch.setdefault('cancelled', False)
                    overrides[base_due_key] = existing_patch
                    sets['installment_overrides'] = overrides
                else:
                    if body.amount is not None:
                        sets['amount'] = str(body.amount)
                    if body.due_date is not None:
                        sets['next_due_date'] = body.due_date.isoformat() if isinstance(document.get('next_due_date'), str) else _stamp(body.due_date)
                        sets['schedule_anchor_day'] = body.due_date.day
                        sets['schedule_anchor_end'] = body.due_date.day == 31
                update_doc = {'$set': sets, '$inc': {'revision': 1}}
                if unsets:
                    update_doc['$unset'] = unsets
                writing.commitments.update_one({'_id': document['_id']}, update_doc)
                if body.scope and document.get('kind') == 'parcelamento' and (body.amount is not None or body.due_date is not None):
                    opens = []
                    for link in document.get('linked_transactions') or []:
                        if link.get('paid_date') or link.get('settled_next'):
                            continue
                        due = str(link.get('due_date') or '')[:10]
                        if due and due >= future_commitments.today().isoformat() and link.get('transaction_id'):
                            opens.append(link)
                    opens.sort(key=lambda link: link.get('due_date') or '')
                    chosen = opens if body.scope == 'future' else opens[:1]
                    first_ym = str(chosen[0].get('due_date') or '')[:7] if chosen else ''
                    for idx, link in enumerate(chosen):
                        row_due = _scoped_future_due(body.due_date, idx, first_ym, str(link.get('due_date') or '')[:7])
                        amount_ids.extend(_apply_amount_to_statement(writing, [link['transaction_id']], body.amount, row_due))
            elif body.source == 'installment':
                label = profile.get('display_name')
                _store_override(writing, f"installment:{body.identity}", profile, None, label, cleared)
                if body.scope and (body.amount is not None or body.due_date is not None):
                    rows = _open_installment_rows(writing, body.identity)
                    chosen = rows if body.scope == 'future' else rows[:1]
                    plan = load(writing, 'installment', body.identity)
                    first_ym = str(chosen[0].get('due_date') or chosen[0].get('month') or '')[:7] if chosen else ''
                    for idx, row in enumerate(chosen):
                        row_ym = str(row.get('due_date') or row.get('month') or '')[:7]
                        row_due = _scoped_future_due(body.due_date, idx, first_ym, row_ym)
                        _patch_row(writing, 'installment', body.identity, row['row_id'], body.amount, row_due)
                        part = plan['installments'][row['row_id'] - 1]
                        amount_ids.extend(_apply_amount_to_statement(writing, _open_statement_ids(part), body.amount, row_due))
            else:
                documents = [row for row in writing.obligations.find({}) if obligation_series_key(row) == body.identity]
                opens = _series_rows(writing, body.identity)
                for document in documents:
                    _store_override(writing, f"obligation:{document['_id']}", profile, None, profile.get('display_name'), cleared)
                if body.scope and (body.amount is not None or body.due_date is not None):
                    chosen = opens if body.scope == 'future' else opens[:1]
                    first_ym = _obligation_ym(chosen[0]) if chosen else ''
                    for idx, document in enumerate(chosen):
                        row_due = _scoped_future_due(body.due_date, idx, first_ym, _obligation_ym(document))
                        _patch_row(writing, 'obligation', str(document['_id']), 1, body.amount, row_due)
                        amount_ids.extend(_apply_amount_to_statement(writing, _open_statement_ids(document), body.amount, row_due))
            result = {'persisted': True, 'request_id': str(body.request_id), 'source': body.source, 'identity': body.identity, 'linked': linked, 'amount_rows': amount_ids, 'duplicate_request': False}
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
        return {'persisted': True, 'request_id': str(body.request_id), 'updated': 0, 'linked': []}
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
