"""Monthly future charges; reading this projection never advances a schedule."""
import re
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from app.atomic import mongo_snapshot, with_batch_size
from app.auth import require_session
from app.commitments import commitment_cutoff, advance, linked_due_dates, schedule_from, historical_occurrence_conflicts
from app.commitment_estimates import effective_commitments
from app.economic_policy import amount
from app.financial_time import civil_date, today
from app.future_commitments import database, effective_documents
from app.obligation_policy import OPEN_OBLIGATION_STATUSES
from app.installment_cards import attach_cards

router = APIRouter(dependencies=[Depends(require_session)])
MAX_MONTHS = 120


def monthly_projection(db, months=None, as_of: date | None = None):
    if months is not None and (isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= MAX_MONTHS):
        raise HTTPException(422, 'Escolha de 1 a 120 meses.')
    current = as_of or today()
    cutoff = commitment_cutoff(current)
    first = current.replace(day=1)
    last_limit = advance(first, 'mensal', MAX_MONTHS) - timedelta(days=1)
    charges, bills, carried, warnings = [], [], [], []
    latest_installment = None

    def warning(label, reason):
        message = f'{label}: {reason}'
        if message not in warnings:
            warnings.append(message)

    def add(identity, name, category, value, part, *, estimated=False, destination=None, details=None):
        nonlocal latest_installment
        if value is None or value <= 0 or part.get('future_excluded'):
            return
        due = None
        if part.get('data_vencimento') or part.get('due_date'):
            due = civil_date(part.get('data_vencimento') or part['due_date'])
            if due < cutoff:
                return
            month = due.strftime('%Y-%m')
        else:
            month = str(part.get('competencia') or '')
            if not re.fullmatch(r'[1-9]\d{3}-(0[1-9]|1[0-2])', month):
                warning(name, 'vencimento ou competência ausente; fora do gráfico')
                return
            if month < cutoff.strftime('%Y-%m'):
                return
        # A due before today stays in its own month. It is not dropped, and it is not moved forward.
        entry = {
            'source_ref': identity, 'name': name, 'category': category,
            'month': month, 'amount': value, 'estimated': estimated, 'overdue': bool(due and due < current),
            'due_date': due.isoformat() if due else None,
            **(details or {})}
        if month < first.strftime('%Y-%m'):
            if destination is None:
                carried.append(entry)
            return
        if category == 'installments':
            latest_installment = max(latest_installment or month, month)
        (charges if destination is None else destination).append(entry)

    with mongo_snapshot(db) as (reading, consistency):
        managed = effective_commitments(
            reading,
            with_batch_size(reading.commitments.find({'status': 'ativo'}), 2000),
        )
        for item in managed:
            name = item['name']
            if historical_occurrence_conflicts(item):
                warning(name, 'agenda e pagamentos precisam de conferência; fora do gráfico')
                continue
            estimate = item.get('amount_estimate')
            if estimate and estimate.get('requires_review'):
                warning(name, 'média não confirmada; usando valor de referência')
            # Seek from the cutoff so an unlinked due before today remains pending. The offset still skips older history.
            seek = cutoff
            gap = max(0, (first.year - seek.year) * 12 + first.month - seek.month)
            count = (last_limit - seek).days // 7 + 2 if item['frequency'] == 'semanal' else MAX_MONTHS + 2 + gap
            if item['kind'] == 'parcelamento':
                count = max(count, item.get('installment_count') or 0)
            variable = item.get('amount_mode') == 'last_three_bills' or bool(item.get('amount_estimate'))
            category = 'other' if item['kind'] == 'obrigacao' else 'accounts' if variable else 'installments' if item['kind'] == 'parcelamento' else 'subscriptions'
            linked = linked_due_dates(item)
            for part in schedule_from(item, seek, count):
                if str(part['due_date'])[:10] in linked:
                    continue
                add('commitment:' + item['commitment_id'], name, category,
                    Decimal(str(part['amount'])), part, estimated=bool(estimate), details={
                        'source_type': item['kind'], 'occurrence_number': part.get('installment_number'),
                        'occurrence_total': item.get('installment_count'), 'payment_method': item.get('payment_method'),
                        'billing_platform': item.get('billing_platform'), 'card_id': item.get('card_id'),
                        'account_id': item.get('account_id'), 'category_name': item.get('category'),
                    })

        plans = attach_cards(
            reading,
            effective_documents(
                reading,
                'installment',
                with_batch_size(reading.installment_plans.find({'status': 'ativo'}), 2000),
            ),
        )
        plan_labels = {str(item['_id']): item.get('display_name') for item in plans if item.get('display_name')}
        plan_ids = {str(item['_id']) for item in plans}
        for item in plans:
            name = plan_labels.get(str(item['_id'])) or item.get('descricao') or 'Compra parcelada'
            if item.get('future_correction_needs_review'):
                warning(name, 'correção antiga precisa de conferência; usando a fonte atual')
            if str(item.get('moeda') or 'BRL').upper() != 'BRL':
                warning(name, 'moeda diferente de BRL; fora do gráfico')
                continue
            total_parts = item.get('numero_parcelas') or len(item.get('installments', []))
            for part in item.get('installments', []):
                if part.get('status') != 'projetada' or part.get('transaction_id') or part.get('bill_id') or part.get('payment_link'):
                    continue
                add('installment:' + str(item['_id']), name, 'installments', amount(part.get('valor')), part,
                    details={'source_type': 'installment_plan', 'occurrence_number': part.get('numero') or part.get('parcela_numero'),
                             'occurrence_total': total_parts, 'card_label': item.get('card_label')})

        obligations = effective_documents(
            reading,
            'obligation',
            with_batch_size(reading.obligations.find({'status': {'$in': list(OPEN_OBLIGATION_STATUSES)}}), 2000),
        )
        for item in obligations:
            # An explicitly linked installment must not also enter as an obligation.
            if str(item.get('origem_id')) in plan_ids and item.get('tipo') != 'fatura_cartao':
                continue
            name = item.get('descricao') or item.get('tipo') or 'Obrigação'
            if item.get('future_correction_needs_review'):
                warning(name, 'correção antiga precisa de conferência; usando a fonte atual')
            if str(item.get('moeda') or 'BRL').upper() != 'BRL':
                warning(name, 'moeda diferente de BRL; fora do gráfico')
                continue
            if item.get('payment_link'):
                continue
            is_bill = item.get('tipo') == 'fatura_cartao'
            add('obligation:' + str(item['_id']), name, 'other', amount(item.get('saldo_aberto')), item,
                destination=bills if is_bill else None, details={
                    'source_type': item.get('tipo'), 'payment_method': item.get('metodo_pagamento'),
                })

    automatic = max(1, (int(latest_installment[:4]) - first.year) * 12 + int(latest_installment[5:]) - first.month + 1) if latest_installment else 12
    horizon = months if months is not None else min(automatic, MAX_MONTHS)
    rows = []
    for index in range(horizon):
        key = advance(first, 'mensal', index).strftime('%Y-%m')
        entries = [entry for entry in charges if entry['month'] == key]
        totals = {kind: sum((entry['amount'] for entry in entries if entry['category'] == kind), Decimal('0'))
                  for kind in ('installments', 'subscriptions', 'accounts', 'other')}
        rows.append({'month': key, 'total': str(sum(totals.values(), Decimal('0'))),
                     **{kind: str(value) for kind, value in totals.items()},
                     'estimated': str(sum((entry['amount'] for entry in entries if entry['estimated']), Decimal('0'))),
                     'bill_balance': str(sum((entry['amount'] for entry in bills if entry['month'] == key), Decimal('0'))),
                     'items': [{**{field: value for field, value in entry.items() if field != 'overdue'}, 'amount': str(entry['amount'])} for entry in entries]})
    window = rows[:12]
    carried_total = sum((entry['amount'] for entry in carried), Decimal('0'))
    first_key = rows[0]['month']
    month_overdue = sum((entry['amount'] for entry in charges if entry['month'] == first_key and entry.get('overdue')), Decimal('0'))
    month_upcoming = sum((entry['amount'] for entry in charges if entry['month'] == first_key and not entry.get('overdue')), Decimal('0'))
    this_overdue = carried_total + month_overdue
    this_upcoming = month_upcoming
    def window_sum(key):
        base = sum((Decimal(row[key]) for row in window), Decimal('0'))
        if key == 'bill_balance':
            extra = Decimal('0')
        elif key == 'total':
            extra = carried_total
        else:
            extra = sum((entry['amount'] for entry in carried if entry['category'] == key), Decimal('0'))
        return str(base + extra)
    this_month = rows[0]['total'] if carried_total == 0 else str(Decimal(rows[0]['total']) + carried_total)
    summary = {'this_month': this_month, 'overdue': str(this_overdue), 'upcoming': str(this_upcoming),
               'next_12': window_sum('total') if len(rows) >= 12 else None,
               'installments': window_sum('installments'), 'subscriptions': window_sum('subscriptions'),
               'accounts': window_sum('accounts'), 'other': window_sum('other'),
               'bill_balance': window_sum('bill_balance'), 'window_months': len(window)}
    return {'items': rows, 'months': horizon, 'start_month': rows[0]['month'], 'end_month': rows[-1]['month'],
            'as_of': current.isoformat(), 'last_installment_month': latest_installment,
            'automatic_horizon_capped': months is None and automatic > MAX_MONTHS,
            'warnings': warnings, 'read_consistency': consistency, 'currency': 'BRL',
            'total': str(sum((Decimal(row['total']) for row in rows), Decimal('0'))), 'summary': summary,
            'commitment_cutoff_date': cutoff.isoformat()}


@router.get('/v1/commitment-projection')
def projection(months: int | None = Query(default=None, ge=1, le=MAX_MONTHS), db=Depends(database)):
    return monthly_projection(db, months)
