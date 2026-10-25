"""Two-way link between a parcelamento and the first posted installment.

The extrato row is that first installment on the purchase date, never the plan
total and never a later parcel. It is not a linked_transactions payment, so the
card bill stays open until it is reconciled. A hypothetical obrigação does not
write a row.
"""
from datetime import datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import re
import unicodedata
from uuid import uuid4, uuid5, NAMESPACE_URL

from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo.database import Database
from pydantic import ValidationError

from app.atomic import mongo_transaction, session_database
from app.commitments import COMMITMENT_CUTOFF, CommitmentIn, advance, as_date, stored_document
from app.financial_time import LOCAL_ZONE, local_date
from app.movement_insert_lock import lock_movement_insert
from app.taxonomy import infer_classification, taxonomy_overrides


def merchant_key(value: str) -> str:
    plain = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode('ascii').upper()
    return re.sub(r'\s+', ' ', re.sub(r'[^A-Z0-9 ]', ' ', plain)).strip()


def installment_count_of(parsed: dict) -> int | None:
    raw = (parsed or {}).get('installment_count')
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    if 2 <= count <= 48:
        return count
    return None


def statement_installment(item: dict) -> Decimal:
    """Amount that posts on the purchase date: the first installment, not the total."""
    return Decimal(str(item['amount'])).quantize(Decimal('0.01'))


def plan_total(item: dict) -> Decimal:
    return (statement_installment(item) * Decimal(int(item['installment_count']))).quantize(Decimal('0.01'))


def split_installment(total: Decimal, count: int) -> Decimal:
    return (total / Decimal(count)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def installment_detail(count: int) -> str:
    return f'Parcela 1 de {int(count)}'


def purchase_fingerprint(item: dict) -> str:
    raw = '|'.join([
        str(item.get('name') or '').strip(),
        as_date(item['purchase_date']).isoformat(),
        format(statement_installment(item), 'f'),
        str(int(item['installment_count'])),
        str(item.get('card_id') or ''),
        str(item.get('account_id') or ''),
        str(item.get('payment_method') or ''),
    ])
    return sha256(raw.encode()).hexdigest()


def matching_parcelamentos(db, merchant: str, count: int) -> list[dict]:
    key = merchant_key(merchant)
    found = []
    for item in db.commitments.find({'kind': 'parcelamento'}):
        if item.get('status') == 'cancelado' or item.get('future_deleted_at'):
            continue
        if int(item.get('installment_count') or 0) != count:
            continue
        if merchant_key(item.get('name') or '') != key:
            continue
        found.append(item)
    return found


def _payment_method(parsed: dict) -> str:
    return {
        'cartao_credito': 'cartao',
        'cartao': 'cartao',
        'cartao_debito': 'debito',
        'debito': 'debito',
        'pix': 'pix',
        'boleto': 'boleto',
        'paypal': 'paypal',
        'dinheiro': 'outro',
        'transferencia': 'outro',
    }.get(str((parsed or {}).get('payment_method') or 'cartao_credito'), 'cartao')


def _capture_payment_label(method: str) -> str | None:
    return {
        'cartao': 'cartao_credito',
        'debito': 'cartao_debito',
        'pix': 'pix',
        'boleto': 'boleto',
        'paypal': 'cartao_credito',
    }.get(method)


def resolve_card(db, hint: str | None = None, account_profile_id: str | None = None, card_id: str | None = None) -> str | None:
    if card_id and ObjectId.is_valid(str(card_id)):
        card = db.accounts.find_one({'_id': ObjectId(str(card_id))})
        if card and card.get('tipo') == 'cartao_credito' and card.get('status') != 'encerrado':
            return str(card['_id'])
    cards = [card for card in db.accounts.find({'tipo': 'cartao_credito'}) if card.get('status') != 'encerrado']
    if account_profile_id:
        matched = [card for card in cards if card.get('external_key') == account_profile_id or str(card.get('_id')) == account_profile_id]
        if len(matched) == 1:
            return str(matched[0]['_id'])
    needle = merchant_key(hint or '')
    if needle:
        matched = []
        for card in cards:
            label = merchant_key(' '.join(str(card.get(field) or '') for field in ('apelido', 'instituicao', 'nome')))
            if needle in label or label in needle:
                matched.append(card)
        if len(matched) == 1:
            return str(matched[0]['_id'])
    return None


def _account_fields(item: dict) -> dict:
    fields = {}
    card_id = item.get('card_id')
    account_id = item.get('account_id')
    if item.get('payment_method') == 'cartao' and card_id and ObjectId.is_valid(str(card_id)):
        fields['account_id'] = ObjectId(str(card_id))
    elif account_id and ObjectId.is_valid(str(account_id)):
        fields['account_id'] = ObjectId(str(account_id))
    elif isinstance(account_id, str) and account_id.startswith('profile:'):
        fields['account_profile_id'] = account_id.removeprefix('profile:')
    label = _capture_payment_label(item.get('payment_method') or '')
    if label:
        fields['metodo_pagamento'] = label
    return fields


def _close_total(plan_total: Decimal, captured: Decimal) -> bool:
    return abs(plan_total - captured) <= max(Decimal('0.05'), abs(plan_total) * Decimal('0.01'))


def _apply_suggestion(db, document: dict) -> None:
    suggestion = infer_classification(document, taxonomy_overrides(db))
    if suggestion.get('categoria') and not document.get('categoria_sugerida'):
        document['categoria_sugerida'] = suggestion['categoria']
    if suggestion.get('subcategoria') and not document.get('subcategoria'):
        document['subcategoria'] = suggestion['subcategoria']
    if suggestion.get('natureza'):
        document['natureza'] = suggestion['natureza']


def _pointer(db, item: dict, provisional_id: str, fingerprint: str) -> None:
    item['purchase_provisional_id'] = provisional_id
    item['purchase_fingerprint'] = fingerprint
    if db.commitments.find_one({'commitment_id': item['commitment_id']}):
        db.commitments.update_one({'commitment_id': item['commitment_id']}, {'$set': {
            'purchase_provisional_id': provisional_id,
            'purchase_fingerprint': fingerprint,
        }})


def _pending_purchase(db, item: dict):
    key = merchant_key(item.get('name') or '')
    total = statement_installment(item)
    found = []
    for document in db.provisional_transactions.find({'parcelamento_purchase': True, 'parcelamento_pending_commitment': True}):
        if document.get('status') == 'cancelled':
            continue
        if merchant_key(document.get('merchant_raw') or '') != key:
            continue
        if not _close_total(total, abs(_decimal(document.get('valor')))):
            continue
        found.append(document)
    return found[0] if len(found) == 1 else None


def ensure_parcelamento_purchase(db, item: dict, *, materialize: bool = True) -> str | None:
    """Insert the purchase once. The caller owns the Mongo transaction."""
    if item.get('kind') != 'parcelamento' or item.get('status') == 'cancelado' or item.get('future_deleted_at'):
        return None
    if item.get('purchase_provisional_id'):
        return item['purchase_provisional_id']
    if item.get('purchase_transaction_id'):
        return None
    if not materialize:
        return None
    pending = _pending_purchase(db, item)
    fingerprint = purchase_fingerprint(item)
    now = datetime.now(timezone.utc)
    if pending:
        provisional_id = pending['provisional_id']
        db.provisional_transactions.update_one({'provisional_id': provisional_id}, {'$set': {
            'status': 'confirmed',
            'status_revisao': 'validado',
            'confirmed_at': pending.get('confirmed_at') or now,
            'parcelamento_commitment_id': item['commitment_id'],
            'parcelamento_pending_commitment': False,
            'purchase_fingerprint': fingerprint,
            'installment_count': int(item['installment_count']),
            'installment_number': 1,
            'valor': Decimal128(str(-statement_installment(item))),
            'detalhe': installment_detail(item['installment_count']),
            'updated_at': now,
        }, '$inc': {'revision': 1}})
        _pointer(db, item, provisional_id, fingerprint)
        db.audit_log.insert_one({
            'event': 'commitment_purchase_adopted',
            'commitment_id': item['commitment_id'],
            'provisional_id': provisional_id,
            'created_at': now,
        })
        return provisional_id
    provisional_id = str(uuid5(NAMESPACE_URL, 'parcelamento-purchase:' + item['commitment_id']))
    event_id = str(uuid5(NAMESPACE_URL, 'parcelamento-purchase-event:' + item['commitment_id']))
    existing = db.provisional_transactions.find_one({'provisional_id': provisional_id})
    if not existing:
        document = {
            'provisional_id': provisional_id,
            'event_id': event_id,
            'status': 'confirmed',
            'revision': 1,
            'status_revisao': 'validado',
            'revisado_em': now,
            'data_evento': datetime.combine(as_date(item['purchase_date']), time(12), LOCAL_ZONE).astimezone(timezone.utc),
            'valor': Decimal128(str(-statement_installment(item))),
            'fluxo': 'saida',
            'moeda': 'BRL',
            'merchant_raw': str(item.get('name') or '').strip(),
            'merchant_normalized': ''.join(char.lower() for char in str(item.get('name') or '') if char.isalnum()),
            'tipo': 'compra',
            'detalhe': installment_detail(item['installment_count']),
            'installment_number': 1,
            'parcelamento_purchase': True,
            'parcelamento_commitment_id': item['commitment_id'],
            'parcelamento_pending_commitment': False,
            'installment_count': int(item['installment_count']),
            'purchase_fingerprint': fingerprint,
            'date_precision': 'day',
            'created_at': now,
            'confirmed_at': now,
            **_account_fields(item),
        }
        if item.get('category'):
            document['categoria_sugerida'] = item['category']
        _apply_suggestion(db, document)
        db.provisional_transactions.insert_one(document)
        if not db.raw_events.find_one({'event_id': event_id}):
            db.raw_events.insert_one({
                'event_id': event_id,
                'source_type': 'manual',
                'source_provider': 'parcelamento_commitment',
                'captured_at': now,
                'received_at': now,
                'processing_status': 'confirmed',
                'raw_payload': {'commitment_id': item['commitment_id'], 'installment_count': int(item['installment_count'])},
                'parsed_payload': {
                    'amount': format(statement_installment(item), 'f'),
                    'currency': 'BRL',
                    'merchant_raw': document['merchant_raw'],
                    'event_timestamp': document['data_evento'].isoformat(),
                    'direction': 'expense',
                    'event_type': 'manual_purchase',
                    'installment_count': int(item['installment_count']),
                },
                'parser_version': 'parcelamento-purchase-v1',
            })
        db.audit_log.insert_one({
            'event': 'commitment_purchase_recorded',
            'commitment_id': item['commitment_id'],
            'provisional_id': provisional_id,
            'created_at': now,
        })
    _pointer(db, item, provisional_id, fingerprint)
    return provisional_id


def refresh_parcelamento_purchase(db, previous: dict, item: dict) -> None:
    pointer = item.get('purchase_provisional_id')
    if not pointer:
        return
    provisional = db.provisional_transactions.find_one({'provisional_id': pointer, 'parcelamento_purchase': True})
    if not provisional or provisional.get('status') == 'cancelled':
        return
    if provisional.get('purchase_fingerprint') != previous.get('purchase_fingerprint'):
        return
    fingerprint = purchase_fingerprint(item)
    if fingerprint == previous.get('purchase_fingerprint'):
        return
    now = datetime.now(timezone.utc)
    merchant = str(item.get('name') or '').strip()
    changes = {
        'merchant_raw': merchant,
        'merchant_normalized': ''.join(char.lower() for char in merchant if char.isalnum()),
        'data_evento': datetime.combine(as_date(item['purchase_date']), time(12), LOCAL_ZONE).astimezone(timezone.utc),
        'valor': Decimal128(str(-statement_installment(item))),
        'detalhe': installment_detail(item['installment_count']),
        'installment_number': 1,
        'installment_count': int(item['installment_count']),
        'purchase_fingerprint': fingerprint,
        'updated_at': now,
        **_account_fields(item),
    }
    update = {'$set': changes, '$inc': {'revision': 1}}
    missing = {key: '' for key in ('account_id', 'account_profile_id', 'metodo_pagamento') if key not in changes}
    if missing:
        update['$unset'] = missing
    db.provisional_transactions.update_one({'provisional_id': pointer}, update)
    db.commitments.update_one({'commitment_id': item['commitment_id']}, {'$set': {'purchase_fingerprint': fingerprint}})
    item['purchase_fingerprint'] = fingerprint
    db.audit_log.insert_one({
        'event': 'commitment_purchase_updated',
        'commitment_id': item['commitment_id'],
        'provisional_id': pointer,
        'created_at': now,
    })


def sync_parcelamento_purchase(db, previous: dict, item: dict) -> None:
    if item.get('kind') != 'parcelamento' or item.get('status') == 'cancelado' or item.get('future_deleted_at'):
        return
    if item.get('purchase_provisional_id'):
        refresh_parcelamento_purchase(db, previous, item)
        return
    if item.get('status') != 'ativo' or item.get('purchase_transaction_id'):
        return
    if as_date(item['purchase_date']) < COMMITMENT_CUTOFF:
        return
    ensure_parcelamento_purchase(db, item, materialize=True)


def align_purchase_to_first_installment(db) -> dict:
    """Replace a statement row that stored the plan total with the first installment."""
    counts = {'corrected': 0, 'unchanged': 0, 'skipped': 0}
    identities = [item['commitment_id'] for item in db.commitments.find({'kind': 'parcelamento'}) if item.get('purchase_provisional_id')]

    def apply(writing, commitment_id):
        item = writing.commitments.find_one({'commitment_id': commitment_id})
        pointer = (item or {}).get('purchase_provisional_id')
        row = writing.provisional_transactions.find_one({'provisional_id': pointer, 'parcelamento_purchase': True}) if pointer else None
        if not item or not row or row.get('status') == 'cancelled':
            return 'skipped'
        current = abs(_decimal(row.get('valor'))).quantize(Decimal('0.01'))
        share = statement_installment(item)
        if current == share:
            return 'unchanged'
        if current != plan_total(item) or share == plan_total(item):
            return 'skipped'
        now = datetime.now(timezone.utc)
        fingerprint = purchase_fingerprint(item)
        writing.provisional_transactions.update_one({'provisional_id': pointer}, {'$set': {
            'valor': Decimal128(str(-share)),
            'detalhe': installment_detail(item['installment_count']),
            'installment_number': 1,
            'purchase_fingerprint': fingerprint,
            'updated_at': now,
        }, '$inc': {'revision': 1}})
        writing.commitments.update_one({'commitment_id': commitment_id}, {'$set': {'purchase_fingerprint': fingerprint}})
        writing.audit_log.insert_one({
            'event': 'commitment_purchase_amount_corrected',
            'commitment_id': commitment_id,
            'provisional_id': pointer,
            'created_at': now,
        })
        return 'corrected'

    for commitment_id in identities:
        if isinstance(db, Database):
            with mongo_transaction(db) as session:
                writing = session_database(db, session)
                lock_movement_insert(writing)
                outcome = apply(writing, commitment_id)
        else:
            outcome = apply(db, commitment_id)
        counts[outcome] += 1
    return counts


def _attach_evidence(db, event: dict, target: dict) -> None:
    if db.transaction_evidence.find_one({'event_id': event['event_id']}):
        return
    now = datetime.now(timezone.utc)
    db.transaction_evidence.insert_one({
        'evidence_id': str(uuid4()),
        'event_id': event['event_id'],
        'target_type': 'provisional',
        'provisional_id': target['provisional_id'],
        'match_score': 1.0,
        'decision': 'parcelamento_purchase',
        'created_at': now,
    })
    db.provisional_transactions.update_one({'provisional_id': target['provisional_id']}, {'$inc': {'evidence_count': 1}})


def _mark_event_matched(db, event: dict, target_id, parsed: dict | None = None, parser_version: str | None = None) -> None:
    changes = {'processing_status': 'matched', 'linked_transaction_id': target_id, 'confidence': 1.0}
    if parsed is not None:
        changes['parsed_payload'] = parsed
    if parser_version:
        changes['parser_version'] = parser_version
    db.raw_events.update_one({'event_id': event['event_id'], 'processing_status': {'$in': ['pending', 'needs_parse']}}, {'$set': changes})


def claim_existing_purchase(db, event: dict, merchant: str, count: int) -> bool:
    """A plan that already has its purchase must not gain a second extrato line."""
    matches = matching_parcelamentos(db, merchant, count)
    if len(matches) != 1:
        return False
    item = matches[0]
    pointer = item.get('purchase_provisional_id')
    if pointer:
        target = db.provisional_transactions.find_one({'provisional_id': pointer})
        if target and target.get('status') != 'cancelled':
            captured = _decimal((event.get('parsed_payload') or {}).get('amount'))
            if captured > 0 and not _close_total(plan_total(item), captured):
                return False
            _attach_evidence(db, event, target)
            _mark_event_matched(db, event, target.get('_id'))
            return True
        return False
    return bool(item.get('purchase_transaction_id'))


def _card_review(db, event: dict, draft: dict) -> None:
    identity = str(uuid5(NAMESPACE_URL, 'parcelamento-card:' + event['event_id']))
    if db.review_queue.find_one({'review_id': identity}):
        return
    db.review_queue.insert_one({
        'review_id': identity,
        'event_id': event['event_id'],
        'provisional_id': draft['provisional_id'],
        'status': 'pending',
        'reason': 'parcelamento_needs_card',
        'created_at': datetime.now(timezone.utc),
    })
    db.provisional_transactions.update_one({'provisional_id': draft['provisional_id']}, {'$set': {'parcelamento_pending_commitment': True}})


def open_parcelamento_for_capture(db, event: dict, draft: dict, parsed: dict) -> str | None:
    """Create or attach the plan for a capture already inserted in this transaction."""
    count = installment_count_of(parsed) or draft.get('installment_count')
    if not count:
        return None
    merchant = str(draft.get('merchant_raw') or parsed.get('merchant_raw') or '').strip()
    matches = matching_parcelamentos(db, merchant, int(count))
    if len(matches) > 1:
        return None
    if len(matches) == 1:
        item = matches[0]
        pointer = item.get('purchase_provisional_id')
        current = db.provisional_transactions.find_one({'provisional_id': pointer}) if pointer else None
        if current and current.get('status') != 'cancelled' and pointer != draft['provisional_id']:
            return item['commitment_id']
        fingerprint = purchase_fingerprint(item)
        _pointer(db, item, draft['provisional_id'], fingerprint)
        db.provisional_transactions.update_one({'provisional_id': draft['provisional_id']}, {'$set': {
            'parcelamento_commitment_id': item['commitment_id'],
            'parcelamento_pending_commitment': False,
            'purchase_fingerprint': fingerprint,
        }})
        return item['commitment_id']
    method = _payment_method(parsed)
    card_id = resolve_card(
        db,
        parsed.get('payment_instrument_hint'),
        parsed.get('account_profile_id'),
        parsed.get('card_id'),
    ) if method == 'cartao' else None
    if method == 'cartao' and not card_id:
        _card_review(db, event, draft)
        return None
    total = Decimal(str(parsed.get('amount') or abs(_decimal(draft.get('valor')))))
    purchase_day = local_date(draft['data_evento'])
    try:
        body = CommitmentIn(
            kind='parcelamento',
            name=merchant[:160],
            purchase_date=purchase_day,
            next_due_date=advance(purchase_day, 'mensal', 1),
            amount=split_installment(total, int(count)),
            frequency='mensal',
            installment_count=int(count),
            next_installment_number=1,
            payment_method=method,
            card_id=card_id,
            renewal_status='nao_aplicavel',
        )
    except ValidationError:
        _card_review(db, event, draft)
        return None
    item = stored_document(body)
    item['commitment_id'] = str(uuid5(NAMESPACE_URL, 'parcelamento-from-event:' + event['event_id']))
    item['source'] = 'capture_parcelamento'
    item['purchase_provisional_id'] = draft['provisional_id']
    item['purchase_fingerprint'] = purchase_fingerprint(item)
    if not db.commitments.find_one({'commitment_id': item['commitment_id']}):
        db.commitments.insert_one(item)
        db.audit_log.insert_one({
            'event': 'commitment_created',
            'commitment_id': item['commitment_id'],
            'source': 'capture_parcelamento',
            'created_at': item['created_at'],
        })
    db.provisional_transactions.update_one({'provisional_id': draft['provisional_id']}, {'$set': {
        'parcelamento_commitment_id': item['commitment_id'],
        'parcelamento_pending_commitment': False,
        'purchase_fingerprint': item['purchase_fingerprint'],
    }})
    return item['commitment_id']


def _decimal(value) -> Decimal:
    if value is None:
        return Decimal('0')
    if hasattr(value, 'to_decimal'):
        return value.to_decimal()
    return Decimal(str(value))


def mark_installment_draft(draft: dict, count: int, total) -> None:
    """The bank message states the plan total. Only the first installment posts."""
    share = split_installment(Decimal(str(total)), int(count))
    draft['valor'] = Decimal128(str(-share))
    draft['fluxo'] = 'saida'
    draft['parcelamento_purchase'] = True
    draft['installment_count'] = int(count)
    draft['installment_number'] = 1
    draft['detalhe'] = installment_detail(count)


def purchase_summary(commitment: dict) -> dict:
    count = int(commitment.get('installment_count') or 0)
    if commitment.get('kind') != 'parcelamento' or count < 2:
        return {}
    return {
        'count': count,
        'number': 1,
        'installment_amount': float(statement_installment(commitment)),
        'total': float(plan_total(commitment)),
    }


def decorate_purchase_links(db, items: list[dict], *, preloaded_commitments=None) -> None:
    capture_ids = [item.get('provisional_id') for item in items if item.get('movement_source') == 'capture' and item.get('provisional_id')]
    if not capture_ids:
        return
    capture_id_set = set(capture_ids)
    rows = preloaded_commitments if preloaded_commitments is not None else db.commitments.find({'purchase_provisional_id': {'$in': capture_ids}})
    for commitment in rows:
        pointer = commitment.get('purchase_provisional_id')
        if pointer not in capture_id_set:
            continue
        summary = purchase_summary(commitment)
        for item in items:
            if item.get('provisional_id') != pointer:
                continue
            if summary:
                item['parcelamento'] = summary
            if not item.get('commitment'):
                item['commitment'] = {
                    'id': commitment['commitment_id'],
                    'name': commitment['name'],
                    'kind': commitment['kind'],
                    'purchase': True,
                }


def _repair_one(db, event_id: str) -> bool:
    review = db.review_queue.find_one({'event_id': event_id, 'reason': 'event_needs_parse', 'status': 'pending'})
    event = db.raw_events.find_one({'event_id': event_id})
    if not review or not event or event.get('processing_status') != 'needs_parse':
        return False
    from app.transaction_service import enrich_notification_event
    enriched = enrich_notification_event(dict(event))
    parsed = enriched.get('parsed_payload') or {}
    count = installment_count_of(parsed)
    merchant = str(parsed.get('merchant_raw') or '').strip()
    if not count or not merchant:
        return False
    matches = matching_parcelamentos(db, merchant, count)
    if len(matches) != 1:
        return False
    item = matches[0]
    if as_date(item['purchase_date']) < COMMITMENT_CUTOFF and local_date(event.get('captured_at') or datetime.now(timezone.utc)) < COMMITMENT_CUTOFF:
        return False
    ensure_parcelamento_purchase(db, item, materialize=True)
    pointer = item.get('purchase_provisional_id')
    target = db.provisional_transactions.find_one({'provisional_id': pointer}) if pointer else None
    if not target:
        return False
    _attach_evidence(db, event, target)
    _mark_event_matched(db, event, target.get('_id'), parsed, enriched.get('parser_version'))
    db.review_queue.update_one({'review_id': review['review_id'], 'status': 'pending'}, {'$set': {
        'status': 'resolved',
        'decision': 'attached_to_parcelamento',
        'resolved_at': datetime.now(timezone.utc),
    }})
    return True


def repair_unparsed_installments(db, limit: int = 50) -> dict:
    pending = []
    for review in db.review_queue.find({'status': 'pending', 'reason': 'event_needs_parse'}):
        if review.get('event_id'):
            pending.append(review['event_id'])
        if len(pending) >= limit:
            break
    attached = skipped = 0
    for event_id in pending:
        if isinstance(db, Database):
            with mongo_transaction(db) as session:
                writing = session_database(db, session)
                lock_movement_insert(writing)
                changed = _repair_one(writing, event_id)
        else:
            changed = _repair_one(db, event_id)
        if changed:
            attached += 1
        else:
            skipped += 1
    return {'attached': attached, 'skipped': skipped}


def backfill_recent_parcelamento_purchases(db) -> dict:
    """Materialize purchases for parcelamentos contracted on or after the cutoff."""
    counts = {'examined': 0, 'inserted': 0, 'already': 0, 'before_cutoff': 0}
    identities = []
    for item in db.commitments.find({'kind': 'parcelamento'}):
        if item.get('status') != 'ativo' or item.get('future_deleted_at'):
            continue
        counts['examined'] += 1
        if item.get('purchase_provisional_id') or item.get('purchase_transaction_id'):
            counts['already'] += 1
            continue
        if as_date(item['purchase_date']) < COMMITMENT_CUTOFF:
            counts['before_cutoff'] += 1
            continue
        identities.append(item['commitment_id'])

    def apply(writing, commitment_id):
        current = writing.commitments.find_one({'commitment_id': commitment_id})
        if not current or current.get('purchase_provisional_id') or current.get('purchase_transaction_id'):
            return False
        before = writing.provisional_transactions.find_one({'provisional_id': str(uuid5(NAMESPACE_URL, 'parcelamento-purchase:' + commitment_id))})
        ensure_parcelamento_purchase(writing, current, materialize=True)
        after = writing.provisional_transactions.find_one({'provisional_id': current.get('purchase_provisional_id')})
        return bool(after and not before)

    for commitment_id in identities:
        if isinstance(db, Database):
            with mongo_transaction(db) as session:
                writing = session_database(db, session)
                lock_movement_insert(writing)
                created = apply(writing, commitment_id)
        else:
            created = apply(db, commitment_id)
        if created:
            counts['inserted'] += 1
        else:
            counts['already'] += 1
    return counts
