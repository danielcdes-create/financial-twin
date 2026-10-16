"""Current-client versions for the pre-existing synthetic API regressions."""
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from uuid import uuid4
from bson import ObjectId
from fastapi.testclient import TestClient
from app.main import review_view
from app.source_tokens import source_token

class ObservedTestClient(TestClient):
    def request(self,method,url,**kwargs):
        body=kwargs.get('json')
        db=self.app.state.db
        path=str(url).split('?')[0]
        if isinstance(body,dict):
            body=dict(body)
            match=re.fullmatch(r'/v1/transactions/([^/]+)/(classification|review)',path)
            if match and 'expected_token' not in body:
                row=db.transactions.find_one({'_id':ObjectId(match[1])}) if ObjectId.is_valid(match[1]) else None
                body['expected_token']=source_token(row)
            match=re.fullmatch(r'/v1/account-positions/([^/]+)/balance-reference',path)
            if match:
                body.setdefault('expected_token',source_token(db.account_balance_snapshots.find_one({'account_key':match[1]})))
                body.setdefault('request_id',str(uuid4()))
            match=re.fullmatch(r'/v1/reviews/([^/]+)/decision',path)
            if match and 'review_token' not in body:
                row=db.review_queue.find_one({'review_id':match[1]})
                if row:
                    view=review_view(db,row)
                    body.update({key:view[key] for key in ('review_token','source_token','candidate_token')})
            match=re.fullmatch(r'/v1/commitments/([^/]+)/payment',path)
            if match:
                commitment=db.commitments.find_one({'commitment_id':match[1]})
                source=(db.transactions.find_one({'_id':ObjectId(body['transaction_id'])}) if body.get('transaction_id') and ObjectId.is_valid(body['transaction_id'])
                    else db.provisional_transactions.find_one({'provisional_id':body.get('provisional_id')}))
                body.setdefault('expected_revision',(commitment or {}).get('revision',1))
                body.setdefault('expected_source_token',source_token(source))
            kwargs['json']=body
        return super().request(method,url,**kwargs)
