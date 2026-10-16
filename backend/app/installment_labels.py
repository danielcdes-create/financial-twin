"""Display labels for imported plans, following manually reviewed linked purchases."""
from bson import ObjectId

from app.cards import card_item


def _as_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else ''


def _same(left, right):
    return str(left) == str(right)


def _rows_for(db, identities):
    wanted = [item for item in identities if item not in (None, '')]
    if not wanted:
        return []
    found = list(db.transactions.find({'_id': {'$in': wanted}}))
    seen = {str(row.get('_id')) for row in found}
    if len(seen) < len({str(item) for item in wanted}):
        for row in db.transactions.find({}):
            if str(row.get('_id')) in {str(item) for item in wanted} and str(row.get('_id')) not in seen:
                found.append(row)
                seen.add(str(row.get('_id')))
    return found


def card_labels(db, card_ids):
    """Readable card name for each credit-card account. A bank account is not a card."""
    valid = []
    for item in card_ids:
        if ObjectId.is_valid(str(item or '')):
            valid.append(ObjectId(str(item)))
    if not valid:
        return {}
    cards = {str(row['_id']): row for row in db.accounts.find({'_id': {'$in': valid}, 'tipo': 'cartao_credito'})}
    if not cards:
        return {}
    metas = {row['_id']: row for row in db.account_profiles.find({'_id': {'$in': [f'card-profile:{identity}' for identity in cards]}})}
    labels = {}
    for identity, card in cards.items():
        item = card_item(card, metas.get(f'card-profile:{identity}', {}))
        labels[identity] = _as_text(item.get('apelido')) or f"{item.get('instituicao') or 'Cartão'} · final {item.get('cartao_final') or ''}".strip(' ·')
    return labels


def plan_card_labels(db, plan_ids):
    keys = [str(item) for item in plan_ids if item not in (None, '')]
    if not keys:
        return {}
    links = list(db.installment_card_links.find({'_id': {'$in': keys}}))
    names = card_labels(db, [row.get('card_id') for row in links])
    return {str(row['_id']): names.get(str(row.get('card_id') or '')) for row in links if names.get(str(row.get('card_id') or ''))}


def linked_statement_snapshots(db, groups):
    """Live seller, category and card on the linked statement rows. The audit log is not the source."""
    pairs = [(key, list(identities or [])) for key, identities in groups.items()]
    rows = _rows_for(db, [identity for _, identities in pairs for identity in identities])
    by_id = {str(row.get('_id')): row for row in rows}
    merchant_ids = [row.get('merchant_id') for row in rows if row.get('merchant_id')]
    merchants = {str(row['_id']): row.get('nome_padrao') for row in db.merchants.find({'_id': {'$in': merchant_ids}}, {'nome_padrao': 1})} if merchant_ids else {}
    cards = card_labels(db, [row.get('card_id') for row in rows])
    result = {}
    for key, identities in pairs:
        chosen = None
        for identity in identities:
            row = by_id.get(str(identity))
            if not row:
                continue
            vendor = _as_text(row.get('vendedor')) or _as_text(row.get('merchant_name')) or _as_text(merchants.get(str(row.get('merchant_id') or '')))
            card = _as_text(row.get('card_label')) or cards.get(str(row.get('card_id') or '')) or ''
            snapshot = {
                'vendedor': vendor,
                'descricao_original': _as_text(row.get('descricao_original')) or _as_text(row.get('estabelecimento_original')) or _as_text(row.get('merchant_raw')),
                'categoria': _as_text(row.get('categoria')),
                'subcategoria': _as_text(row.get('subcategoria')),
                'natureza': _as_text(row.get('natureza')),
                'metodo_pagamento': _as_text(row.get('metodo_pagamento')),
                'plataforma': _as_text(row.get('plataforma')),
                'card_label': card,
            }
            if not any(snapshot.values()):
                continue
            if chosen is None or (vendor and not chosen.get('vendedor')):
                chosen = snapshot
        if chosen:
            result[key] = chosen
    return result


def reviewed_plan_labels(db, plans):
    """Return a plan's latest reviewed seller name without changing its source data.

    Identity comes only from the importer's durable installment transaction links.
    A manually edited seller on a linked ledger row is the app-facing label; the
    imported descriptor remains available as the fallback and original source.
    """
    plans = list(plans)
    linked = []
    for plan in plans:
        for part in plan.get("installments", []):
            transaction_id = part.get("transaction_id")
            if transaction_id:
                linked.append((str(plan.get("_id")), part, transaction_id))
    if not linked:
        return {}

    transaction_ids = list({identity for _, _, identity in linked})
    overrides = {row['_id'].split(':', 1)[1]: row for row in db.future_schedule_overrides.find(
        {'_id': {'$in': [f"installment:{plan_id}" for plan_id, _, _ in linked]}},
        {'updated_at': 1},
    )}
    edits = list(db.audit_log.find(
        {"event": "transaction_classification_updated", "transaction_id": {"$in": transaction_ids},
         "after.vendedor": {"$exists": True}},
        {"transaction_id": 1, "after.vendedor": 1, "created_at": 1},
    ))
    edits.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
    reviewed_names = {}
    for edit in edits:
        identity = str(edit.get("transaction_id"))
        if identity not in reviewed_names:
            name = (edit.get("after") or {}).get("vendedor")
            reviewed_names[identity] = (name.strip() if isinstance(name, str) and name.strip() else None,
                                        edit.get("created_at"))

    candidates = {}
    for plan_id, part, transaction_id in linked:
        name, edited_at = reviewed_names.get(str(transaction_id), (None, None))
        if not name:
            continue
        overridden_at = (overrides.get(plan_id) or {}).get('updated_at')
        if overridden_at and edited_at and edited_at <= overridden_at:
            continue
        rank = (edited_at, -(part.get("numero") or part.get("parcela_numero") or 9999))
        current = candidates.get(plan_id)
        if current is None or rank > current[0]:
            candidates[plan_id] = (rank, name.strip())
    return {plan_id: value[1] for plan_id, value in candidates.items()}
