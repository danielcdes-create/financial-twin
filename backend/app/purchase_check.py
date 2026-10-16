"""Read-only purchase impact assessment. All inputs remain explicit assumptions."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from pydantic import Field, model_validator, field_validator
import re

from app.commitments import advance
from app.planning import PlannedFlow, StrictModel, money, purchase_flows, simulate
from app.purchase_horizon import PurchaseHorizonInput, PurchaseScenarioInput, purchase_horizon


class PurchaseOfferExtraction(StrictModel):
    product_label: str | None = Field(default=None, max_length=120)
    total_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    installment_count: int | None = Field(default=None, ge=1, le=60)
    installment_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    ambiguity: str | None = Field(default=None, max_length=300)

    @field_validator('product_label', mode='before')
    @classmethod
    def compact_display_label(cls, value):
        # This is a display-only title, not a financial fact. Long marketplace
        # copy must not discard a valid price or weaken the monetary validators.
        if isinstance(value, str):
            text = ' '.join(value.split())
            return text if len(text) <= 120 else text[:119].rstrip() + '…'
        return value

    @field_validator('total_price', 'installment_amount', mode='before')
    @classmethod
    def normalize_visible_brl(cls, value):
        if isinstance(value,str):
            text=re.sub(r'^(?:R\$|BRL)\s*','',value.strip())
            if re.fullmatch(r'(?:\d{1,3}(?:\.\d{3})+|\d+),\d{1,2}',text):
                return text.replace('.','').replace(',','.')
            if re.fullmatch(r'\d{1,3}(?:\.\d{3})+',text):
                return text.replace('.','')
            return text
        return value


class PurchaseCheckInput(PurchaseHorizonInput):
    price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    opening_balance: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    reserve: Decimal = Field(default=Decimal('0'), ge=0, max_digits=14, decimal_places=2)
    flows: list[PlannedFlow] = Field(default_factory=list, max_length=2000)
    monthly_net_income: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    monthly_work_hours: Decimal | None = Field(default=None, gt=0, le=744, max_digits=6, decimal_places=2)
    goal_target: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    goal_saved: Decimal = Field(default=Decimal('0'), ge=0, max_digits=14, decimal_places=2)
    goal_monthly_contribution: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)

    @model_validator(mode='after')
    def coherent(self):
        if self.first_payment < self.start:
            raise ValueError('A primeira cobrança não pode anteceder o início.')
        if self.goal_target is not None and self.goal_monthly_contribution is None:
            raise ValueError('Informe a contribuição mensal da meta.')
        if self.goal_target is None and self.goal_monthly_contribution is not None:
            raise ValueError('Informe o valor alvo da meta.')
        identities = [row.source_ref or row.id for row in self.flows]
        if len(identities) != len(set(identities)):
            raise ValueError('Cada compromisso pode entrar uma única vez.')
        if any(row.date < self.start for row in self.flows):
            raise ValueError('Fluxos anteriores ao saldo inicial não podem ser contados novamente.')
        return self


def _goal_date(start: date, target: Decimal, saved: Decimal, monthly: Decimal, payments: list[dict]) -> date | None:
    balance = saved + sum((row['amount'] for row in payments if row['date'] == start), Decimal('0'))
    by_day: dict[date, Decimal] = {}
    for row in payments:
        if row['date'] > start:
            by_day[row['date']] = by_day.get(row['date'], Decimal('0')) + row['amount']
    for month in range(1, 121):
        when = advance(start, 'mensal', month)
        by_day[when] = by_day.get(when, Decimal('0')) + monthly
    # A later installment can spend a reserve that was already enough on the start day.
    reached = start if balance >= target else None
    for when in sorted(by_day):
        balance += by_day[when]
        if balance >= target:
            if reached is None:
                reached = when
        else:
            reached = None
    return reached


def assess_purchase(body: PurchaseCheckInput) -> dict:
    horizon = purchase_horizon(body.start, body.installments, body.first_payment)
    scenario = simulate(PurchaseScenarioInput(
        start=body.start, days=horizon['days'], opening_balance=body.opening_balance,
        reserve=body.reserve, purchase=body.price, installments=body.installments,
        first_payment=body.first_payment, flows=body.flows,
        assumptions=['Valores do print devem ser conferidos antes da análise.'],
    ))
    scenario['horizon'] = horizon
    gaps = list(scenario['gaps'])
    work_hours = None
    if body.monthly_net_income is not None and body.monthly_work_hours is not None:
        work_hours = format((body.price * body.monthly_work_hours / body.monthly_net_income).quantize(Decimal('.1'), rounding=ROUND_HALF_UP), '.1f')
    else:
        gaps.append('Informe renda líquida e horas trabalhadas no mês para calcular horas de trabalho.')
    goal = None
    if body.goal_target is not None and body.goal_monthly_contribution is not None:
        baseline = _goal_date(body.start, body.goal_target, body.goal_saved, body.goal_monthly_contribution, [])
        with_purchase = _goal_date(body.start, body.goal_target, body.goal_saved, body.goal_monthly_contribution, purchase_flows(body.price, body.installments, body.first_payment))
        goal = {'baseline_date': baseline.isoformat() if baseline else None,
                'purchase_date': with_purchase.isoformat() if with_purchase else None,
                'delay_days': (with_purchase - baseline).days if baseline and with_purchase else None,
                'assumption': 'A compra e os aportes mensais saem do mesmo dinheiro reservado para a meta; sem rendimentos.'}
        if baseline is None or with_purchase is None:
            gaps.append('A meta não foi alcançada no limite de dez anos destas premissas.')
    else:
        gaps.append('Informe uma meta e aporte mensal para estimar eventual adiamento.')
    return {'price': money(body.price), 'installments': body.installments, 'horizon': horizon,
            'installment_amounts': [money(-row['amount']) for row in purchase_flows(body.price, body.installments, body.first_payment)],
            'work_hours': work_hours, 'goal': goal, 'cash_scenarios': scenario,
            'gaps': gaps, 'execution': 'none',
            'assumptions': ['Horas = preço / (renda líquida mensal / horas trabalhadas no mês).',
                            'Saldo, renda, meta e compromissos informados são premissas a conferir.',
                            'A análise não cria compra, parcela, transferência ou obrigação.']}
