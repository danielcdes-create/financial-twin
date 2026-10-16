"""Shared eligibility of unpaid obligations; a suggestion is not a cash debit."""
from decimal import Decimal

OPEN_OBLIGATION_STATUSES = ('futura', 'aberta', 'vencida', 'parcialmente_paga')


def bill_status(balance, paid, due, current_date):
    if balance == Decimal('0'): return 'paga'
    if paid: return 'parcialmente_paga'
    return 'vencida' if due < current_date else 'aberta'
