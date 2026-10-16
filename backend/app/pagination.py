"""Stable, filter-bound cursor pages for workflow queues."""
import base64, hashlib, json
from bson import ObjectId
from fastapi import HTTPException
from app.financial_time import utc_instant

def page(collection, query, field, limit=100, cursor=None):
    signature=hashlib.sha256(json.dumps(query,sort_keys=True,default=str).encode()).hexdigest()[:20]
    bounded=query
    if cursor:
        try:
            value=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
            if value['filter']!=signature or value['field']!=field: raise ValueError()
            stamp=utc_instant(value['date']);key=ObjectId(value['id']) if value['object_id'] else value['id']
        except Exception as exc: raise HTTPException(422,'Cursor inválido para esta consulta.') from exc
        bounded={'$and':[query,{'$or':[{field:{'$lt':stamp}},{field:stamp,'_id':{'$lt':key}}]}]}
    rows=list(collection.find(bounded).sort([(field,-1),('_id',-1)]).limit(limit+1 if limit else 0))
    more=bool(limit and len(rows)>limit); rows=rows[:limit] if limit else rows
    token=None
    if more:
        last=rows[-1]
        token=base64.urlsafe_b64encode(json.dumps({'date':utc_instant(last[field]).isoformat(),'id':str(last['_id']),'object_id':isinstance(last['_id'],ObjectId),'field':field,'filter':signature}).encode()).decode().rstrip('=')
    return rows,{'total':collection.count_documents(query),'has_more':more,'next_cursor':token,'limit':limit}
