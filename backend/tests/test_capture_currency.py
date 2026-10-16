import hashlib
import io
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi import HTTPException, UploadFile
from app.capture_batches import scan_sync
from app.document_validation import validate_document_extraction
from app.main import extract_document_data
from app.schemas_document import DocumentExtraction
from app.services.ai.xai.provider import DOCUMENT_FACTS_PROMPT, _strict_schema
from app.statement_extraction import StatementExtraction
from test_capture_batches import BatchDB
from test_pdf_input import pdf_bytes


class CaptureCurrencyTests(unittest.TestCase):
    def test_explicit_brl_aliases_have_one_canonical_currency_and_exact_cents(self):
        for label in ('BRL', ' brl ', 'R$', 'reais', 'REAL BRASILEIRO', 'Brazilian real', 'R$ (BRL)'):
            with self.subTest(label=label):
                receipt = DocumentExtraction(document_type='receipt', total=12.59, currency=label)
                self.assertEqual(receipt.currency, 'BRL')
                self.assertEqual(validate_document_extraction(receipt)['total'], Decimal('12.59'))
                statement = StatementExtraction(document_kind='bank_statement', currency=label, rows=[
                    {'date': '2026-10-01', 'amount': '-12.59', 'description': 'Synthetic store', 'kind': 'expense'}
                ], complete=True)
                self.assertEqual(statement.currency, 'BRL')
                self.assertEqual(statement.rows[0].amount, Decimal('-12.59'))

    def test_unknown_or_foreign_currency_is_never_inferred_or_converted(self):
        for currency in (None, '', '$', 'USD', 'US$', 'EUR', 'BRL/USD', 'peso real', '美元BRL', 'US$ R$'):
            with self.subTest(currency=currency):
                receipt = DocumentExtraction(document_type='receipt', total=12.59, currency=currency)
                with self.assertRaises(ValueError):
                    validate_document_extraction(receipt)
                statement = StatementExtraction(document_kind='bank_statement', currency=currency, rows=[], complete=True)
                self.assertEqual(statement.currency, currency, 'Drafts preserve source currency; commit validates it before financial posting.')
        receipt = DocumentExtraction(document_type='receipt', total=12.59)
        self.assertIsNone(receipt.currency)
        with self.assertRaisesRegex(ValueError, 'identificar a moeda'):
            validate_document_extraction(receipt)
        currency_schema = _strict_schema(DocumentExtraction)['properties']['currency']
        self.assertEqual({part['type'] for part in currency_schema['anyOf']}, {'string', 'null'})
        self.assertIn('never relabel or convert', DOCUMENT_FACTS_PROMPT)

    def test_statement_total_is_blocked_on_receipt_route_even_if_currency_is_brl(self):
        for kind in ('bank_statement', 'card_statement', 'Credit Card Statement', 'extrato bancário', 'fatura de cartão'):
            with self.subTest(kind=kind):
                receipt = DocumentExtraction(document_type=kind, total=3869.18, currency='R$')
                with self.assertRaisesRegex(ValueError, 'confira cada linha'):
                    validate_document_extraction(receipt)
        for kind in ('receipt', 'invoice', 'boleto'):
            self.assertEqual(validate_document_extraction(DocumentExtraction(
                document_type=kind, total=12.59, currency='R$'))['total'], Decimal('12.59'))

    def test_failed_receipt_pdf_can_be_explicitly_rescanned_with_same_bytes_and_batch_id(self):
        data = pdf_bytes(pages=1)
        digest = hashlib.sha256(data).hexdigest()
        batch_id = uuid4()
        db = BatchDB()
        service = Mock()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, service=service)))
        receipt = DocumentExtraction(document_type='bank_statement', total=3869.18, currency='R$')
        with patch('app.main.store_media', return_value='original-source.pdf') as receipt_media, \
             patch('app.main.XAITextProvider.extract_text', return_value=receipt):
            with self.assertRaises(HTTPException) as failed:
                extract_document_data(data, 'application/pdf', digest, 'itau.pdf', 'mobile_capture', db, request)
        self.assertEqual(failed.exception.status_code, 422)
        self.assertIn('Fatura / extrato', failed.exception.detail)
        self.assertEqual(receipt_media.call_args.args[1:3], (data, digest))
        service.process.assert_not_called()
        for collection in ('raw_events', 'transactions', 'provisional_transactions', 'capture_batches'):
            self.assertFalse(db[collection].docs)

        statement = StatementExtraction(document_kind='bank_statement', currency='R$', rows=[
            {'date': '2026-10-01', 'amount': '-12.59', 'description': 'Synthetic store', 'kind': 'expense'}
        ], complete=True)
        with patch('app.capture_batches.extract_statement', return_value=statement) as extract, \
             patch('app.capture_batches.store_media', return_value='original-source.pdf') as batch_media, \
             patch.object(db.statement_extractions, 'update_one', return_value=None):
            first = scan_sync(UploadFile(filename='itau.pdf', file=io.BytesIO(data)), batch_id, request)
            repeated = scan_sync(UploadFile(filename='itau.pdf', file=io.BytesIO(data)), batch_id, request)
        self.assertEqual(first['event_id'], str(batch_id))
        self.assertEqual(first['batch']['sha256'], digest)
        self.assertEqual(first['batch']['rows'][0]['amount'], '-12.59')
        self.assertEqual(first, repeated)
        self.assertTrue(first['needs_review'])
        self.assertEqual(len(db.capture_batches.docs), 1)
        self.assertEqual(batch_media.call_args.args[1:3], (data, digest))
        extract.assert_called_once_with('itau.pdf', data, None)
        for collection in ('raw_events', 'transactions', 'provisional_transactions'):
            self.assertFalse(db[collection].docs)


if __name__ == '__main__':
    unittest.main()
