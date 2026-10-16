import hashlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi import HTTPException, UploadFile
from app.pdf_input import PDFPasswordError, cpf_digits, read_pdf
from app.statement_extraction import StatementExtraction, extract_statement
from app.capture_batches import scan_sync
from test_capture_batches import BatchDB

CPF = '01234567890'  # Synthetic password; preserve its leading zero.


def pdf_bytes(pages=10, scanned=False, password=None):
    import pymupdf as fitz
    pdf = fitz.open()
    for number in range(1, pages + 1):
        page = pdf.new_page()
        if scanned:
            page.draw_rect(fitz.Rect(40, 40, 100, 100), color=(0, 0, 0))
        else:
            page.insert_text((40, 60), f'PAGE {number}: 01/10/2026 Synthetic card statement purchase amount 12.59 BRL')
    kwargs = {'encryption': fitz.PDF_ENCRYPT_AES_256, 'owner_pw': 'synthetic-owner', 'user_pw': password} if password else {}
    data = pdf.tobytes(**kwargs); pdf.close()
    return data


class PDFInputTests(unittest.TestCase):
    def test_ten_text_pages_are_all_read(self):
        content = read_pdf(pdf_bytes())
        for number in range(1, 11): self.assertIn(f'PAGE {number}:', content.text)
        self.assertFalse(content.images)

    def test_ten_scanned_a4_pages_are_all_rendered(self):
        content = read_pdf(pdf_bytes(scanned=True))
        self.assertIsNone(content.text)
        self.assertEqual(len(content.images), 10)
        self.assertTrue(all(data.startswith(b'\x89PNG') and mime == 'image/png' for data, mime in content.images))

    def test_eleven_pages_are_rejected_without_truncation(self):
        with self.assertRaisesRegex(ValueError, 'até 10 páginas'):
            read_pdf(pdf_bytes(pages=11))

    def test_protected_pdf_uses_formatted_cpf_and_preserves_original(self):
        data = pdf_bytes(password=CPF)
        before = hashlib.sha256(data).hexdigest()
        content = read_pdf(data, '012.345.678-90')
        self.assertIn('PAGE 10:', content.text)
        self.assertEqual(hashlib.sha256(data).hexdigest(), before)
        import pymupdf as fitz
        original = fitz.open(stream=data, filetype='pdf')
        self.assertTrue(original.is_encrypted); original.close()

    def test_protected_scanned_pdf_is_opened_before_rendering(self):
        self.assertEqual(len(read_pdf(pdf_bytes(scanned=True, password=CPF), CPF).images), 10)

    def test_missing_wrong_or_malformed_cpf_is_not_echoed(self):
        data = pdf_bytes(pages=1, password=CPF)
        for value in [None, '98765432100', '123', 'secret-value']:
            with self.assertRaises(PDFPasswordError) as failure: read_pdf(data, value)
            self.assertNotIn(CPF, str(failure.exception))
            if value: self.assertNotIn(value, str(failure.exception))

    def test_plain_pdf_does_not_require_cpf(self):
        self.assertIn('PAGE 1:', read_pdf(pdf_bytes(pages=1), 'invalid-unused').text)
        self.assertEqual(cpf_digits('012.345.678-90'), CPF)

    def test_cpf_is_not_added_to_ai_input(self):
        expected = StatementExtraction(document_kind='card_statement', rows=[], complete=True)
        with patch('app.statement_extraction.extract_ai', return_value=expected) as ai:
            self.assertIs(extract_statement('protected.pdf', pdf_bytes(password=CPF), CPF), expected)
        self.assertNotIn(CPF, str(ai.call_args))
        self.assertIn('PAGE 10:', str(ai.call_args))

    def test_password_failure_keeps_batch_unpersisted_and_returns_retryable_status(self):
        db = BatchDB()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
        file = UploadFile(filename='protected.pdf', file=io.BytesIO(pdf_bytes(pages=1, password=CPF)))
        with self.assertRaises(HTTPException) as failure: scan_sync(file, uuid4(), request)
        self.assertEqual(failure.exception.status_code, 428)
        self.assertIn('CPF', failure.exception.detail)
        for collection in ('capture_batches', 'statement_extractions', 'raw_events', 'provisional_transactions'):
            self.assertFalse(db[collection].docs)

    def test_receipt_uses_the_same_pdf_unlock_and_ten_page_reader(self):
        from app.main import extract_document_data
        from app.schemas_document import DocumentExtraction
        db = BatchDB(); state = SimpleNamespace(service=SimpleNamespace(process=lambda event: {'persisted': True}))
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        extraction = DocumentExtraction(document_type='receipt', merchant='Synthetic store', total=12.59, currency='BRL', items=[])
        data = pdf_bytes(password=CPF)
        with patch('app.main.store_media', return_value='encrypted-original.pdf'), \
             patch('app.main.XAITextProvider.extract_text', return_value=extraction) as provider, \
             patch('app.main.finish_receipt', side_effect=lambda *args: {'persisted': True}):
            result = extract_document_data(data, 'application/pdf', hashlib.sha256(data).hexdigest(), 'receipt.pdf', 'mobile_capture', db, request, CPF)
        self.assertTrue(result['persisted'])
        self.assertIn('PAGE 10:', provider.call_args.args[0])
        self.assertNotIn(CPF, str(provider.call_args))

    def test_http_cpf_field_is_passed_transiently_without_response_echo(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.auth import require_session
        from app.capture_batches import router
        app = FastAPI(); app.include_router(router); app.state.db = BatchDB()
        app.dependency_overrides[require_session] = lambda: None
        with patch('app.capture_batches.scan_sync', return_value={'persisted': True}) as worker:
            response = TestClient(app).post('/v1/capture-batches/scan', data={'batch_id': str(uuid4()), 'pdf_cpf': CPF},
                files={'file': ('protected.pdf', pdf_bytes(pages=1, password=CPF), 'application/pdf')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(worker.call_args.args[-1], CPF)
        self.assertNotIn(CPF, response.text)


if __name__ == '__main__': unittest.main()
