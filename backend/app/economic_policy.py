"""Single economic policy shared by analytics, settlements and planning."""
from decimal import Decimal
from bson.decimal128 import Decimal128
EXPENSE_REASONS = {'despesa_real','estorno_abate_gasto','estorno','ajuste_analitico_no_recorte_de_gastos'}
NEUTRAL_REASONS = {'pagamento_fatura','transferencia_propria','movimentacao_credito_cartao','pagamento_fatura_cartao'}
def amount(value):
    if value is None: return None
    result = value.to_decimal() if isinstance(value, Decimal128) else Decimal(str(value))
    if not result.is_finite(): raise ValueError('Valor financeiro deve ser finito.')
    return result
def role(item, captured=False):
    reason = (item.get('classification') or {}).get('migration_reason'); kind = str(item.get('tipo') or '').casefold()
    if item.get('natureza') == 'TRANSFERENCIA' or item.get('transfer') or reason in NEUTRAL_REASONS or kind in {'pagamento_fatura','pagamento_fatura_cartao'}: return 'neutral'
    if item.get('natureza') == 'ESTORNO' or reason in {'estorno','estorno_abate_gasto'} or kind == 'estorno': return 'refund'
    value = amount(item.get('valor'))
    if value is None: return 'unknown'
    if reason in EXPENSE_REASONS or (captured and value < 0): return 'expense'
    if value > 0 and (captured or reason == 'receita_ou_movimentacao'): return 'income'
    return 'unknown'
def eligible_payment(item, captured=False):
    # The parcelamento purchase is the consumption already on the statement.
    # Linking it would settle an installment and hide a charge that is still owed.
    if item.get('parcelamento_purchase'): return False
    value = amount(item.get('valor'))
    return bool(value is not None and value < 0 and item.get('fluxo') == 'saida' and role(item,captured) == 'expense' and (not captured or item.get('status') == 'confirmed'))
def effects(item, captured=False):
    value = amount(item.get('valor')) or Decimal('0'); kind = role(item,captured)
    return {'role':kind, 'income':value if kind == 'income' else Decimal('0'), 'expenses':-value if kind in {'expense','refund'} else Decimal('0')}

def duplicate_of_ledger(item, ledger_ids):
    """A confirmed capture promoted into the ledger is the same economic event."""
    link = str(item.get('linked_transaction_id') or '')
    return bool(link) and link in ledger_ids

def captures_not_in_ledger(ledger, captures):
    identities = {str(row['_id']) for row in ledger if row.get('_id') is not None}
    return [row for row in captures if not duplicate_of_ledger(row, identities)]
