"""Read-only rolling estimates. Confirmed expenses remain financial history."""
import copy
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from bson import ObjectId
from app.economic_policy import amount, eligible_payment
from app.financial_time import local_date, today
from app.account_positions import account_key_for_legacy
from app.account_profiles import legacy_accounts_by_profile

PATTERNS = {
    'cemig': r'\bCEMIG\b',
    'supergasbras': r'\bSUPERGASBRA(?:S)?',
    'claro': r'\bCLARO\b',
    'tim': r'\bTIM\b',
}
FIELDS = ('estabelecimento_original', 'descricao_original', 'merchant_raw', 'merchant_name')


def estimate_account(db, account, legacy_map=None):
    """Profile-only captures require an unambiguous imported bank account."""
    if not ObjectId.is_valid(account):
        return None
    key = account_key_for_legacy(db.accounts.find_one({'_id': ObjectId(account)}) or {})
    by_profile = legacy_map if legacy_map is not None else legacy_accounts_by_profile(db)
    matched = by_profile.get(key)
    return key if matched and str(matched['_id']) == account else None


def same_estimate_account(row, account, profile_key, captured=False):
    if captured and row.get('account_profile_id'):
        return bool(profile_key and row['account_profile_id'] == profile_key and (
            not row.get('account_id') or str(row['account_id']) == account))
    return str(row.get('account_id')) == account


def estimate(db, item, rule, as_of=None, legacy_map=None):
    """Average the last three actual bills, not three calendar-month slots."""
    result = copy.deepcopy(item)
    result['amount_rule_available'] = True
    result['amount_mode'] = item.get('amount_mode') or 'last_three_bills'
    if result['amount_mode'] == 'fixed':
        return result
    cutoff = min(as_of or today(), today())
    provider = rule.get('provider')
    account = str(item.get('account_id') or '')
    valid = (rule.get('method') == 'last_three_bills' and provider in PATTERNS
             and rule.get('account_id') == account and ObjectId.is_valid(account)
             and item.get('kind') == 'assinatura' and item.get('frequency') == 'mensal'
             and not item.get('card_id'))
    metadata = {'method': 'last_three_bills', 'provider': provider, 'required_count': 3,
                'sample_count': 0, 'sources': [], 'requires_review': True, 'rule_valid': bool(valid)}
    result['amount_estimate'] = metadata
    if not valid:
        metadata['reason'] = 'Regra ou conta alterada; confira a previsão.'
        return result
    pattern = re.compile(PATTERNS[provider], re.I)
    profile_key = estimate_account(db, account, legacy_map=legacy_map)
    merchant_query = {'$or': [{field: {'$regex': pattern.pattern, '$options': 'i'}} for field in FIELDS]}
    sources = []
    for collection, captured in [('transactions', False), ('provisional_transactions', True)]:
        accounts = [{'account_id': {'$in': [ObjectId(account), account]}}]
        if captured and profile_key:
            accounts.append({'account_profile_id': profile_key})
        query = {'$and': [{'$or': accounts}, merchant_query]}
        cursor = db[collection].find(query)
        if hasattr(cursor, 'sort'):
            try:
                cursor = cursor.sort([('data_evento', -1)])
            except Exception:
                pass
        if hasattr(cursor, 'limit'):
            try:
                cursor = cursor.limit(60)
            except Exception:
                pass
        for row in cursor:
            if not same_estimate_account(row, account, profile_key, captured) or not eligible_payment(row, captured):
                continue
            if captured and row.get('linked_transaction_id'):
                continue  # One payment represented in both collections counts once.
            if str(row.get('moeda') or row.get('currency') or 'BRL').upper() != 'BRL':
                continue
            if not any(pattern.search(str(row.get(field) or '')) for field in FIELDS):
                continue
            if not row.get('data_evento'):
                continue
            paid = local_date(row['data_evento'])
            if paid > cutoff:
                continue
            identity = str(row.get('provisional_id') if captured else row.get('_id'))
            if not identity or identity == 'None':
                continue
            sources.append({'source_kind': collection, 'source_id': identity,
                            'paid_date': paid.isoformat(), 'amount': str(abs(amount(row['valor']))),
                            '_instant': str(row['data_evento'])})
    sources.sort(key=lambda x: (x['paid_date'], x['_instant'], x['source_id']), reverse=True)
    selected = sources[:3]
    metadata['sources'] = [{k:v for k,v in row.items() if k != '_instant'} for row in selected]
    metadata['sample_count'] = len(selected)
    if len(selected) < 3:
        metadata['reason'] = 'Ainda não há três contas confirmadas; valor de referência mantido.'
        return result
    average = (sum((Decimal(row['amount']) for row in selected), Decimal('0')) / 3).quantize(
        Decimal('.01'), rounding=ROUND_HALF_UP)
    result['amount'] = str(average)
    metadata.update({'requires_review': False, 'amount': str(average),
                     'latest_paid_date': selected[0]['paid_date']})
    return result


def effective_commitments(db, documents, as_of=None):
    documents = list(documents)
    if not documents:
        return []
    identities = [x['commitment_id'] for x in documents if x.get('commitment_id')]
    collection = getattr(db, 'commitment_amount_rules', None)
    if not identities or collection is None:
        return documents
    rules = {x['_id']: x for x in collection.find({'_id': {'$in': identities}})}
    needs_estimate = any(item.get('commitment_id') in rules or item.get('amount_mode') == 'last_three_bills' for item in documents)
    legacy_map = legacy_accounts_by_profile(db) if needs_estimate else None
    return [estimate(db, item, rules.get(item.get('commitment_id'), {}), as_of, legacy_map=legacy_map) if item.get('commitment_id') in rules or item.get('amount_mode') == 'last_three_bills'
            else item for item in documents]
