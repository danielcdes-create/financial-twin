"""Cross-screen filters, economic drilldowns and persisted classification."""
import copy
import re
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from app.dashboard_window import PROJECTION
from app.main import _dashboard, cursor_movements, transactions, with_merchant_name, with_merchant_names
from app.insights import experiment_report
from app.financial_time import day_start
from test_record_review import RecordDB
from test_uncategorized_movements import CategoryCollection


class FilterCollection(CategoryCollection):
    def find_one(self, query, projection=None, sort=None):
        rows=self.find(query,projection)
        if sort:rows=rows.sort(sort)
        return rows[0] if rows else None

    def _matches(self, row, query):
        query=copy.deepcopy(query)
        for field, condition in list(query.items()):
            if isinstance(condition,dict) and '$regex' in condition:
                value=row.get(field)
                if not isinstance(value,str) or not re.search(condition['$regex'],value,re.I if condition.get('$options')=='i' else 0):return False
                condition.pop('$regex');condition.pop('$options',None)
                if not condition:query.pop(field)
        return super()._matches(row,query)


class FilterDB(RecordDB):
    def __missing__(self,name):
        self[name]=FilterCollection();return self[name]


def put(db, captured=False, day=date(2026,10,2), category='Alimentação', amount='-10', **fields):
    identity=ObjectId()
    row={'_id':identity,'data_evento':day_start(day),'valor':Decimal128(amount),'tipo':'compra','fluxo':'saida',
         'classification':{'migration_reason':'despesa_real'},'status':'confirmed',
         'categoria_sugerida' if captured else 'categoria':category,**fields}
    if captured:row['provisional_id']=str(identity)
    db['provisional_transactions' if captured else 'transactions'].docs.append(row)
    return row


def included(row, projection):
    """Apply a Mongo inclusion projection. Fields left out are absent, as on the server."""
    projected = {}
    if row.get('_id') is not None and projection.get('_id', 1):
        projected['_id'] = row['_id']
    for key, flag in projection.items():
        if not flag or key == '_id':
            continue
        if '.' in key:
            head, tail = key.split('.', 1)
            nested = row.get(head)
            if isinstance(nested, dict) and tail in nested:
                projected.setdefault(head, {})[tail] = nested[tail]
        elif key in row:
            projected[key] = row[key]
    return projected


def listing(db, **kwargs):
    args={'limit':50,'cursor':None,'search':None,'category':None,'account_id':None,
          'date_from':date(2026,10,1),'date_to':date(2026,10,31),'db':db,**kwargs}
    return cursor_movements(**args)


class FilterConsistencyTests(unittest.TestCase):
    def test_apostrophe_brand_keeps_tokens_together_in_indexed_search(self):
        from unittest.mock import Mock
        from app.movement_filters import search_query
        db=Mock();db.merchants.find.return_value=[]
        self.assertEqual(search_query(db,Mock(),'categoria',"McDonald's"),{'$text':{'$search':'"McDonald\'s"'}})
        self.assertEqual(search_query(db,Mock(),'categoria','mercado'),{'$text':{'$search':'mercado'}})

    def test_every_dashboard_category_matches_its_economic_drilldown(self):
        db=FilterDB()
        declared=put(db,day=date(2026,9,25),competencia='2026-10',amount='-17')
        put(db,competencia='2026-11',amount='-99')
        put(db,True,category=None,amount='-30')
        put(db,True,category=None,amount='5',natureza='ESTORNO')
        put(db,True,category='Alimentação',amount='-90',status='provisional')
        put(db,True,amount='100',classification={},fluxo='entrada')
        put(db,True,amount='-100',natureza='TRANSFERENCIA')
        put(db,amount='-100',classification={})
        put(db,day=date(2026,11,1),category='Transporte',data_evento=datetime(2026,11,1,2,59,tzinfo=timezone.utc))
        before=copy.deepcopy(dict(db))
        dashboard=_dashboard('month',date(2026,10,1),date(2026,10,31),db)
        for bucket in dashboard['category_spending']:
            result=listing(db,category=bucket['category'],period_basis='economic',spending_only=True)
            self.assertEqual(result['spending_total'],bucket['amount'])
        self.assertEqual(listing(db,category='Alimentação',period_basis='economic',spending_only=True)['items'][0]['id'],str(declared['_id']))
        self.assertNotEqual(listing(db,category='Alimentação')['total'],1)
        for name in ('transactions','provisional_transactions'):self.assertEqual(before[name].docs,db[name].docs)

    def test_linked_capture_is_one_economic_expense_on_the_dashboard_and_drilldown(self):
        db=FilterDB()
        ledger=put(db,day=date(2026,10,5),amount='-10',category='Alimentação',natureza='COMPRA')
        put(db,True,day=date(2026,10,5),amount='-10',category='Alimentação',natureza='COMPRA',linked_transaction_id=str(ledger['_id']))
        put(db,True,day=date(2026,10,6),amount='-4',category='Transporte',natureza='COMPRA')
        before=copy.deepcopy(dict(db))
        dashboard=_dashboard('month',date(2026,10,1),date(2026,10,31),db)
        self.assertEqual(dashboard['expenses'],14)
        food=next(bucket['amount'] for bucket in dashboard['category_spending'] if bucket['category']=='Alimentação')
        self.assertEqual(food,10)
        natures={row['nature']:row['amount'] for row in dashboard['nature_spending']}
        self.assertEqual(natures['COMPRA'],14)
        drill=listing(db,category='Alimentação',period_basis='economic',spending_only=True)
        self.assertEqual((drill['spending_total'],drill['total']),(10,1))
        self.assertEqual(listing(db)['total'],3)
        for name in ('transactions','provisional_transactions'):self.assertEqual(before[name].docs,db[name].docs)
        projected=_dashboard('month',date(2026,10,1),date(2026,10,31),db,snapshot=(
            [included(row,PROJECTION) for row in db.transactions.docs],
            [included(row,PROJECTION) for row in db.provisional_transactions.docs if row.get('status')=='confirmed']))
        self.assertEqual(projected['expenses'],14)
        self.assertEqual(sum(row['expenses'] for row in projected['monthly_series']),14)
        self.assertIn('linked_transaction_id', PROJECTION)

    def test_no_category_truncation_beyond_eight_or_twelve(self):
        db=FilterDB()
        for index in range(17):put(db,category='Categoria '+str(index))
        dashboard=_dashboard('month',date(2026,10,1),date(2026,10,31),db)
        self.assertEqual(len(dashboard['category_spending']),17)
        self.assertEqual(sum(bucket['amount'] for bucket in dashboard['category_spending']),dashboard['expenses'])

    def test_persisted_capture_category_wins_over_inference_in_every_read(self):
        db=FilterDB()
        row=put(db,True,category=None,merchant_raw='IOF OPERACOES FATURA')
        original=copy.deepcopy(row)
        for item in (with_merchant_name(row,db),with_merchant_names([row],db)[0]):
            self.assertIsNone(item['categoria_sugerida'])
            self.assertIsNone(item['categoria'])
            self.assertEqual(item['categoria_inferida'],'Despesas Financeiras')
        self.assertEqual(listing(db,category='Sem categoria')['total'],1)
        self.assertEqual(listing(db,category='Despesas Financeiras')['total'],0)
        self.assertEqual(row,original)

    def test_cursor_is_bound_to_search_category_account_dates_basis_and_spending(self):
        db=FilterDB();put(db);put(db,True)
        first=listing(db,limit=1)
        for change in ({'category':'Transporte'},{'account_id':str(ObjectId())},{'search':'Outro'},
                       {'date_from':date(2026,10,2)},{'date_to':date(2026,10,30)},
                       {'period_basis':'economic'},{'spending_only':True}):
            with self.subTest(change=change),self.assertRaises(HTTPException) as error:
                listing(db,cursor=first['next_cursor'],**change)
            self.assertEqual(error.exception.status_code,422)
        self.assertEqual(listing(db,cursor=first['next_cursor'],limit=10)['total'],2)

    def test_spending_pagination_filters_before_count_and_page(self):
        db=FilterDB()
        for index in range(9):put(db,True,category=None,amount='-2')
        for index in range(12):put(db,True,category=None,amount='-100',natureza='TRANSFERENCIA')
        ids=[];cursor=None
        while True:
            page=listing(db,limit=2,category='Sem categoria',period_basis='economic',spending_only=True,cursor=cursor)
            self.assertEqual((page['total'],page['spending_total']),(9,18))
            ids.extend(row['provisional_id'] for row in page['items'])
            if not page['has_more']:break
            cursor=page['next_cursor']
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(len(ids),9)

    def test_invalid_dates_are_rejected_in_both_listing_apis(self):
        db=FilterDB()
        with self.assertRaises(HTTPException) as error:listing(db,date_from=date(2026,11,1))
        self.assertEqual(error.exception.status_code,422)
        with self.assertRaises(HTTPException):transactions(limit=50,offset=0,cursor=None,search=None,category=None,
            account_id=None,review_status='todos',date_from=date(2026,11,1),date_to=date(2026,10,1),db=db)

    def test_search_covers_displayed_seller_platform_and_uncategorized_label(self):
        db=FilterDB();seller=ObjectId();platform=ObjectId()
        db.merchants.docs.extend([{'_id':seller,'nome_padrao':'Loja Revisada'},{'_id':platform,'nome_padrao':'Plataforma Revisada'}])
        a=put(db,merchant_id=seller)
        b=put(db,True,platform_id=platform)
        put(db,True,category=None)
        self.assertEqual([row['id'] for row in listing(db,search='Loja Revisada')['items']],[str(a['_id'])])
        self.assertEqual([row['id'] for row in listing(db,search='Plataforma Revisada')['items']],[str(b['_id'])])
        self.assertEqual(listing(db,search='Sem categoria')['total'],1)

    def test_experiment_uses_uncategorized_bucket_instead_of_literal_value(self):
        db=FilterDB();put(db,True,day=date(2026,9,25),category=None,amount='-20')
        put(db,True,category='',amount='-8')
        db.saving_experiments.docs.append({'_id':'synthetic','category':'Sem categoria','baseline_start':'2026-09-01',
            'baseline_end':'2026-09-30','start':'2026-10-01','end':'2026-10-31'})
        with patch('app.insights.today',return_value=date(2026,10,5)):
            report=experiment_report('synthetic',db)
        self.assertEqual(report['baseline_spending'],'20.00')
        self.assertEqual(report['observed_spending'],'8.00')


if __name__=='__main__':unittest.main()
