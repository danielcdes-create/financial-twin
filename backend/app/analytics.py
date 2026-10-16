from decimal import Decimal
from app.economic_policy import EXPENSE_REASONS, amount as decimal_amount, effects, duplicate_of_ledger
from app.category_filter import display_category

def calculate_financial_totals(transactions,confirmed_provisionals=()):
    income=Decimal('0');expenses=Decimal('0');categories={}
    identities={str(item['_id']) for item in transactions if item.get('_id') is not None}
    for captured, records in ((False,transactions),(True,confirmed_provisionals)):
        for item in records:
            if captured and duplicate_of_ledger(item, identities): continue
            result=effects(item,captured)
            income+=result['income'];expenses+=result['expenses']
            if result['role'] in {'expense','refund'}:
                category=display_category(item.get('categoria_sugerida' if captured else 'categoria'))
                categories[category]=categories.get(category,Decimal('0'))+result['expenses']
    return {'income':income,'expenses':expenses,'net_flow':income-expenses,'categories':categories}
