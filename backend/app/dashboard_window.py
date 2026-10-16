"""Fetch only windows consumed by dashboard cards, preserving global freshness."""
from datetime import datetime,timezone,timedelta
from app.financial_time import day_start,today,local_date
from app.economic_period import economic_instant

PROJECTION={'valor':1,'fluxo':1,'tipo':1,'natureza':1,'categoria':1,'categoria_sugerida':1,'subcategoria':1,'descricao_original':1,'estabelecimento_original':1,'merchant_raw':1,'data_evento':1,'competencia':1,'recorrencia':1,'transfer':1,'classification.migration_reason':1,'linked_transaction_id':1}

def snapshot_for(db,windows):
    from app.main import period_start,shift_months,normalized_datetime
    latest=db.transactions.find_one({'data_evento':{'$type':'date'}},{'data_evento':1},sort=[('data_evento',-1)])
    captured=db.provisional_transactions.find_one({'status':'confirmed','data_evento':{'$type':'date'}},{'data_evento':1},sort=[('data_evento',-1)])
    timestamps=[normalized_datetime(row['data_evento']) for row in (latest,captured) if row]
    competence_query={'competencia':{'$regex':r'^\d{4}-(0[1-9]|1[0-2])$'}}
    economic_latest=db.transactions.find_one(competence_query,{'competencia':1},sort=[('competencia',-1)])
    economic_capture=db.provisional_transactions.find_one({'status':'confirmed',**competence_query},{'competencia':1},sort=[('competencia',-1)])
    economic_timestamps=[economic_instant(row) for row in (economic_latest,economic_capture) if row and economic_instant(row)]
    timestamps.extend(economic_timestamps)
    anchor=max(timestamps,default=datetime.now(timezone.utc));ranges=[]
    for period,start,end in windows:
        beginning=period_start(anchor,period,start)
        ending=day_start(end+timedelta(days=1)) if end else anchor+timedelta(microseconds=1)
        if period!='all':
            if start or end:beginning=beginning-(ending-beginning)
            else:beginning=shift_months(beginning,-12 if period=='year' else -{'month':1,'3m':3,'6m':6,'12m':12}.get(period,1))
        ranges.append({'data_evento':{'$gte':beginning,'$lt':ending}})
        ranges.append({'competencia':{'$gte':'0001-01' if beginning.year==1 else local_date(beginning).strftime('%Y-%m'),'$lte':local_date(ending-timedelta(microseconds=1)).strftime('%Y-%m')}})
    query={'$or':ranges}
    txs=list(db.transactions.find(query,PROJECTION));captures=list(db.provisional_transactions.find({'status':'confirmed',**query},PROJECTION))
    return (txs,captures,{'legacy':latest.get('data_evento') if latest else None,'captured':captured.get('data_evento') if captured else None,'anchor':anchor,'economic_anchor':max(economic_timestamps,default=None)})
