"""Give older domain tests the versions a current editor must first observe.

Dedicated concurrency tests construct and retain their own stale tokens.
"""
from bson import ObjectId
from app.source_tokens import source_token
from app.main import review_view

def observed_update(update,db,identity):
    if update.expected_token is not None:return update
    row=db.transactions.find_one({'_id':ObjectId(identity)}) if ObjectId.is_valid(identity) else None
    return update.model_copy(update={'expected_token':source_token(row)})

def observed_review(decision,db,identity):
    if decision.review_token is not None:return decision
    row=db.review_queue.find_one({'review_id':identity})
    if not row:return decision
    view=review_view(db,row)
    return decision.model_copy(update={key:view[key] for key in ('review_token','source_token','candidate_token')})

def observed_mock_payment(body,db,item):
    from decimal import Decimal
    from bson.decimal128 import Decimal128
    document=(db.transactions.find_one.return_value if body.transaction_id else db.provisional_transactions.find_one.return_value)
    if isinstance(document.get('valor'),Decimal):document['valor']=Decimal128(document['valor'])
    return body.model_copy(update={'expected_revision':item.get('revision',1),'expected_source_token':source_token(document)})
