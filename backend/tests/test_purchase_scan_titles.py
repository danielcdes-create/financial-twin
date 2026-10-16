import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.auth import require_session
from app.main import app
from app.purchase_check import PurchaseOfferExtraction
from app.services.ai.xai.provider import XAIError, _post


TITLE = 'Samsung Galaxy Ring Tamanho 6, Monitor de treino, rastreador de sono, bateria de até 7 dias, Material em titânio - Titânio Dourado'
OFFER = {'product_label': TITLE, 'total_price': '1873.14', 'installment_count': 12, 'installment_amount': '156.15', 'ambiguity': None}


def response(offer):
    return io.BytesIO(json.dumps({'output': [{'content': [{'type': 'output_text', 'text': json.dumps(offer)}]}]}).encode())


class PurchaseScanTitleTests(unittest.TestCase):
    def test_original_long_marketplace_title_keeps_cent_exact_offer(self):
        offer = PurchaseOfferExtraction.model_validate_json(json.dumps(OFFER))
        self.assertLessEqual(len(offer.product_label), 120)
        self.assertTrue(offer.product_label.startswith('Samsung Galaxy Ring Tamanho 6'))
        self.assertTrue(offer.product_label.endswith('…'))
        self.assertEqual(str(offer.total_price), '1873.14')
        self.assertEqual(offer.installment_count, 12)
        self.assertEqual(str(offer.installment_amount), '156.15')
        self.assertIsNone(offer.ambiguity)

    def test_title_normalization_does_not_invent_labels_or_prices(self):
        self.assertEqual(PurchaseOfferExtraction(product_label='  Galaxy\n Ring  ').product_label, 'Galaxy Ring')
        self.assertEqual(PurchaseOfferExtraction(product_label='x' * 120).product_label, 'x' * 120)
        unknown = PurchaseOfferExtraction(product_label=None, total_price=None, ambiguity='Duas ofertas sem seleção.')
        self.assertIsNone(unknown.product_label)
        self.assertIsNone(unknown.total_price)
        self.assertEqual(unknown.ambiguity, 'Duas ofertas sem seleção.')
        with self.assertRaises(ValidationError):
            PurchaseOfferExtraction(product_label={'unexpected': 'value'})

    def test_long_title_never_relaxes_financial_or_extra_field_validation(self):
        for change in [{'total_price': '1873.141'}, {'total_price': '-1'}, {'installment_amount': '156,151'},
                       {'installment_count': 61}, {'installment_count': 0}, {'execute_payment': True}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                PurchaseOfferExtraction.model_validate({**OFFER, **change})

    def test_provider_to_http_scan_accepts_long_title_without_persisting(self):
        settings = SimpleNamespace(xai_api_key='fixture-key', xai_base_url='https://fixture.invalid/v1', xai_vision_model='grok-4.3')
        app.dependency_overrides[require_session] = lambda: True
        try:
            with patch('app.services.ai.xai.provider.get_settings', return_value=settings), \
                 patch('app.services.ai.xai.provider.urllib.request.urlopen', return_value=response(OFFER)) as request:
                result = TestClient(app).post('/v1/insights/purchase-scan', files={'file': ('print.png', b'\x89PNG\r\n\x1a\nfixture', 'image/png')})
            self.assertEqual(result.status_code, 200)
            body = result.json()
            self.assertLessEqual(len(body['product_label']), 120)
            self.assertEqual(body['total_price'], '1873.14')
            self.assertEqual(body['installment_count'], 12)
            self.assertEqual(body['installment_amount'], '156.15')
            self.assertFalse(body['persisted'])
            self.assertTrue(body['needs_review'])
            sent = json.loads(request.call_args.args[0].data)
            self.assertFalse(sent['store'])
        finally:
            app.dependency_overrides.pop(require_session, None)

    def test_validation_diagnostics_do_not_log_values_or_unknown_field_names(self):
        bad_offer = {**OFFER, 'total_price': 'SECRET_PRICE', 'PRIVATE_EXTRA': 'SECRET_DATA'}
        with patch('app.services.ai.xai.provider.urllib.request.urlopen', return_value=response(bad_offer)), \
             self.assertLogs('app.services.ai.xai.provider', level='WARNING') as logs:
            with self.assertRaises(XAIError) as raised:
                _post('https://fixture.invalid/v1', 'fixture-key', {}, PurchaseOfferExtraction)
        self.assertEqual(raised.exception.code, 'invalid_output')
        logged = ' '.join(logs.output)
        self.assertIn('total_price:decimal_parsing', logged)
        self.assertIn('unknown_field:extra_forbidden', logged)
        for private in ['SECRET_PRICE', 'SECRET_DATA', 'PRIVATE_EXTRA', TITLE, 'fixture-key']:
            self.assertNotIn(private, logged)


if __name__ == '__main__':
    unittest.main()
