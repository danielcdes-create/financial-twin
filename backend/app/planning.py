"""Pure, cent-exact decision and liquidity engines. No ledger writes."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from app.commitments import advance

class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

Money=Decimal
def money(value): return format(Decimal(str(value)).quantize(Decimal('.01')),'.2f')

class PlannedFlow(StrictModel):
    id:str=Field(min_length=1,max_length=120)
    date:date
    amount:Money=Field(max_digits=14,decimal_places=2)
    label:str=Field(min_length=1,max_length=160)
    account_key:str|None=None
    certainty:Literal['confirmed','assumed']='assumed'
    source_ref:str|None=None

class ScenarioInput(StrictModel):
    start:date
    days:int=Field(default=90,ge=1,le=90)
    opening_balance:Money|None=Field(default=None,max_digits=14,decimal_places=2)
    reserve:Money=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    purchase:Money=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    installments:int=Field(default=1,ge=1,le=60)
    first_payment:date
    wait_days:int=Field(default=30,ge=1,le=365)
    reduced_purchase:Money|None=Field(default=None,ge=0,max_digits=14,decimal_places=2)
    flows:list[PlannedFlow]=Field(default_factory=list,max_length=2000)
    assumptions:list[str]=Field(default_factory=list,max_length=30)

    @model_validator(mode='after')
    def coherent(self):
        if self.first_payment<self.start: raise ValueError('A primeira cobrança deve estar no horizonte ou depois dele.')
        if self.reduced_purchase is not None and self.reduced_purchase>self.purchase: raise ValueError('A compra reduzida não pode exceder a original.')
        identities=[row.source_ref or row.id for row in self.flows]
        if len(set(identities))!=len(identities): raise ValueError('Fluxos repetidos: cada fonte pode entrar uma vez.')
        if any(row.date<self.start for row in self.flows):raise ValueError('Fluxo anterior ao início: ajuste o saldo inicial, sem contar novamente o passado.')
        return self

def purchase_flows(total,count,first):
    cents=int(total*100);base,remainder=divmod(cents,count)
    return [{'date':advance(first,'mensal',i),'amount':-Decimal(base+(i<remainder))/100,'label':f'Parcela {i+1}/{count}','id':f'purchase:{i+1}'} for i in range(count)]

def simulate(body:ScenarioInput):
    assumptions=list(body.assumptions)+['Saldo inicial e fluxos informados são premissas; previsão não é garantia.','Datas de parcelas representam pagamento em caixa; para cartão, informe o vencimento da fatura.']
    if body.opening_balance is None:
        return {'status':'insufficient_data','gaps':['Informe saldo inicial conhecido ou uma premissa explícita.'],'scenarios':[],'assumptions':assumptions}
    reduced=body.reduced_purchase if body.reduced_purchase is not None else body.purchase*Decimal('.8')
    definitions=[('baseline',Decimal('0'),body.first_payment),('buy_now',body.purchase,body.first_payment),('wait',body.purchase,body.first_payment+timedelta(days=body.wait_days)),('reduce',reduced.quantize(Decimal('.01')),body.first_payment)]
    scenarios=[];end=body.start+timedelta(days=body.days-1)
    for name,total,first in definitions:
        additions=purchase_flows(total,body.installments,first) if total else []
        flows=[row.model_dump() for row in body.flows]+additions
        by_day={}
        for row in flows:
            by_day.setdefault(row['date'],[]).append(row)
        balance=body.opening_balance;curve=[];minimum=balance;breach=None
        for offset in range(body.days):
            day=body.start+timedelta(days=offset);events=by_day.get(day,[])
            opening=balance;balance+=sum((row['amount'] for row in events),Decimal('0'));minimum=min(minimum,balance)
            if breach is None and balance<body.reserve:breach=day.isoformat()
            curve.append({'date':day.isoformat(),'opening':money(opening),'closing':money(balance),'below_reserve':balance<body.reserve,'events':[{'id':r['id'],'label':r['label'],'amount':money(r['amount'])} for r in events]})
        scenarios.append({'name':name,'purchase_total':money(total),'minimum_balance':money(minimum),'closing_balance':money(balance),'first_reserve_breach':breach,'outside_horizon_total':money(-sum((r['amount'] for r in additions if r['date']>end),Decimal('0'))),'curve':curve})
    return {'status':'scenario','gaps':[], 'excluded_flows':[row.model_dump(mode='json') for row in body.flows if row.date>end],'assumptions':assumptions,'scenarios':scenarios,'execution':'none'}

class LiquidityAccount(StrictModel):
    key:str=Field(min_length=1,max_length=80)
    balance:Money|None=Field(default=None,max_digits=14,decimal_places=2)
    reserve:Money=Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)
    transferable:bool=True
    transfer_limit:Money|None=Field(default=None,ge=0,max_digits=14,decimal_places=2)

class LiquidityInput(StrictModel):
    start:date
    days:int=Field(default=30,ge=1,le=90)
    accounts:list[LiquidityAccount]=Field(min_length=1,max_length=30)
    flows:list[PlannedFlow]=Field(default_factory=list,max_length=2000)
    @model_validator(mode='after')
    def coherent(self):
        keys=[row.key for row in self.accounts]
        if len(keys)!=len(set(keys)):raise ValueError('Conta repetida.')
        if len({r.source_ref or r.id for r in self.flows})!=len(self.flows):raise ValueError('Fonte de fluxo repetida.')
        if any(row.account_key not in keys for row in self.flows):raise ValueError('Cada fluxo exige uma conta selecionada.')
        if any(row.date<self.start for row in self.flows):raise ValueError('Fluxo anterior ao saldo inicial não pode entrar novamente.')
        return self

def plan_liquidity(body:LiquidityInput):
    known={row.key:row for row in body.accounts if row.balance is not None}
    balances={key:row.balance for key,row in known.items()};gaps=[f'Saldo desconhecido: {row.key}' for row in body.accounts if row.balance is None]
    proposals=[];uncovered=[];end=body.start+timedelta(days=body.days-1)
    for offset in range(body.days):
        day=body.start+timedelta(days=offset)
        for row in body.flows:
            if row.date==day and row.account_key in balances:balances[row.account_key]+=row.amount
        day_limits={key:row.transfer_limit for key,row in known.items()}
        for target in sorted(known):
            deficit=max(Decimal('0'),known[target].reserve-balances[target])
            if not deficit:continue
            for source in sorted(known):
                account=known[source]
                if source==target or not account.transferable or source=='binance':continue
                # Preserve the donor's worst future known balance, including later obligations.
                future=Decimal('0');worst=Decimal('0')
                for future_day in range(1,(end-day).days+1):
                    future+=sum((f.amount for f in body.flows if f.account_key==source and f.date==day+timedelta(days=future_day) and (f.amount<0 or f.certainty=='confirmed')),Decimal('0'));worst=min(worst,future)
                excess=max(Decimal('0'),balances[source]+worst-account.reserve)
                quantity=min(deficit,excess,day_limits[source] if day_limits[source] is not None else excess)
                if quantity<=0:continue
                balances[source]-=quantity;balances[target]+=quantity;deficit-=quantity
                if day_limits[source] is not None:day_limits[source]-=quantity
                proposals.append({'date':day.isoformat(),'from_account_key':source,'to_account_key':target,'amount':money(quantity),'reason':'Cobrir déficit preservando a reserva e os débitos futuros conhecidos da origem.'})
                if not deficit:break
            if deficit:uncovered.append({'date':day.isoformat(),'account_key':target,'amount':money(deficit)})
    return {'proposals':proposals,'uncovered':uncovered,'gaps':gaps,'closing_balances':{key:money(value) for key,value in balances.items()},'assumptions':['Saldos e fluxos precisam ser conferidos. Recomendações não executam transferências.','Receitas futuras são premissas, não garantia de disponibilidade.'],'execution':'none'}
