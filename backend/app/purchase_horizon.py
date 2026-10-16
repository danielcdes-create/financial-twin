"""One calendar horizon shared by purchase context and cash assessment."""
from datetime import date, timedelta

from pydantic import Field, model_validator

from app.commitments import advance
from app.planning import ScenarioInput, StrictModel

MAX_PURCHASE_DAYS = 3660


class PurchaseHorizonInput(StrictModel):
    start: date
    installments: int = Field(default=1, ge=1, le=60)
    first_payment: date

    @model_validator(mode='after')
    def valid_horizon(self):
        purchase_horizon(self.start, self.installments, self.first_payment)
        return self


class PurchaseScenarioInput(ScenarioInput):
    # Keep the general /simulate workflow's existing 90-day limit.
    days: int = Field(ge=1, le=MAX_PURCHASE_DAYS)


def purchase_horizon(start: date, installments: int, first_payment: date) -> dict:
    if first_payment < start:
        raise ValueError('A primeira cobrança não pode anteceder o início.')
    months = installments + 1
    try:
        # Preserve the existing minimum of 90 days, and cover deferred payments.
        # Also leave a month after the last payment in the "wait 30 days" scenario.
        end = max(start + timedelta(days=89), advance(first_payment, 'mensal', months),
                  advance(first_payment + timedelta(days=30), 'mensal', installments))
    except (ValueError, OverflowError) as exc:
        raise ValueError('Datas de pagamento fora do período suportado.') from exc
    days = (end - start).days + 1
    if days > MAX_PURCHASE_DAYS:
        raise ValueError('Escolha datas de pagamento com horizonte de até dez anos.')
    return {'start': start.isoformat(), 'end': end.isoformat(), 'days': days,
            'minimum_months': months, 'rule': 'installments_plus_one_month',
            'minimum_days': 90}
