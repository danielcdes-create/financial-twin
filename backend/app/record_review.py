"""Read existing, explicitly reviewed statement captures without rewriting history."""
import base64
import hashlib
import json
from decimal import Decimal

from bson import ObjectId
from fastapi import HTTPException

from app.account_profiles import account_profiles, legacy_accounts_by_profile, profile_for_legacy
from app.financial_time import local_date, utc_instant


def historical_statement_reviews(db, capture_query):
    """Recover only the original rows of an audited, completed statement review.

    Explicit statuses always win. Older statement commits omitted the review
    fields, so their original validation can be displayed from the saved review
    and audit. An edited capture or an incomplete source stays pending.
    """
    candidates = list(db.provisional_transactions.find({'$and': [capture_query,
        {'status_revisao': {'$exists': False}, 'capture_batch_id': {'$exists': True}, 'revision': 1}]}))
    if not candidates:
        return {}
    batch_ids = list({row['capture_batch_id'] for row in candidates if row.get('capture_batch_id')})
    batches = {row['batch_id']: row for row in db.capture_batches.find(
        {'batch_id': {'$in': batch_ids}, 'status': 'committed'})}
    audited = {}
    for audit in db.audit_log.find({'event': 'statement_batch_committed', 'batch_id': {'$in': batch_ids}}):
        audited.setdefault(audit['batch_id'], []).append(audit)
    reviews = {}
    accounts = {}
    legacy_by_profile = None
    valid_profile_keys = None
    for capture in candidates:
        batch = batches.get(capture.get('capture_batch_id'))
        if not batch or not batch.get('committed_at') or not capture.get('confirmed_at'):
            continue
        stamp = utc_instant(batch['committed_at'])
        if utc_instant(capture['confirmed_at']) != stamp:
            continue
        if capture.get('updated_at') and utc_instant(capture['updated_at']) > stamp:
            continue
        reference = {'row_id': capture.get('capture_batch_row'), 'provisional_id': capture.get('provisional_id')}
        inserted = (batch.get('result') or {}).get('inserted') or []
        if reference not in inserted:
            continue
        if not any(reference in (audit.get('inserted') or []) and audit.get('created_at') and
                   utc_instant(audit['created_at']) == stamp for audit in audited.get(batch['batch_id'], [])):
            continue
        reviewed = next((row for row in (batch.get('review') or {}).get('rows', [])
                         if row.get('row_id') == reference['row_id'] and row.get('selected', True)), None)
        if not reviewed or not reviewed.get('date') or reviewed.get('amount') is None:
            continue
        account_ref = (batch.get('review') or {}).get('account_ref') or ''
        kind, _, identity = account_ref.partition(':')
        if kind == 'ledger' and ObjectId.is_valid(identity):
            if identity not in accounts:
                account = db.accounts.find_one({'_id': ObjectId(identity)})
                if account:
                    if legacy_by_profile is None:
                        legacy_by_profile = legacy_accounts_by_profile(db)
                        valid_profile_keys = {p["key"] for p in account_profiles(db)}
                    from app.account_positions import account_key_for_legacy
                    key = account_key_for_legacy(account)
                    matched = legacy_by_profile.get(key)
                    prof = key if matched and matched["_id"] == account["_id"] and key in valid_profile_keys else None
                else:
                    prof = None
                accounts[identity] = (account, prof)
            account, profile = accounts[identity]
            if not account or str(capture.get('account_id')) != identity or capture.get('account_profile_id') != profile:
                continue
        elif kind == 'profile' and identity:
            if capture.get('account_id') is not None or capture.get('account_profile_id') != identity:
                continue
        else:
            continue
        if capture.get('moeda') != 'BRL' or capture.get('fluxo') != ('saida' if Decimal(str(reviewed['amount'])) < 0 else 'entrada'):
            continue
        amount = capture.get('valor')
        amount = amount.to_decimal() if hasattr(amount, 'to_decimal') else Decimal(str(amount))
        kinds = {'expense': 'compra', 'income': 'receita', 'refund': 'estorno', 'bill_payment': 'pagamento_fatura'}
        if (amount != Decimal(str(reviewed['amount'])) or
            local_date(capture.get('data_evento')).isoformat() != reviewed['date'] or
            capture.get('merchant_raw') != str(reviewed.get('description') or '').strip() or
            capture.get('tipo') != kinds.get(reviewed.get('kind')) or
            capture.get('bank_transaction_ref') != reviewed.get('bank_ref')):
            continue
        reviews[capture['provisional_id']] = stamp
    return reviews


def capture_review_query(query, status, historical):
    validated = {'$or': [
        {'status_revisao': 'validado'},
        {'status_revisao': {'$exists': False}, 'provisional_id': {'$in': list(historical)}},
    ]}
    if status == 'validado':
        return {'$and': [query, validated]}
    if status == 'pendente':
        return {'$and': [query, {'status_revisao': {'$ne': 'validado'}},
                         {'provisional_id': {'$nin': list(historical)}}]}
    return query


def display_capture_review(item, historical):
    if item.get('status_revisao') not in {'pendente', 'validado'}:
        stamp = historical.get(item.get('provisional_id'))
        item['status_revisao'] = 'validado' if stamp else 'pendente'
        if stamp:
            item['revisado_em'] = stamp.isoformat().replace('+00:00', 'Z')
            item['review_origin'] = 'statement_batch_review'
    return item


def review_filter_key(status, search, category, account_id, date_from, date_to):
    values = [status, (search or '').strip(), category, account_id,
              date_from.isoformat() if date_from else None, date_to.isoformat() if date_to else None]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def encode_review_cursor(cursor, filter_key):
    payload = {'kind': 'review-records-v1', 'filters': filter_key, 'movement_cursor': cursor}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')


def decode_review_cursor(cursor, filter_key):
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
        if payload['kind'] != 'review-records-v1' or payload['filters'] != filter_key or not isinstance(payload['movement_cursor'], str):
            raise ValueError()
        return payload['movement_cursor']
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, 'O filtro da revisão mudou. Volte à primeira página.')
