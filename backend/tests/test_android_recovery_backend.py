"""BE01-BE06 acceptance cases, using only synthetic in-memory collections."""
import copy
import io
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from starlette.datastructures import Headers
from app.account_profiles import (AccountProfileCreate, AccountProfileUpdate,
    AccountProfileDelete, create_profile, update_profile, delete_profile, account_usage)
from app.cards import BillEdit, edit_bill, token
from app.commitments import CommitmentIn, schedule
from app.commitment_projection import monthly_projection
from app.insights import _planning_context
from app.main import create_commitment, update_commitment, extract_document_sync
from app.schemas_document import DocumentExtraction
from app.transaction_service import TransactionService
from test_architecture_failures import rollback_fake, FailsOnce
from test_domain_ledger_fixes import DomainDB, DomainCollection, expense, payment, put
from test_domain_account_time_privacy import fixture


class RecoveryCollection(DomainCollection):
    def replace_one(self, query, document):
        old = next((row for row in self.docs if self._matches(row, query)), None)
        if old is None: return SimpleNamespace(matched_count=0)
        self.docs[self.docs.index(old)] = copy.deepcopy(document)
        return SimpleNamespace(matched_count=1)


class RecoveryDB(DomainDB):
    def __missing__(self, key):
        self[key] = RecoveryCollection()
        return self[key]


class AndroidBackendRecoveryTests(unittest.TestCase):
    def commitment(self, db=None, **changes):
        db = db if db is not None else RecoveryDB()
        profile = create_profile(db, AccountProfileCreate(request_id=uuid4(),
            instituicao='Banco sintético', tipo='conta_corrente'))
        values = dict(kind='assinatura', name='Assinatura sintética', purchase_date='2026-10-01',
            next_due_date='2026-10-20', amount='100.00', payment_method='pix',
            account_id='profile:'+profile['key'], renewal_status='automatica', creation_id=uuid4())
        values.update(changes)
        body = CommitmentIn(**values)
        result = create_commitment(body, db)
        return db, profile, body, result

    def close(self, db, profile, **changes):
        values = {key: profile[key] for key in AccountProfileUpdate.model_fields if key in profile}
        return update_profile(db, profile['key'], AccountProfileUpdate(**{
            **values, 'expected_revision':profile['revision'], **changes}))

    def test_be01_every_commitment_state_blocks_deletion_including_deleted_future(self):
        for status in ('ativo','pausado','cancelado','concluido'):
            with self.subTest(status=status):
                db, profile, _, _ = self.commitment(status=status)
                db.commitments.docs[0]['future_deleted_at'] = datetime.now(timezone.utc)
                self.assertEqual(account_usage(db, profile['key'])['compromissos'], 1)
                with self.assertRaises(HTTPException) as error:
                    delete_profile(db, profile['key'], AccountProfileDelete(expected_revision=1))
                self.assertEqual(error.exception.status_code,409)
                self.assertFalse(db.account_profiles.docs[0].get('deleted'))

    def test_be01_amount_rules_and_legacy_identities_are_in_usage(self):
        db=RecoveryDB()
        legacy=ObjectId()
        db.accounts.docs.append({'_id':legacy,'tipo':'conta_corrente','instituicao':'Itaú'})
        for identity in ('profile:itau', str(legacy), legacy):
            db.commitments.docs.append({'account_id':identity,'status':'concluido'})
            db.commitment_amount_rules.docs.append({'account_id':identity})
        usage=account_usage(db,'itau')
        self.assertEqual(usage['compromissos'],3)
        self.assertEqual(usage['regras_de_valor'],3)

    def test_be01_create_and_edit_lock_before_reference_validation(self):
        events=[]
        def locked(_db): events.append('lock')
        def checked(*_args): events.append('refs')
        with patch('app.main.lock_movement_insert',locked), patch('app.main.validate_commitment_refs',checked):
            db,_,body,result=self.commitment()
            update_commitment(result['commitment_id'],CommitmentIn(**{
                **body.model_dump(),'revision':1,'notes':'Uma nota'}),db)
        self.assertEqual(events,['lock','refs','lock','refs'])

    def test_be02_historical_account_remains_valid_for_compatible_edit(self):
        for closed in ({'status':'encerrada'}, {'archived':True}):
            for status in ('cancelado','pausado','ativo'):
                with self.subTest(closed=closed,status=status):
                    db,profile,body,result=self.commitment()
                    self.close(db,profile,**closed)
                    original=copy.deepcopy(db.commitments.docs[0]['linked_transactions'])
                    response=update_commitment(result['commitment_id'],CommitmentIn(**{
                        **body.model_dump(),'revision':1,'status':status,'notes':'Conta histórica'}),db)
                    self.assertEqual(response['account_id'],body.account_id)
                    self.assertEqual(response['status'],status)
                    self.assertEqual(db.commitments.docs[0]['linked_transactions'],original)
                    with self.assertRaises(HTTPException):
                        create_commitment(CommitmentIn(**{**body.model_dump(),'creation_id':uuid4()}),db)

    def test_be02_changing_to_closed_account_is_rejected(self):
        db,profile,body,result=self.commitment()
        other=create_profile(db,AccountProfileCreate(request_id=uuid4(),instituicao='Outro',tipo='conta_corrente'))
        self.close(db,other,status='encerrada')
        with self.assertRaises(HTTPException) as error:
            update_commitment(result['commitment_id'],CommitmentIn(**{
                **body.model_dump(),'revision':1,'account_id':'profile:'+other['key']}),db)
        self.assertEqual(error.exception.status_code,422)
        self.assertEqual(db.commitments.docs[0]['account_id'],body.account_id)

    def test_be02_retained_legacy_identity_survives_profile_closing(self):
        db=RecoveryDB();identity=ObjectId()
        db.accounts.docs.append({'_id':identity,'tipo':'conta_corrente','instituicao':'Itaú'})
        db,_,body,result=self.commitment(db,account_id=str(identity))
        profile={'key':'itau','revision':1,'instituicao':'Itaú','tipo':'conta_corrente'}
        self.close(db,profile,status='encerrada')
        response=update_commitment(result['commitment_id'],CommitmentIn(**{
            **body.model_dump(),'revision':1,'status':'cancelado'}),db)
        self.assertEqual(response['account_id'],str(identity))

    def test_be03_edited_open_bill_stays_in_planning_and_projection_once(self):
        db=RecoveryDB();card,bill_id,obligation_id=ObjectId(),ObjectId(),ObjectId()
        db.accounts.docs.append({'_id':card,'tipo':'cartao_credito','instituicao':'Sintético'})
        due=datetime(2026,10,20,tzinfo=timezone.utc)
        bill={'_id':bill_id,'card_account_id':card,'competencia':'2026-10','data_vencimento':due,
            'status':'aberta','valor_total':Decimal128('100'),'valor_pago':Decimal128('0'),
            'saldo_aberto':Decimal128('100'),'novos_lancamentos':Decimal128('100')}
        obligation={'_id':obligation_id,'tipo':'fatura_cartao','origem_id':bill_id,
            'competencia':'2026-10','data_vencimento':due,'status':'futura',
            'saldo_aberto':Decimal128('100'),'valor_pago':Decimal128('0'),
            'valor_original':Decimal128('100'),'principal':Decimal128('100')}
        db.credit_card_bills.docs.append(bill);db.obligations.docs.append(obligation)
        with patch('app.cards.today',return_value=date(2026,10,9)):
            edit_bill(str(bill_id),BillEdit(expected_token=token(bill,{},[obligation]),
                competencia='2026-10',data_vencimento='2026-10-20',valor_total='120.00'),db)
        suggestions=_planning_context(date(2026,10,9),date(2026,11,9),db)['suggested_flows']
        self.assertEqual(len(suggestions),1);self.assertEqual(suggestions[0]['amount'],'-120.00')
        with patch('app.commitment_projection.today',return_value=date(2026,10,9)):
            self.assertEqual(monthly_projection(db,1)['items'][0]['bill_balance'],'120.00')
        for status in ('futura','aberta','vencida','parcialmente_paga','paga','cancelada'):
            db.obligations.docs[0]['status']=status
            self.assertEqual(len(_planning_context(date(2026,10,9),date(2026,11,9),db)['suggested_flows']),
                int(status in ('futura','aberta','vencida','parcialmente_paga')))

    def test_be04_same_amount_merchant_next_day_remains_independent_and_replay_is_one(self):
        db,event,_=fixture('itau')
        event['captured_at']+=timedelta(days=1)
        event['parsed_payload']['event_timestamp']=event['captured_at'].isoformat()
        service=TransactionService(db);response=service.process(event)
        self.assertEqual(response['status'],'review_required')
        self.assertEqual(len(db.provisional_transactions.docs),1)
        self.assertEqual(db.transaction_evidence.docs,[])
        self.assertTrue(service.process(event)['duplicate'])
        self.assertEqual(len(db.provisional_transactions.docs),1)

    def test_ps01_ambiguous_native_evidence_is_not_reinterpreted_as_itau_purchase(self):
        db,event,_=fixture('itau')
        event['raw_payload']['text']='Compra aprovada de R$ 8,00 em LOJA no dia 01/10/2026 às 12:00. Compra não aprovada.'
        event['parsed_payload']={'event_type':'financial_notification','currency':'BRL',
            'event_timestamp':event['captured_at'].isoformat()}
        self.assertEqual(TransactionService(db).process(event)['status'],'needs_parse')
        self.assertEqual(db.provisional_transactions.docs,[])
        self.assertEqual(db.raw_events.docs[0]['parsed_payload'],event['parsed_payload'])
        self.assertEqual(len(db.review_queue.docs),1)
        self.assertEqual(db.transaction_evidence.docs,[])

    def test_be05_last_payment_allows_note_edit_without_reopening_debt(self):
        for count in (1,600):
            with self.subTest(count=count):
                db,_,body,result=self.commitment(kind='parcelamento',installment_count=count,
                    next_installment_number=count,next_due_date='2026-10-09',renewal_status='nao_aplicavel')
                item=db.commitments.docs[0];tx=expense(date(2026,10,9),value='-100.00');put(db,'transactions',tx)
                payment(db,item,tx)
                completed=copy.deepcopy(db.commitments.docs[0])
                values={key:value for key,value in completed.items() if key in CommitmentIn.model_fields}
                edited=CommitmentIn(**{**values,'notes':'Observação após quitação'})
                update_commitment(result['commitment_id'],edited,db)
                saved=db.commitments.docs[0]
                self.assertEqual(saved['status'],'concluido')
                self.assertEqual(saved['next_installment_number'],count+1)
                self.assertEqual(saved['linked_transactions'],completed['linked_transactions'])
                self.assertEqual(saved['next_due_date'],completed['next_due_date'])
                self.assertEqual(schedule(saved),[])
                with self.assertRaises(ValidationError):
                    CommitmentIn(**{**values,'status':'ativo'})

    def voice(self, db, identity, captured_at=None):
        upload=UploadFile(filename=f'voice-{identity}.txt',file=io.BytesIO(b'Gastei oito reais hoje'),
            headers=Headers({'content-type':'text/plain'}))
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db,service=TransactionService(db))))
        kwargs={'event_id':identity}
        if captured_at is not None: kwargs['captured_at']=captured_at
        return extract_document_sync(upload,'mobile_voice',db,request,**kwargs)

    def voice_patches(self):
        extraction=DocumentExtraction(document_type='receipt',total='8.00',currency='BRL',merchant='Lanchonete')
        return patch('app.main.store_media',return_value='synthetic-retained-voice.txt'),patch('app.main.XAITextProvider.extract_text',return_value=extraction)

    def test_be06_new_voices_keep_uuid_time_and_independent_capture(self):
        db=RecoveryDB();first_id,second_id=uuid4(),uuid4()
        stamp=datetime(2026,10,8,15,tzinfo=timezone.utc)
        media,ai=self.voice_patches()
        with media,ai as extraction:
            first=self.voice(db,first_id,stamp)
            self.assertEqual(self.voice(db,first_id,stamp),first)
            second=self.voice(db,second_id,stamp+timedelta(days=1))
        self.assertEqual(extraction.call_count,2)
        self.assertEqual(first['processing']['persisted_event_id'],str(first_id))
        self.assertEqual(second['processing']['persisted_event_id'],str(second_id))
        self.assertEqual([row['data_evento'] for row in db.provisional_transactions.docs],[stamp,stamp+timedelta(days=1)])
        self.assertEqual(len(db.raw_events.docs),2)

    def test_be06_legacy_voice_receipt_and_completed_context_remain_unchanged(self):
        db=RecoveryDB();media,ai=self.voice_patches()
        with media,ai as extraction:
            first=self.voice(db,uuid4());second=self.voice(db,uuid4())
        self.assertEqual(extraction.call_count,1)
        self.assertEqual(first['processing']['persisted_event_id'],second['processing']['persisted_event_id'])
        self.assertEqual(len(db.raw_events.docs),1)
        self.assertTrue(all(row['context']=={'source_provider':'mobile_voice','mime_type':'text/plain'} for row in db.batch_commands.docs))

    def test_be06_changed_command_time_is_conflict_and_lost_ack_reuses_raw_event(self):
        db=RecoveryDB();identity=uuid4();stamp=datetime(2026,10,8,15,tzinfo=timezone.utc)
        media,ai=self.voice_patches()
        with media,ai as extraction:
            with patch('app.main.complete_upload',side_effect=RuntimeError('Lost ACK write')):
                with self.assertRaises(RuntimeError):self.voice(db,identity,stamp)
            result=self.voice(db,identity,stamp)
            with self.assertRaises(HTTPException) as error:self.voice(db,identity,stamp+timedelta(days=1))
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(extraction.call_count,1)
        self.assertEqual(len(db.provisional_transactions.docs),1)
        self.assertEqual(result['processing']['persisted_event_id'],str(identity))

    def test_be06_new_protocol_requires_uuid_and_explicit_timezone_before_persistence(self):
        for identity,stamp,code in ((None,datetime(2026,10,8,15,tzinfo=timezone.utc),428),
            (uuid4(),datetime(2026,10,8,15),422)):
            db=RecoveryDB();media,ai=self.voice_patches()
            with media,ai as extraction,self.assertRaises(HTTPException) as error:
                self.voice(db,identity,stamp)
            self.assertEqual(error.exception.status_code,code)
            self.assertFalse(db.raw_events.docs)
            self.assertFalse(db.batch_commands.docs)
            extraction.assert_not_called()


if __name__=='__main__': unittest.main()
