import sys, unittest
from pathlib import Path
from datetime import datetime, date, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
sys.path[:0]=[str(Path(__file__).parents[1]),str(Path(__file__).parent)]
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from app.financial_time import day_start, local_date, date_filter
from app.matching import score_evidence
from app.transaction_service import TransactionService
from app.analytics import calculate_financial_totals
from app.main import update_provisional, ProvisionalUpdate, decide_provisional, ProvisionalAction, link_commitment_payment, CommitmentPaymentLink
from observed_contract import observed_mock_payment
from app.commitments import schedule, stored_document
from test_ingestion import FakeDB, Collection, event
from test_architecture_failures import FailsOnce, rollback_fake
import test_commitments

from observed_contract import observed_update

class IntegrityTests(unittest.TestCase):
    def test_ledger_refund_claim_blocks_economic_reclassification(self):
        from app.main import update_transaction_classification,TransactionClassificationUpdate
        identity=ObjectId();row={'_id':identity,'natureza':'ESTORNO','valor':Decimal128('10'),'fluxo':'entrada'}
        db=FakeDB(transactions=Collection([row]),refund_claims=Collection([{'_id':'ledger:'+str(identity),'case_id':'test'}]))
        with self.assertRaises(HTTPException) as error:update_transaction_classification(str(identity),observed_update(TransactionClassificationUpdate(natureza='TRANSFERENCIA'), db, str(identity)),db)
        self.assertEqual(error.exception.status_code,409);self.assertEqual(row['natureza'],'ESTORNO')

    def test_batch_rejects_changed_preview_before_writing(self):
        from app.main import update_transaction_classification,TransactionClassificationUpdate,classification_match_preview
        import test_transaction_review
        fixture=test_transaction_review.TransactionReviewTests();fixture.setUp();fixture.db.transactions.docs[1]['descricao_original']='IFD*GVG ALIM'
        token=classification_match_preview(str(fixture.first),fixture.db)['preview_token']
        fixture.db.transactions.docs[1]['categoria']='Alterada depois da prévia'
        with self.assertRaises(HTTPException) as error:update_transaction_classification(str(fixture.first),observed_update(TransactionClassificationUpdate(categoria='Restaurantes',apply_similar_classification=True,classification_preview_token=token), fixture.db, str(fixture.first)),fixture.db)
        self.assertEqual(error.exception.status_code,409);self.assertEqual(fixture.db.audit_log.docs,[])

    def test_login_window_limits_attempts_and_recovers(self):
        from app.auth import LoginAttempts
        limiter=LoginAttempts()
        for _ in range(10):limiter.check('test',now=10)
        with self.assertRaises(HTTPException) as error:limiter.check('test',now=20)
        self.assertEqual(error.exception.status_code,429)
        limiter.check('test',now=71)

    def test_missing_revision_rejected_for_versioned_capture(self):
        db=FakeDB(provisional_transactions=Collection([{'provisional_id':'p','status':'provisional','revision':1}]))
        with self.assertRaises(HTTPException) as error:update_provisional('p',ProvisionalUpdate(valor=3),db)
        self.assertEqual(error.exception.status_code,428)

    def test_calendar_excludes_next_local_midnight(self):
        limits=date_filter(date(2026,9,28),date(2026,9,28))
        self.assertEqual(local_date('2026-09-29T01:00:00Z'),date(2026,9,28))
        self.assertEqual(limits['$lt'],day_start(date(2026,9,29)))
        self.assertEqual(limits['$gte'].hour,3)

    def test_matching_ambiguity_amount_direction_and_manual(self):
        stamp=datetime(2026,9,29,12,tzinfo=timezone.utc)
        for amounts,source in [([26],'receipt'),([-30],'receipt'),([-26,-26],'receipt'),([-26],'manual')]:
            with self.subTest(amounts=amounts,source=source):
                db=FakeDB(transactions=Collection([{'_id':str(i),'valor':Decimal128(str(a)),'data_evento':stamp,'estabelecimento_original':'Padaria ABC','tipo':'compra'} for i,a in enumerate(amounts)]))
                result=TransactionService(db).process(event('test',source,stamp))
                self.assertNotEqual(result['status'],'matched')
                self.assertEqual(len(db.provisional_transactions.docs),1)

    def test_timestamp_iso_and_datetime_score_equal(self):
        stamp=datetime(2026,9,29,12,tzinfo=timezone.utc); e=event('test','receipt',stamp)
        tx={'valor':-26,'data_evento':stamp,'estabelecimento_original':'Padaria ABC'}
        expected=score_evidence(e,tx);e['parsed_payload']['event_timestamp']=stamp.isoformat()
        self.assertEqual(score_evidence(e,tx),expected)

    def test_refund_is_negative_consumption(self):
        result=calculate_financial_totals([], [{'valor':26,'natureza':'ESTORNO'}])
        self.assertEqual(result['income'],0);self.assertEqual(result['expenses'],-26)

    def test_capture_confirmation_rolls_back_when_audit_fails(self):
        capture={'_id':ObjectId(),'provisional_id':'p','event_id':'e','status':'provisional','tipo':'compra','fluxo':'saida','valor':Decimal128('-26')}
        db=FakeDB(provisional_transactions=Collection([capture]),raw_events=Collection([{'event_id':'e'}]),audit_log=FailsOnce())
        with patch('app.main.mongo_transaction',rollback_fake):
            with self.assertRaises(RuntimeError): decide_provisional('p',ProvisionalAction(action='confirm'),db)
        self.assertEqual(db.provisional_transactions.docs[0]['status'],'provisional')

    def test_transfer_edit_invalidates_reconciliation(self):
        item={'_id':ObjectId(),'provisional_id':'p','status':'confirmed','valor':Decimal128('-200'),'fluxo':'saida','tipo':'transferencia','natureza':'TRANSFERENCIA','revision':2,'transfer':{'from_account_key':'itau','to_account_key':'santander','source_transaction_id':'old','reconciliation_status':'conciliada'}}
        db=FakeDB(provisional_transactions=Collection([item]))
        with self.assertRaises(HTTPException):update_provisional('p',ProvisionalUpdate(valor=-300,expected_revision=1),db)
        update_provisional('p',ProvisionalUpdate(valor=-300,expected_revision=2),db)
        self.assertNotIn('source_transaction_id',item['transfer']);self.assertEqual(item['revision'],3)

    def test_end_of_month_anchor_survives_payment(self):
        item=stored_document(test_commitments.CommitmentTests().base());item['next_due_date']=datetime(2026,2,28,tzinfo=timezone.utc);item['next_installment_number']=2
        self.assertEqual([p['due_date'] for p in schedule(item)],['2026-02-28','2026-03-31'])

    def test_transfer_cannot_settle_commitment(self):
        db=MagicMock();item=stored_document(test_commitments.CommitmentTests().base());item['_id']=ObjectId()
        db.commitments.find_one.side_effect=[item,None]
        db.provisional_transactions.find_one.return_value={'status':'confirmed','natureza':'TRANSFERENCIA','valor':-29.9,'fluxo':'saida','data_evento':datetime(2026,1,31,tzinfo=timezone.utc)}
        with self.assertRaises(HTTPException) as error:link_commitment_payment(item['commitment_id'],observed_mock_payment(CommitmentPaymentLink(provisional_id='p'),db,item),db)
        self.assertEqual(error.exception.status_code,422);db.commitments.update_one.assert_not_called()
