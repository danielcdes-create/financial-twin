"""Reviewed card associations without rewriting imported installment history."""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException

from app.cards import card_item, card_context, token
from app.installment_labels import linked_statement_snapshots, reviewed_plan_labels


def attach_cards(db, plans):
    plans = list(plans)
    if not plans:
        return []
    labels = reviewed_plan_labels(db, plans)
    links = {str(row['_id']): row for row in db.installment_card_links.find(
        {'_id': {'$in': [str(plan['_id']) for plan in plans]}})}
    ids = {ObjectId(row['card_id']) for row in links.values() if ObjectId.is_valid(row.get('card_id', ''))}
    cards = {str(row['_id']): row for row in db.accounts.find({'_id': {'$in': list(ids)}, 'tipo': 'cartao_credito'})}
    metas = {row['_id']: row for row in db.account_profiles.find(
        {'_id': {'$in': [f'card-profile:{identity}' for identity in cards]}})}
    groups = {}
    for plan in plans:
        identities = []
        for part in plan.get('installments') or []:
            if part.get('transaction_id'):
                identities.append(part['transaction_id'])
            if part.get('open_statement_id'):
                identities.append(part['open_statement_id'])
        groups[str(plan['_id'])] = identities
    snapshots = linked_statement_snapshots(db, groups)
    result = []
    for plan in plans:
        link = links.get(str(plan['_id']))
        identity = link.get('card_id') if link else None
        card = cards.get(identity)
        item = card_item(card, metas.get(f'card-profile:{identity}', {})) if card else None
        raw = plan.get('descricao_original') or plan.get('descricao') or ''
        display = labels.get(str(plan['_id'])) or plan.get('display_name') or plan.get('descricao') or 'Compra parcelada'
        snapshot = snapshots.get(str(plan['_id'])) or {}
        linked_card = (item['apelido'] or f"{item['instituicao']} · final {item['cartao_final']}") if item else None
        saved_card = (plan.get('profile') or {}).get('card_label') or snapshot.get('card_label')
        card_name = saved_card or linked_card
        if snapshot.get('vendedor') and display in {None, '', raw, plan.get('descricao')}:
            display = snapshot['vendedor']
        result.append({**plan, 'display_name': display,
            'card_id': identity, 'card_label': card_name,
            'card_deleted': bool(item and item['deleted']),
            'card_link_requires_review': bool(link and not card),
            **({'linked_purchase': snapshot} if snapshot else {}),
            **({'categoria': snapshot['categoria']} if snapshot.get('categoria') and not plan.get('categoria') else {}),
            **({'subcategoria': snapshot['subcategoria']} if snapshot.get('subcategoria') and not plan.get('subcategoria') else {})})
    return result


def bind_reviewed_card(db, plan_id, card_id, expected_token, basis):
    """Called within a transaction after source review; no financial row changes."""
    if not ObjectId.is_valid(plan_id):
        raise HTTPException(404, 'Parcelamento não encontrado.')
    plan = db.installment_plans.find_one({'_id': ObjectId(plan_id)})
    if not plan:
        raise HTTPException(404, 'Parcelamento não encontrado.')
    previous = db.installment_card_links.find_one({'_id': plan_id})
    if token(plan, previous) != expected_token:
        raise HTTPException(409, 'O parcelamento ou o vínculo mudou. Confira novamente.')
    card, meta = card_context(db, card_id)
    if meta.get('deleted'):
        raise HTTPException(409, 'Restaure o cartão antes de vincular o parcelamento.')
    if previous and previous.get('card_id') == card_id:
        return previous
    now = datetime.now(timezone.utc)
    after = {'_id': plan_id, 'card_id': str(card['_id']), 'basis': basis,
             'revision': (previous or {}).get('revision', 0) + 1, 'updated_at': now}
    db.installment_card_links.update_one({'_id': plan_id}, {'$set': {key: value for key, value in after.items() if key != '_id'}}, upsert=True)
    db.audit_log.insert_one({'event': 'installment_card_linked', 'plan_id': plan_id,
        'before': previous, 'after': after, 'created_at': now})
    return after
