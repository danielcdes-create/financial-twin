import copy
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitment_projection import monthly_projection
from app.commitments import CommitmentIn, stored_document
from test_capture_batches import BatchDB
from app.future_commitments import digest


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.db=BatchDB()
        clock=patch('app.commitment_projection.today',return_value=date(2026,10,8))
        clock.start();self.addCleanup(clock.stop)

    def plan(self, parts, **kw):
        row={'_id':ObjectId(),'status':'ativo','descricao':'Compra','installments':parts,**kw}
        self.db.installment_plans.docs.append(row)
        return row

    def subscription(self, **kw):
        fields=dict(kind='assinatura',name='Serviço',purchase_date='2020-01-01',
            next_due_date='2020-01-15',amount='100.01',payment_method='pix',renewal_status='automatica')
        fields.update(kw)
        body=CommitmentIn(**fields)
        row=stored_document(body);row['_id']=ObjectId()
        self.db.commitments.docs.append(row)
        return row

    def test_auto_horizon_includes_final_january_2028_and_empty_months(self):
        self.plan([{'competencia':'2026-10','valor':Decimal128('20.10'),'status':'projetada'},
                   {'competencia':'2028-01','valor':Decimal128('30.20'),'status':'projetada'}])
        result=monthly_projection(self.db)
        self.assertEqual(result['last_installment_month'],'2028-01')
        self.assertEqual(result['months'],16)
        self.assertEqual(result['items'][-1]['total'],'30.20')
        self.assertEqual(result['items'][1]['total'],'0')
        self.assertEqual(result['total'],'50.30')

    def test_custom_horizon_and_old_recurring_schedule_seek(self):
        self.subscription()
        result=monthly_projection(self.db,24)
        self.assertEqual(result['months'],24)
        self.assertEqual(result['end_month'],'2028-09')
        self.assertTrue(all(row['total']=='100.01' for row in result['items']))
        self.assertEqual(result['total'],'2400.24')

    def test_paid_billed_cancelled_and_pre_cutoff_stay_out(self):
        self.plan([{'competencia':'2026-10','valor':Decimal128('100'),'status':'paga'},
            {'competencia':'2026-11','valor':Decimal128('100'),'status':'projetada','bill_id':ObjectId()},
            {'competencia':'2026-12','valor':Decimal128('100'),'status':'cancelada'},
            {'competencia':'2026-09','valor':Decimal128('100'),'status':'projetada'},
            {'data_vencimento':datetime(2026,10,1),'valor':Decimal128('100'),'status':'projetada'}])
        result=monthly_projection(self.db,3)
        # 01/10 is on the cutoff and before today, so it stays in October. September is before the cutoff.
        self.assertEqual(result['items'][0]['total'],'100')
        self.assertEqual(result['items'][1]['total'],'0')
        self.assertEqual(result['items'][2]['total'],'0')
        self.assertEqual(result['summary']['this_month'],'100')
        self.assertEqual(result['last_installment_month'],'2026-10')

    def test_override_uses_effective_amount_and_month_without_writes(self):
        part={'competencia':'2026-11','valor':Decimal128('10'),'status':'projetada'}
        plan=self.plan([part])
        self.db.future_schedule_overrides.docs=[{'_id':f'installment:{plan["_id"]}',
            'rows':{'1':{'original_token':digest(part),'amount':'42.59','due_date':'2026-12-15','cancelled':False}}}]
        before=copy.deepcopy({key:value.docs for key,value in self.db.items()})
        result=monthly_projection(self.db,3)
        self.assertEqual(result['items'][1]['total'],'0')
        self.assertEqual(result['items'][2]['total'],'42.59')
        for key,docs in before.items():self.assertEqual(self.db[key].docs,docs)

    def test_bill_is_separate_and_obligation_linked_to_plan_is_not_duplicated(self):
        plan=self.plan([{'competencia':'2026-11','valor':Decimal128('20'),'status':'projetada'}])
        for kind,value,origin in [('fatura_cartao','500',ObjectId()),('parcelamento','20',plan['_id']),('financiamento','80',ObjectId())]:
            self.db.obligations.docs.append({'_id':ObjectId(),'tipo':kind,'saldo_aberto':Decimal128(value),
                'status':'futura','origem_id':origin,'data_vencimento':datetime(2026,11,15)})
        result=monthly_projection(self.db,2)
        self.assertEqual(result['items'][1]['total'],'100')
        self.assertEqual(result['items'][1]['bill_balance'],'500')

    def test_paused_cancelled_and_deleted_future_schedules_do_not_reappear(self):
        for status in ['pausado','cancelado','concluido']:self.subscription(status=status)
        active=self.subscription();active['future_cutoff']=datetime(2026,10,8,tzinfo=timezone.utc)
        self.assertEqual(monthly_projection(self.db,12)['total'],'0')

    def test_automatic_horizon_capped_and_invalid_ranges_rejected(self):
        self.plan([{'competencia':'2040-01','valor':Decimal128('20'),'status':'projetada'}])
        result=monthly_projection(self.db)
        self.assertEqual(result['months'],120)
        self.assertTrue(result['automatic_horizon_capped'])
        for months in [0,121,-1,True,1.5]:
            with self.assertRaises(HTTPException):monthly_projection(self.db,months)

    def test_http_authentication_and_period_contract(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.auth import require_session
        from app.commitment_projection import router
        app=FastAPI();app.include_router(router);app.state.db=self.db
        client=TestClient(app)
        self.assertEqual(client.get('/v1/commitment-projection').status_code,401)
        app.dependency_overrides[require_session]=lambda:None
        response=client.get('/v1/commitment-projection?months=24')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['months'],24)
        self.assertEqual(response.json()['end_month'],'2028-09')
        for months in ['0','121','wrong']:
            self.assertEqual(client.get('/v1/commitment-projection?months='+months).status_code,422)

    def test_summary_matches_rows_and_keeps_bills_out_of_the_commitment_total(self):
        from decimal import Decimal
        self.plan([{'competencia':'2026-11','valor':Decimal128('20'),'status':'projetada'}])
        self.subscription()
        for kind, value in [('fatura_cartao','500'),('financiamento','5000')]:
            self.db.obligations.docs.append({'_id':ObjectId(),'tipo':kind,'saldo_aberto':Decimal128(value),
                'status':'futura','origem_id':ObjectId(),'data_vencimento':datetime(2026,11,15)})
        result = monthly_projection(self.db, 12)
        window = result['items'][:12]
        def added(key):
            return str(sum((Decimal(row[key]) for row in window), Decimal('0')))
        summary = result['summary']
        self.assertEqual(summary['this_month'], result['items'][0]['total'])
        self.assertEqual(summary['next_12'], added('total'))
        self.assertEqual(summary['installments'], added('installments'))
        self.assertEqual(summary['subscriptions'], added('subscriptions'))
        self.assertEqual(summary['accounts'], added('accounts'))
        self.assertEqual(summary['other'], added('other'))
        self.assertEqual(summary['bill_balance'], added('bill_balance'))
        november = result['items'][1]
        self.assertGreater(Decimal(november['installments']), 0)
        self.assertGreater(Decimal(november['subscriptions']), 0)
        self.assertGreater(Decimal(november['other']), 0)
        self.assertEqual(november['bill_balance'], '500')
        self.assertEqual(november['accounts'], '0')
        self.assertEqual(Decimal(november['total']), Decimal(november['installments']) + Decimal(november['subscriptions']) + Decimal(november['accounts']) + Decimal(november['other']))
        for months in [0, 121]:
            with self.assertRaises(HTTPException): monthly_projection(self.db, months)

    def test_variable_account_leaves_subscriptions_and_keeps_the_month_total(self):
        from decimal import Decimal
        self.subscription(amount='30.00', next_due_date='2026-11-15')
        self.subscription(amount='80.00', amount_mode='last_three_bills', account_id=str(ObjectId()), next_due_date='2026-11-15')
        self.plan([{'competencia': '2026-11', 'valor': Decimal128('20'), 'status': 'projetada'}])
        self.db.obligations.docs.append({'_id': ObjectId(), 'tipo': 'financiamento', 'saldo_aberto': Decimal128('50'),
            'status': 'futura', 'origem_id': ObjectId(), 'data_vencimento': datetime(2026, 11, 15)})
        november = monthly_projection(self.db, 2)['items'][1]
        series = [Decimal(november[key]) for key in ('installments', 'subscriptions', 'accounts', 'other')]
        previous = Decimal(november['installments']) + Decimal(november['subscriptions']) + Decimal(november['accounts']) + Decimal(november['other'])
        self.assertEqual(series[0], Decimal('20'))
        self.assertEqual(series[1], Decimal('30.00'))
        self.assertEqual(series[2], Decimal('80.00'))
        self.assertEqual(series[3], Decimal('50'))
        self.assertEqual(Decimal(november['total']), sum(series, Decimal('0')))
        self.assertEqual(Decimal(november['total']), previous)
        self.assertEqual(november['bill_balance'], '0')

    def test_short_horizon_does_not_invent_twelve_months(self):
        self.subscription()
        result = monthly_projection(self.db, 3)
        self.assertIsNone(result['summary']['next_12'])
        self.assertEqual(result['summary']['this_month'], result['items'][0]['total'])
        self.assertEqual(result['summary']['window_months'], 3)


if __name__=='__main__':unittest.main()
