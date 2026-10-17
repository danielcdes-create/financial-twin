"""Evidence-backed product workflows. All financial ledger access is read-only.

Writes are additive, atomic and revision checked. No bank execution or messages.
"""
from app.commitment_estimates import effective_commitments

import asyncio, hashlib, json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Query, UploadFile, File
from pydantic import Field, model_validator
from pymongo.errors import DuplicateKeyError
from app.auth import require_session
from app.ai_admission import run_ai
from app.atomic import mongo_transaction, mongo_snapshot, session_database
from app.financial_time import date_filter, today, local_date
from app.economic_policy import amount, effects, role
from app.category_filter import matches_category
from app.planning import StrictModel, ScenarioInput, LiquidityInput, simulate, plan_liquidity, money
from app.purchase_check import PurchaseCheckInput, assess_purchase
from app.purchase_horizon import purchase_horizon
from app.services.ai.xai.provider import XAIVisionProvider, XAIError, public_xai_error
from app.pagination import page
from app.account_positions import account_positions, account_key_for_legacy
from app.commitments import schedule_from, as_date, historical_occurrence_conflicts
from app.future_commitments import effective_documents
from app.economic_period import economic_rows, economic_basis
from app.obligation_policy import OPEN_OBLIGATION_STATUSES

router=APIRouter(prefix='/v1/insights',dependencies=[Depends(require_session)])
def database(request:Request):
    db=request.app.state.db
    if db is None:raise HTTPException(503,'Banco indisponível.')
    return db
def safe(value):
    if isinstance(value,dict):return {('id' if k=='_id' else k):safe(v) for k,v in value.items()}
    if isinstance(value,list):return [safe(v) for v in value]
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,ObjectId):return str(value)
    if isinstance(value,Decimal) or hasattr(value,'to_decimal'):return money(amount(value))
    return value
def audit(db,event,identity,before,after):
    db.audit_log.insert_one({'event':event,'workflow_id':identity,'actor':'owner','before':safe(before),'after':safe(after),'created_at':datetime.now(timezone.utc)})
def period(start,end):
    if start>end or (end-start).days>366:raise HTTPException(422,'Escolha um período de até 367 dias, em ordem cronológica.')
    return date_filter(start,end)
def rows_for(db,start,end):
    period(start,end)
    ledger=list(economic_rows(db.transactions,start,end))
    linked={str(row['_id']) for row in ledger}
    captures=[row for row in economic_rows(db.provisional_transactions,start,end,{'status':'confirmed'}) if str(row.get('linked_transaction_id') or '') not in linked]
    return [(False,row) for row in ledger]+[(True,row) for row in captures]
def source_id(captured,row):return ('capture:'+row['provisional_id']) if captured else ('ledger:'+str(row['_id']))
def source(db,reference):
    kind,_,identity=reference.partition(':')
    if kind=='ledger' and ObjectId.is_valid(identity):row=db.transactions.find_one({'_id':ObjectId(identity)})
    elif kind=='capture':row=db.provisional_transactions.find_one({'provisional_id':identity,'status':'confirmed'})
    else:raise HTTPException(422,'Referência deve ser ledger:<id> ou capture:<id>.')
    if not row:raise HTTPException(404,'Movimentação de referência não encontrada.')
    return kind=='capture',row

@router.get('/closure')
def closure(start:date,end:date,db=Depends(database)):
    period(start,end)
    with mongo_snapshot(db) as (reading,consistency):
        rows=rows_for(reading,start,end);positions=account_positions(reading)
        totals={'income':Decimal('0'),'expenses':Decimal('0')};unknown=0;refunds=0
        for captured,row in rows:
            effect=effects(row,captured)
            for key in totals:totals[key]+=effect[key]
            unknown+=effect['role']=='unknown';refunds+=effect['role']=='refund'
        pending=reading.review_queue.count_documents({'status':'pending'})
        missing_account=sum(1 for captured,row in rows if captured and not row.get('account_profile_id') and role(row,True)!='neutral')
        gaps=[]
        if missing_account:gaps.append(f'{missing_account} capturas confirmadas sem conta atribuída no período.')
        if unknown:gaps.append(f'{unknown} movimentos com efeito econômico não determinado.')
        if pending:gaps.append(f'{pending} revisões aguardam decisão; abra Revisão para percorrer todas.')
        if any(p['balance'] is None for p in positions):gaps.append('Existem contas sem referência de saldo.')
        gaps.append('Cobertura integral dos extratos não foi declarada; ausência de pendências não prova fechamento completo.')
        preview=sorted(rows,key=lambda pair:str(pair[1].get('data_evento')),reverse=True)[:100]
        return {'start':start,'end':end,'read_consistency':consistency,'coverage':'incomplete','economic_period_basis':'competencia_declarada; sem competencia: data_evento_brasilia','cash_period_basis':'data_evento_brasilia','gaps':gaps,'income':money(totals['income']),'expenses':money(totals['expenses']),'net_flow':money(totals['income']-totals['expenses']),'refund_count':refunds,'movement_count':len(rows),'accounts':positions,'trail_preview':[{'ref':source_id(c,r),'label':r.get('merchant_name') or r.get('estabelecimento_original') or r.get('merchant_raw') or 'Movimentação','date':safe(r.get('data_evento')),'competencia':r.get('competencia'),'economic_basis':economic_basis(r),'amount':money(amount(r.get('valor')) or 0),'role':role(r,c)} for c,r in preview],'preview_limit':100,'preview_truncated':len(rows)>100,'all_movements_path':'/v1/movements?cursor_mode=true'}

@router.get('/trail')
def trail(reference:str,db=Depends(database)):
    with mongo_snapshot(db) as (reading,consistency):
        result=_trail(reference,reading)
    return {**result,'read_consistency':consistency,'read_at':datetime.now(timezone.utc).isoformat()}

def _trail(reference,db):
    captured,row=source(db,reference);nodes=[];edges=[];missing=[]
    def node(kind,doc,identity=None):
        key=f'{kind}:{identity or doc.get("_id")}';nodes.append({'ref':key,'kind':kind,'data':safe(doc)});return key
    origin=node('capture' if captured else 'ledger',row,row.get('provisional_id') if captured else None)
    refs=[]
    if row.get('event_id'):refs.append(('raw_event','raw_events',{'event_id':row['event_id']}))
    if (row.get('source') or {}).get('source_record_id'):refs.append(('source_record','source_records',{'_id':row['source']['source_record_id']}))
    if row.get('bill_id'):refs.append(('bill','credit_card_bills',{'_id':row['bill_id']}))
    for kind,collection,query in refs:
        linked=db[collection].find_one(query)
        if linked:edges.append({'from':origin,'to':node(kind,linked),'relation':'source'})
        else:missing.append({'kind':kind,'query':safe(query)})
    query={'provisional_id':row['provisional_id']} if captured else {'transaction_id':row['_id']}
    for evidence in db.transaction_evidence.find({**query,'status':{'$ne':'reversed'}}):
        evidence_node=node('evidence',evidence);edges.append({'from':evidence_node,'to':origin,'relation':evidence.get('decision','linked')})
        event=db.raw_events.find_one({'event_id':evidence.get('event_id')})
        if event:edges.append({'from':node('raw_event',event),'to':evidence_node,'relation':'supports'})
    for commitment in db.commitments.find({f'linked_transactions.{"provisional_id" if captured else "transaction_id"}':row.get('provisional_id') if captured else str(row['_id'])}):
        edges.append({'from':node('commitment',commitment),'to':origin,'relation':'settled_by'})
    if not captured:
        for reconciliation in db.reconciliations.find({'$or':[{'origem.entity':'transactions','origem.id':row['_id']},{'destino.entity':'transactions','destino.id':row['_id']},{'transaction_id':row['_id']}]}):
            target=node('reconciliation',reconciliation);edges.append({'from':origin,'to':target,'relation':'reconciles'})
            for endpoint in [reconciliation.get('origem',{}),reconciliation.get('destino',{}),{'entity':'obligations','id':reconciliation.get('obligation_id')}]:
                collection=endpoint.get('entity')
                if collection not in {'obligations','credit_card_bills','installment_plans'} or not endpoint.get('id'):continue
                linked=db[collection].find_one({'_id':endpoint['id']})
                if linked:edges.append({'from':target,'to':node(collection,linked),'relation':'settles'})
                else:missing.append({'kind':collection,'id':str(endpoint['id'])})
    return {'reference':reference,'economic_role':role(row,captured),'nodes':nodes,'edges':edges,'missing':missing,'explanation':'Evidências comprovam; obrigações preveem; somente movimentos efetivos afetam os totais conforme a natureza.'}

class Undo(StrictModel):
    request_id:UUID
    expected_revision:int=Field(ge=1)
    reason:str=Field(min_length=5,max_length=500)

@router.post('/evidence/{event_id}/undo')
def undo_evidence(event_id:str,body:Undo,db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session)
        from app.movement_insert_lock import lock_movement_insert
        lock_movement_insert(writing)
        previous=writing.insight_commands.find_one({'_id':str(body.request_id)})
        if previous:
            if previous.get('event_id')!=event_id or previous.get('reason')!=body.reason:raise HTTPException(409,'Chave usada para outra decisão.')
            return previous['result']
        evidence=writing.transaction_evidence.find_one({'event_id':event_id,'status':{'$ne':'reversed'}})
        if not evidence:raise HTTPException(404,'Vínculo ativo não encontrado.')
        if evidence.get('revision',1)!=body.expected_revision:raise HTTPException(409,'O vínculo mudou. Recarregue a trilha.')
        key='provisional_id' if evidence.get('target_type')=='provisional' else 'transaction_id';identity=str(evidence.get(key))
        archive_key=('capture' if key=='provisional_id' else 'ledger')+':'+identity
        if writing.deleted_movements.find_one({'_id':archive_key}):
            raise HTTPException(409,'O registro foi excluído do Extrato. O histórico do vínculo permanece preservado.')
        if writing.commitments.find_one({f'linked_transactions.{key}':identity}):raise HTTPException(409,'O vínculo sustenta um compromisso. Desvincule o pagamento primeiro.')
        raw=writing.raw_events.find_one({'event_id':event_id})
        if not raw:raise HTTPException(409,'Evento original ausente; revisão manual necessária.')
        parsed=raw.get('parsed_payload') or {};now=datetime.now(timezone.utc)
        draft=writing.provisional_transactions.find_one({'event_id':event_id})
        if not draft:
            from app.financial_time import utc_instant
            from bson.decimal128 import Decimal128
            value=amount(parsed.get('amount'))
            if value is None:raise HTTPException(409,'Evento não contém valor para reabrir captura.')
            draft={'provisional_id':str(body.request_id),'event_id':event_id,'data_evento':utc_instant(parsed.get('event_timestamp') or raw['captured_at']),'valor':Decimal128(str(-abs(value) if parsed.get('direction','expense')=='expense' else abs(value))),'fluxo':'saida' if parsed.get('direction','expense')=='expense' else 'entrada','merchant_raw':parsed.get('merchant_raw') or parsed.get('merchant') or '', 'status':'provisional','revision':1,'created_at':now}
            writing.provisional_transactions.insert_one(draft)
        else:writing.provisional_transactions.update_one({'_id':draft['_id']},{'$set':{'status':'provisional','updated_at':now},'$inc':{'revision':1}})
        writing.transaction_evidence.update_one({'_id':evidence['_id']},{'$set':{'status':'reversed','revision':body.expected_revision+1,'reversed_at':now,'reversal_reason':body.reason}})
        writing.raw_events.update_one({'event_id':event_id},{'$set':{'processing_status':'provisional','linked_transaction_id':None}})
        result={'event_id':event_id,'status':'reopened','provisional_id':draft['provisional_id']}
        audit(writing,'evidence_reversed',event_id,evidence,result)
        writing.insight_commands.insert_one({'_id':str(body.request_id),'event_id':event_id,'reason':body.reason,'result':result,'created_at':now})
        return result

@router.get('/planning-context')
def planning_context(start:date,end:date,db=Depends(database)):
    period(start,end)
    return _planning_context(start,end,db)

@router.get('/purchase-context')
def purchase_context(start:date,first_payment:date,installments:int=Query(default=1,ge=1,le=60),db=Depends(database)):
    try:horizon=purchase_horizon(start,installments,first_payment)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    return {**_planning_context(start,date.fromisoformat(horizon['end']),db,purchase=True),'horizon':horizon}

_OBLIGATION_LABELS = {
    'fatura_cartao': 'Fatura do cartão',
    'fatura': 'Fatura do cartão',
    'financiamento': 'Financiamento',
    'emprestimo': 'Empréstimo',
    'imposto': 'Imposto / tributo',
    'boleto': 'Boleto',
    'obrigacao': 'Obrigação',
}

def _obligation_display_label(obligation: dict, due: date) -> str:
    raw_type = str(obligation.get('tipo') or 'Obrigação').strip()
    kind_label = _OBLIGATION_LABELS.get(raw_type.lower(), raw_type.replace('_', ' ').capitalize())
    comp = str(obligation.get('competencia') or '').strip()
    if len(comp) == 7 and comp[4] == '-':
        period_label = f'{comp[5:7]}/{comp[0:4]}'
    else:
        period_label = due.strftime('%d/%m/%Y')
    return f'{kind_label} · {period_label}'

def _planning_context(start:date,end:date,db,purchase:bool=False):
    flows=[];excluded=[];suggestions=[]
    with mongo_snapshot(db) as (reading,consistency):
        positions=account_positions(reading)
        account_keys={p['legacy_account_id']:p['key'] for p in positions if p.get('legacy_account_id')}
        excluded.extend({'ref':'profile:'+p['key'],'label':p.get('apelido') or p.get('instituicao') or p['key'],'reason':'Conta encerrada ou arquivada não é oferecida para novas movimentações.'} for p in positions if p.get('archived') or p.get('status')=='encerrada')
        positions=[p for p in positions if not p.get('archived') and p.get('status')!='encerrada']
        for commitment in effective_commitments(reading, reading.commitments.find({'status':'ativo'})):
            if historical_occurrence_conflicts(commitment):
                excluded.append({'ref':'commitment:'+commitment['commitment_id'],'label':commitment.get('name') or 'Compromisso','reason':'Pagamento histórico contradiz a próxima ocorrência. Confira o histórico e a agenda antes de projetar caixa.'})
                continue
            # Card purchases stay out of the shared planning context so a bill is not
            # added twice. Cabe na minha vida lists each card commitment on its own
            # due date and omits the consolidated invoice instead.
            if commitment.get('payment_method')=='cartao' and not purchase:
                excluded.append({'ref':'commitment:'+commitment['commitment_id'],'label':commitment.get('name') or 'Compromisso','reason':'Informe o vencimento da fatura para prever caixa; não duplicar compra e fatura.'});continue
            reference=str(commitment.get('account_id') or '')
            key=reference.removeprefix('profile:') if reference.startswith('profile:') else account_keys.get(reference)
            # Old weekly subscriptions can need more than 600 occurrences to
            # reach a multiyear purchase horizon. Do not silently truncate them.
            next_due=start
            count=max(600,(end-next_due).days//7+2) if commitment['frequency']=='semanal' else max(600,(end.year-next_due.year)*12+end.month-next_due.month+2)
            for occurrence in schedule_from(commitment,start,count):
                due=date.fromisoformat(occurrence['due_date'])
                if start<=due<=end:
                    identity=f'commitment:{commitment["commitment_id"]}:{due}'
                    flows.append({'id':identity,'source_ref':identity,'date':due.isoformat(),'amount':money(-Decimal(str(occurrence['amount']))),'label':commitment['name'],'account_key':key,'certainty':'assumed'})
        # Imported obligations are offered once and require explicit overlap/account review.
        # Due dates in the legacy schema are civil dates stored at UTC midnight.
        for obligation in effective_documents(reading, 'obligation', reading.obligations.find({'status':{'$in':list(OPEN_OBLIGATION_STATUSES)}})):
            if obligation.get('future_excluded'): continue
            if purchase and str(obligation.get('tipo') or '').lower() in {'fatura_cartao', 'fatura'}: continue
            if not obligation.get('data_vencimento'):continue
            due=as_date(obligation['data_vencimento']);value=amount(obligation.get('saldo_aberto'))
            if not value or value<=0 or due>end:continue
            identity='obligation:'+str(obligation['_id'])
            suggestions.append({'id':identity,'source_ref':identity,'date':max(start,due).isoformat(),'original_due_date':due.isoformat(),'overdue':due<start,'amount':money(-value),'label':_obligation_display_label(obligation,due),'account_key':None,'certainty':'assumed'})
        return {'accounts':positions,'flows':flows,'suggested_flows':suggestions,'excluded_sources':excluded,'unmapped_obligations':len(suggestions),'gaps':['Obrigações importadas só entram após seleção e conferência de sobreposição com compromissos. Faturas entram pelo saldo da obrigação, sem somar novamente as compras.','Fluxos previstos não comprovam pagamentos.'],'read_consistency':consistency,'opening_balance_verified':False}

@router.post('/simulate')
def simulation(body:ScenarioInput):return simulate(body)
@router.post('/purchase-check')
def purchase_check(body:PurchaseCheckInput):return assess_purchase(body)

@router.post('/purchase-scan')
async def purchase_scan(request:Request,file:UploadFile=File(...)):
    # A shopping screenshot is analyzed in memory; it is never added to evidence or the ledger.
    data=await file.read(10*1024*1024+1)
    if len(data)>10*1024*1024:raise HTTPException(413,'Print excede 10 MiB.')
    mime='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None
    if mime is None:raise HTTPException(415,'Envie um print PNG ou JPEG.')
    try:offer=await run_ai(request,XAIVisionProvider().extract_purchase_offer,data,mime)
    except XAIError as exc:raise HTTPException(503,public_xai_error(exc)) from exc
    return {'product_label':offer.product_label,'total_price':money(offer.total_price) if offer.total_price is not None else None,
            'installment_count':offer.installment_count,'installment_amount':money(offer.installment_amount) if offer.installment_amount is not None else None,
            'ambiguity':offer.ambiguity,'needs_review':True,'persisted':False}
@router.post('/liquidity')
def liquidity(body:LiquidityInput):return plan_liquidity(body)

class CaseCreate(StrictModel):
    request_id:UUID
    title:str=Field(min_length=3,max_length=160)
    kind:Literal['post_cancellation','unexpected_amount','extra_installment','missing_refund','other']
    expected_amount:Decimal=Field(ge=0,max_digits=14,decimal_places=2)
    expected_date:date|None=None
    source_refs:list[str]=Field(default_factory=list,max_length=30)
    notes:str=Field(default='',max_length=4000)

def create_workflow(db,collection,body,event):
    identity=str(body.request_id);payload=body.model_dump(mode='json');fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    existing=db[collection].find_one({'_id':identity})
    if existing:
        if existing['request_hash']!=fingerprint:raise HTTPException(409,'Identificador reutilizado com outro conteúdo.')
        return safe(existing)
    now=datetime.now(timezone.utc);item={'_id':identity,**payload,'request_hash':fingerprint,'revision':1,'schema_version':1,'created_at':now,'updated_at':now,'status':'open' if collection=='financial_cases' else 'active'}
    db[collection].insert_one(item);audit(db,event,identity,None,item);return safe(item)

@router.post('/cases',status_code=201)
def create_case(body:CaseCreate,db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session)
        for ref in body.source_refs:
            captured,row=source(writing,ref)
            collection=writing.provisional_transactions if captured else writing.transactions
            collection.update_one({'_id':row['_id']},{'$set':{'updated_at':datetime.now(timezone.utc)}})
        return create_workflow(writing,'financial_cases',body,'financial_case_created')

@router.get('/cases')
def cases(limit:int=Query(50,ge=1,le=100),cursor:str|None=None,db=Depends(database)):
    rows,info=page(db.financial_cases,{},'created_at',limit,cursor);return {'items':safe(rows),**info}

class CaseUpdate(StrictModel):
    revision:int=Field(ge=1)
    status:Literal['open','awaiting_refund','resolved','dismissed']
    refund_ref:str|None=None
    notes:str=Field(default='',max_length=4000)

def changed_document(db,collection,identity,revision,changes,event):
    before=db[collection].find_one({'_id':identity})
    if not before:raise HTTPException(404,'Registro não encontrado.')
    if before['revision']!=revision:raise HTTPException(409,'O registro mudou. Recarregue.')
    after={**changes,'revision':revision+1,'updated_at':datetime.now(timezone.utc)}
    result=db[collection].update_one({'_id':identity,'revision':revision},{'$set':after})
    if result.matched_count!=1:raise HTTPException(409,'Conflito de revisão. Recarregue.')
    audit(db,event,identity,before,{**before,**after});return safe({**before,**after})

@router.patch('/cases/{identity}')
def update_case(identity:str,body:CaseUpdate,db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session);changes=body.model_dump(exclude={'revision'})
        current=writing.financial_cases.find_one({'_id':identity})
        if not current:raise HTTPException(404,'Caso não encontrado.')
        if current['revision']!=body.revision:raise HTTPException(409,'O caso mudou. Recarregue.')
        if current['status']=='resolved':
            if all(current.get(k)==v for k,v in changes.items()):return safe(current)
            raise HTTPException(409,'Caso resolvido é imutável; abra revisão em novo caso sem contar o mesmo crédito.')
        if body.status=='resolved':
            if not body.refund_ref:raise HTTPException(422,'Selecione o crédito recebido para resolver.')
            captured,tx=source(writing,body.refund_ref);value=amount(tx.get('valor'))
            if value is None or value<=0 or role(tx,captured)!='refund':raise HTTPException(422,'A fonte precisa ser um estorno/reembolso positivo confirmado.')
            collection=writing.provisional_transactions if captured else writing.transactions
            collection.update_one({'_id':tx['_id']},{'$set':{'updated_at':datetime.now(timezone.utc)}})
            claim=writing.refund_claims.find_one({'_id':body.refund_ref})
            if claim and claim['case_id']!=identity:raise HTTPException(409,'Este crédito já resolve outro caso.')
            if not claim:
                try:writing.refund_claims.insert_one({'_id':body.refund_ref,'case_id':identity})
                except DuplicateKeyError:raise HTTPException(409,'Este crédito já foi vinculado.')
            changes['recovered_amount']=money(value);changes['resolved_at']=datetime.now(timezone.utc)
        return changed_document(writing,'financial_cases',identity,body.revision,changes,'financial_case_updated')

@router.get('/cases/{identity}/dossier')
def dossier(identity:str,db=Depends(database)):
    item=db.financial_cases.find_one({'_id':identity})
    if not item:raise HTTPException(404,'Caso não encontrado.')
    documents=[];missing=[]
    for ref in list(dict.fromkeys([*item.get('source_refs',[]),*([item['refund_ref']] if item.get('refund_ref') else [])])):
        try:c,row=source(db,ref);documents.append({'reference':ref,'role':role(row,c),'document':safe(row)})
        except HTTPException:missing.append(ref)
    history=list(db.audit_log.find({'workflow_id':identity}).sort('created_at',1))
    text=f'# {item["title"]}\n\nEstado: {item["status"]}\nValor esperado: R$ {item["expected_amount"]}\nValor recuperado comprovado: R$ {item.get("recovered_amount","0.00")}\n\n{item.get("notes","")}\n\nFontes: '+', '.join(d['reference'] for d in documents)
    for entry in documents:
        row=entry['document']
        text+=f'\n\n## Evidência: {entry["reference"]}\nData: {row.get("data_evento", "não informada")}\nValor: {row.get("valor", "não informado")}\nDescrição: {row.get("descricao_original") or row.get("merchant_raw") or "não informada"}\nPapel econômico: {entry["role"]}'
    text+='\n\n## Histórico\n'+'\n'.join(f'- {safe(h.get("created_at"))}: {h["event"]}' for h in history)
    if missing:text+='\n\nFontes ausentes: '+', '.join(missing)
    return {'case':safe(item),'sources':documents,'missing':missing,'history':safe(history),'markdown':text,'sent_externally':False}

@router.get('/guardian-detections')
def detections(start:date,end:date,db=Depends(database)):
    rows=rows_for(db,start,end);items=[]
    for commitment in effective_commitments(db, db.commitments.find({})):
        expected=Decimal(str(commitment['amount']))
        linked_order=sorted(commitment.get('linked_transactions',[]),key=lambda l:str(l.get('paid_date') or l.get('due_date') or ''))
        extra_ids={str(link.get('provisional_id') or link.get('transaction_id')) for link in linked_order[commitment['installment_count']:]} if commitment.get('kind')=='parcelamento' and commitment.get('installment_count') else set()
        for captured,tx in rows:
            if role(tx,captured)!='expense':continue
            # Strong identity: explicitly linked to this contract, never price alone.
            ref=source_id(captured,tx);identifier=ref.split(':',1)[1]
            related=any(link.get('provisional_id' if captured else 'transaction_id')==identifier for link in commitment.get('linked_transactions',[]))
            if not related:continue
            kind=None
            if commitment.get('status')=='cancelado' and tx.get('data_evento') and commitment.get('cancelled_at') and local_date(tx['data_evento'])>local_date(commitment['cancelled_at']):kind='post_cancellation'
            elif identifier in extra_ids:kind='extra_installment'
            elif not commitment.get('amount_estimate') and abs(amount(tx['valor']))>expected+Decimal('.02'):kind='unexpected_amount'
            if kind:items.append({'kind':kind,'title':commitment['name'],'source_refs':[ref],'expected_amount':money(expected),'actual_amount':money(abs(amount(tx['valor']))),'explanation':'Movimento explicitamente vinculado ao compromisso; confira contrato antes de abrir caso.'})
    for item in db.financial_cases.find({'status':'awaiting_refund'}):
        if item.get('expected_date') and as_date(item['expected_date'])<today():items.append({'kind':'missing_refund','case_id':str(item['_id']),'title':item['title'],'explanation':'Prazo informado venceu e o caso ainda não tem crédito conciliado.'})
    return {'items':items,'gaps':['Detecção exige vínculo explícito com o contrato. Cobranças não vinculadas precisam de revisão.'],'writes_performed':False}

class ExperimentCreate(StrictModel):
    request_id:UUID
    title:str=Field(min_length=3,max_length=160)
    category:str=Field(min_length=1,max_length=120)
    protocol:str=Field(min_length=5,max_length=2000)
    baseline_start:date
    baseline_end:date
    start:date
    end:date
    target_reduction_pct:Decimal=Field(ge=0,le=100)
    @model_validator(mode='after')
    def coherent(self):
        if self.baseline_start>self.baseline_end or self.start>self.end or self.baseline_end>=self.start:raise ValueError('Períodos devem ser ordenados e não sobrepostos.')
        if (self.end-self.start).days>90 or (self.baseline_end-self.baseline_start).days>366:raise ValueError('Experimento: até 91 dias; linha de base: até 367 dias.')
        return self

@router.post('/experiments',status_code=201)
def create_experiment(body:ExperimentCreate,db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session);existing=writing.saving_experiments.find_one({'_id':str(body.request_id)})
        if not existing:
            try:writing.experiment_locks.insert_one({'_id':'owner','experiment_id':str(body.request_id)})
            except DuplicateKeyError:raise HTTPException(409,'Conclua ou cancele o experimento ativo primeiro.')
        return create_workflow(writing,'saving_experiments',body,'saving_experiment_created')

@router.get('/experiments')
def experiments(limit:int=Query(50,ge=1,le=100),cursor:str|None=None,db=Depends(database)):
    rows,info=page(db.saving_experiments,{},'created_at',limit,cursor);return {'items':safe(rows),**info}

class ExperimentUpdate(StrictModel):
    revision:int=Field(ge=1)
    status:Literal['completed','cancelled']
    satisfaction:int|None=Field(default=None,ge=1,le=5)
    confounders:str=Field(default='',max_length=4000)

@router.patch('/experiments/{identity}')
def update_experiment(identity:str,body:ExperimentUpdate,db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session);item=writing.saving_experiments.find_one({'_id':identity})
        if not item:raise HTTPException(404,'Experimento não encontrado.')
        if item['status']!='active':raise HTTPException(409,'Experimento já encerrado.')
        if body.status=='completed' and today()<as_date(item['start']):raise HTTPException(422,'O experimento ainda não começou.')
        changes={**body.model_dump(exclude={'revision'}),'actual_end':min(today(),as_date(item['end'])).isoformat()}
        result=changed_document(writing,'saving_experiments',identity,body.revision,changes,'saving_experiment_'+body.status)
        writing.experiment_locks.delete_one({'_id':'owner','experiment_id':identity});return result

@router.get('/experiments/{identity}/report')
def experiment_report(identity:str,db=Depends(database)):
    with mongo_snapshot(db) as (reading,consistency):
        item=reading.saving_experiments.find_one({'_id':identity})
        if not item:raise HTTPException(404,'Experimento não encontrado.')
        baseline_start=as_date(item['baseline_start']);baseline_end=as_date(item['baseline_end']);start=as_date(item['start']);end=min(as_date(item['end']),today())
        if item.get('actual_end'):end=min(end,as_date(item['actual_end']))
        def spending(a,b):
            return sum((effects(row,captured)['expenses'] for captured,row in rows_for(reading,a,b) if matches_category(row.get('categoria_sugerida' if captured else 'categoria'),item['category'])),Decimal('0')) if a<=b else Decimal('0')
        base=spending(baseline_start,baseline_end);observed=spending(start,end);base_days=(baseline_end-baseline_start).days+1;days=max(0,(end-start).days+1)
        expected=base/base_days*days;difference=expected-observed
        return {'experiment':safe(item),'baseline_spending':money(base),'observed_spending':money(observed),'baseline_days':base_days,'observed_days':days,'expected_at_baseline_daily_rate':money(expected),'observed_difference':money(difference),'observed_reduction_pct':str((difference/expected*100).quantize(Decimal('.01'))) if expected>0 else None,'coverage':'not_verified','read_consistency':consistency,'interpretation':'Diferença observada, não economia causal comprovada. Compare cobertura, viagens, preços, renda e satisfação.','future_days_excluded':max(0,(as_date(item['end'])-end).days)}
