import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]))
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.auth import require_session
from app.main import app
from app.purchase_check import PurchaseOfferExtraction
from app.schemas_document import DocumentExtraction
from app.services.ai.xai.provider import XAIError, _strict_schema
from app.voice_capture import CaptureDraft, InterpretInput, XAIVoiceProvider, interpret


class VoiceCaptureTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[require_session]=lambda:True
        self.client=TestClient(app)
        self.settings=SimpleNamespace(xai_api_key='server-secret',xai_base_url='https://api.x.ai/v1',xai_transcription_model='grok-voice-transcribe-2.0',xai_text_model='grok-4.3')

    def tearDown(self):
        app.dependency_overrides.pop(require_session,None)

    def response(self,payload):
        return io.BytesIO(json.dumps(payload).encode())

    def test_stt_uses_real_endpoint_model_language_and_file_last(self):
        with patch('app.voice_capture.get_settings',return_value=self.settings), patch('app.voice_capture.urllib.request.urlopen',return_value=self.response({'text':'Paguei R$ 35,90 na padaria.'})) as send:
            result=XAIVoiceProvider().transcribe(b'RIFFxxxxWAVEaudio','audio/wav','wav')
        request=send.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.x.ai/v1/stt')
        self.assertEqual(request.get_header('Authorization'),'Bearer server-secret')
        body=request.data
        self.assertIn(b'grok-voice-transcribe-2.0',body)
        self.assertIn(b'\r\npt\r\n',body)
        self.assertLess(body.index(b'name="model"'),body.index(b'name="file"'))
        self.assertLess(body.index(b'name="format"'),body.index(b'name="file"'))
        self.assertFalse(result['persisted'])
        self.assertNotIn('server-secret',json.dumps(result))

    def test_http_errors_and_empty_speech_are_actionable(self):
        for status,code in [(401,'authentication'),(429,'rate_limit'),(503,'unavailable')]:
            with self.subTest(status=status), patch('app.voice_capture.get_settings',return_value=self.settings), patch('app.voice_capture.urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://api.x.ai',status,'error',{},None)):
                with self.assertRaises(XAIError) as failure:XAIVoiceProvider().transcribe(b'a','audio/wav','wav')
                self.assertEqual(failure.exception.code,code)
        with patch('app.voice_capture.get_settings',return_value=self.settings), patch('app.voice_capture.urllib.request.urlopen',return_value=self.response({'text':'  '})):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as failure:XAIVoiceProvider().transcribe(b'a','audio/wav','wav')
            self.assertEqual(failure.exception.status_code,422)

    def test_upload_rejects_fake_mime_empty_and_oversize_before_provider(self):
        with patch('app.voice_capture.XAIVoiceProvider') as provider:
            for data,status in [(b'',422),(b'not audio',415),(b'a'* (20*1024*1024+1),413)]:
                with self.subTest(status=status):
                    result=self.client.post('/v1/capture/transcribe',files={'file':('fake.wav',data,'audio/wav')})
                    self.assertEqual(result.status_code,status)
            provider.assert_not_called()

    def test_upload_detects_audio_without_trusting_name_and_never_needs_database(self):
        with patch('app.voice_capture.XAIVoiceProvider') as provider:
            provider.return_value.transcribe.return_value={'text':'Transferi R$ 20','model':'grok-voice-transcribe-2.0','needs_review':True,'persisted':False}
            result=self.client.post('/v1/capture/transcribe',files={'file':('bad.exe',b'RIFFxxxxWAVEaudio','application/octet-stream')})
            self.assertEqual(result.status_code,200)
            provider.return_value.transcribe.assert_called_once_with(b'RIFFxxxxWAVEaudio','audio/wav','wav')
            self.assertFalse(result.json()['persisted'])

    def test_routes_retain_session_protection(self):
        app.dependency_overrides.pop(require_session,None)
        for path in ['/v1/capture/transcribe','/v1/capture/interpret']:
            with patch('app.auth.get_settings',return_value=SimpleNamespace(environment='production',app_password='configured')):
                response=self.client.post(path,files={'file':('a.wav',b'RIFFxxxxWAVEaudio','audio/wav')}) if path.endswith('transcribe') else self.client.post(path,json={'context':'purchase','text':'test'})
            self.assertEqual(response.status_code,401)

    def test_interpret_is_contextual_and_read_only(self):
        for context,model,answer in [('purchase',PurchaseOfferExtraction,PurchaseOfferExtraction(total_price='3256',installment_count=10)),('movement',CaptureDraft,CaptureDraft(kind='own_transfer',amount='20',origin_hint='Itaú',destination_hint='Inter')),('commitment',CaptureDraft,CaptureDraft(kind='assinatura',amount='35.90',frequency='mensal'))]:
            with self.subTest(context=context), patch('app.voice_capture.get_settings',return_value=self.settings), patch('app.voice_capture._post',return_value=answer) as send:
                result=interpret(InterpretInput(context=context,text='test transcript'))
                self.assertIs(send.call_args.args[3],model)
                self.assertFalse(send.call_args.args[2]['store'])
                self.assertFalse(result['persisted'])
                self.assertTrue(result['needs_review'])

    def test_invalid_draft_and_context_do_not_reach_provider(self):
        with self.assertRaises(ValidationError):CaptureDraft(amount='20.001')
        with self.assertRaises(ValidationError):CaptureDraft(amount='-20')
        with patch('app.voice_capture.interpret') as provider:
            for body in [{'context':'execute_payment','text':'Pague'},{'context':'purchase','text':''},{'context':'purchase','text':'x'*12001}]:
                self.assertEqual(self.client.post('/v1/capture/interpret',json=body).status_code,422)
            provider.assert_not_called()

    def test_print_prices_normalize_brazilian_currency_without_rounding(self):
        for text,expected in [('R$ 3.256,90','3256.90'),('3.256','3256'),('BRL 35,90','35.90'),('3256.90','3256.90')]:
            self.assertEqual(str(PurchaseOfferExtraction(total_price=text).total_price),expected)
        for text in [35.901,'35.9011','R$ desconhecido','-35','35,999']:
            with self.assertRaises(ValidationError):PurchaseOfferExtraction(total_price=text)

    def test_strict_schema_recurses_into_receipt_items_and_decimal_money_is_string(self):
        schema=_strict_schema(DocumentExtraction)
        item=schema['$defs']['ReceiptItem']
        self.assertFalse(item['additionalProperties'])
        self.assertEqual(set(item['required']),set(item['properties']))
        purchase=_strict_schema(PurchaseOfferExtraction)
        options=purchase['properties']['total_price']['anyOf']
        self.assertEqual({v['type'] for v in options},{'string','null'})

    def test_print_failure_preserves_specific_retry_message(self):
        with patch('app.insights.XAIVisionProvider') as provider:
            provider.return_value.extract_purchase_offer.side_effect=XAIError('secret underlying error','rate_limit')
            response=self.client.post('/v1/insights/purchase-scan',files={'file':('x.png',b'\x89PNG\r\n\x1a\nabc','image/png')})
        self.assertEqual(response.status_code,503)
        self.assertIn('limite de uso',response.json()['detail'])
        self.assertNotIn('secret',response.text)


if __name__=='__main__':unittest.main()
