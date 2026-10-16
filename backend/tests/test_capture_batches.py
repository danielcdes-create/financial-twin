import copy
import hashlib
import io
import sys
import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.auth import require_session
from app.capture_batches import BatchReview, BatchCommit, BatchDiscard, analyze, commit, discard_batch, file_status, fingerprint, preview, router, similar_description
from app.statement_extraction import extract_table, extract_statement, StatementExtraction
from app.analytics import calculate_financial_totals
from test_movement_deletion import DeletionCollection, values
from test_ingestion import FakeDB, Cursor
from test_architecture_failures import rollback_fake, FailsOnce


class BatchCursor(Cursor):
    def sort(self, *_): return self


class BatchCollection(DeletionCollection):
    def _matches(self, document, query):
        for key, wanted in query.items():
            if key == '$or':
                if not any(self._matches(document, part) for part in wanted): return False
                continue
            actual = values(document, key.split('.'))
            if isinstance(wanted, dict):
                def matches(value):
                    for operator, expected in wanted.items():
                        if operator == '$in' and value not in expected: return False
                        if operator == '$gte' and (value is None or value < expected): return False
                        if operator == '$lte' and (value is None or value > expected): return False
                        if operator == '$lt' and (value is None or value >= expected): return False
                        if operator == '$ne' and value == expected: return False
                    return True
                if not any(matches(value) for value in actual): return False
            elif not any(value == wanted for value in actual): return False
        return True

    def find(self, query, projection=None): return BatchCursor(row for row in self.docs if self._matches(row, query))


class BatchDB(FakeDB):
    def __missing__(self, key): self[key] = BatchCollection(); return self[key]


class FileImportStatusTests(unittest.TestCase):
    def setUp(self):
        self.db = BatchDB()
        self.data = b'%PDF-1.7\nSynthetic original protected statement bytes'
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.event_id = str(uuid4())
        self.batch_id = str(uuid4())
        self.app = FastAPI(); self.app.include_router(router); self.app.state.db = self.db
        self.app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(self.app)

    def batch(self, **changes):
        document = {'_id': self.batch_id, 'batch_id': self.batch_id, 'sha256': self.digest,
            'filename': 'original.pdf', 'status': 'committed',
            'result': {'persisted': True, 'batch_id': self.batch_id}, **changes}
        self.db.capture_batches.docs.append(document)
        return document

    def status(self, data=None, filename='original.pdf', event_id=None):
        return self.client.post('/v1/capture-batches/file-status', data={'event_id': event_id or self.event_id},
            files={'file': (filename, self.data if data is None else data, 'application/pdf')})

    def test_exact_original_bytes_acknowledge_import_even_after_filename_change(self):
        self.batch()
        response = self.status(filename='renamed.pdf')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'event_id': self.event_id, 'sha256': self.digest,
            'imported': True, 'batch_id': self.batch_id})
        self.assertNotEqual(self.event_id, response.json()['batch_id'])

    def test_same_filename_with_different_bytes_is_not_acknowledged(self):
        self.batch()
        different = self.data + b' changed'
        response = self.status(data=different)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'event_id': self.event_id,
            'sha256': hashlib.sha256(different).hexdigest(), 'imported': False})

    def test_absent_draft_or_discarded_batch_does_not_acknowledge_import(self):
        for status in (None, 'draft', 'discarded'):
            with self.subTest(status=status):
                self.db.capture_batches.docs.clear()
                if status: self.batch(status=status)
                self.assertEqual(self.status().json(), {'event_id': self.event_id,
                    'sha256': self.digest, 'imported': False})

    def test_committed_batch_requires_matching_persisted_result(self):
        for changes in ({'result': None}, {'result': {}},
                        {'result': {'persisted': False, 'batch_id': self.batch_id}},
                        {'result': {'persisted': 1, 'batch_id': self.batch_id}},
                        {'result': {'persisted': True, 'batch_id': str(uuid4())}},
                        {'_id': str(uuid4())}, {'batch_id': 'invalid-id'}):
            with self.subTest(changes=changes):
                self.db.capture_batches.docs.clear(); self.batch(**changes)
                self.assertFalse(self.status().json()['imported'])

    def test_valid_committed_batch_is_found_after_draft_or_malformed_result(self):
        self.batch(status='draft')
        self.batch(result={'persisted': True, 'batch_id': str(uuid4())})
        self.batch()
        self.assertTrue(self.status().json()['imported'])

    def test_status_never_extracts_stores_or_mutates_collections(self):
        self.batch()
        for collection in ('statement_extractions', 'media_assets', 'raw_events', 'provisional_transactions', 'transactions', 'audit_log'):
            self.db[collection].docs.append({'_id': 'synthetic-' + collection})
        before = {name: copy.deepcopy(collection.docs) for name, collection in self.db.items()}
        with patch('app.capture_batches.extract_statement', side_effect=AssertionError('Unexpected extraction')) as extraction, \
             patch('app.capture_batches.store_media', side_effect=AssertionError('Unexpected media write')) as storage, \
             patch('app.capture_batches.run_upload', side_effect=AssertionError('Unexpected upload worker')) as upload, \
             patch.object(BatchCollection, 'insert_one', side_effect=AssertionError('Unexpected insert')), \
             patch.object(BatchCollection, 'update_one', side_effect=AssertionError('Unexpected update')):
            self.assertTrue(self.status().json()['imported'])
        extraction.assert_not_called(); storage.assert_not_called(); upload.assert_not_called()
        self.assertEqual({name: collection.docs for name, collection in self.db.items()}, before)

    def test_empty_or_oversized_file_is_rejected_before_database_lookup(self):
        with patch.object(self.db.capture_batches, 'find') as lookup:
            for data in (b'', b'x' * (10 * 1024 * 1024 + 1)):
                with self.subTest(size=len(data)):
                    self.assertEqual(self.status(data=data).status_code, 413)
            lookup.assert_not_called()

    def test_file_read_is_bounded_and_maximum_size_is_accepted(self):
        data = b'x' * (10 * 1024 * 1024)
        stream = io.BytesIO(data)
        with patch.object(stream, 'read', wraps=stream.read) as read:
            result = file_status(UploadFile(file=stream, filename='synthetic.pdf'), uuid4(), self.db)
        self.assertFalse(result['imported'])
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        read.assert_called_once_with(10 * 1024 * 1024 + 1)

    def test_invalid_or_missing_event_id_and_missing_file_are_rejected(self):
        with patch.object(self.db.capture_batches, 'find') as lookup:
            self.assertEqual(self.status(event_id='invalid-id').status_code, 422)
            self.assertEqual(self.client.post('/v1/capture-batches/file-status',
                files={'file': ('synthetic.pdf', self.data, 'application/pdf')}).status_code, 422)
            self.assertEqual(self.client.post('/v1/capture-batches/file-status',
                data={'event_id': self.event_id}).status_code, 422)
            lookup.assert_not_called()

    def test_status_requires_authenticated_session(self):
        self.app.dependency_overrides.clear()
        with patch.object(self.db.capture_batches, 'find') as lookup:
            self.assertEqual(self.status().status_code, 401)
            lookup.assert_not_called()


class CaptureBatchTests(unittest.TestCase):
    def setUp(self):
        self.db = BatchDB()
        self.account = ObjectId()
        self.db.accounts.docs.append({'_id': self.account, 'tipo': 'conta_corrente', 'instituicao': 'Itaú'})
        self.id = uuid4()
        self.line = {'row_id': 1, 'date': '2026-10-01', 'amount': '-12.59', 'description': 'Mixpão Padaria', 'kind': 'expense', 'bank_ref': None}
        self.batch = {'_id': str(self.id), 'batch_id': str(self.id), 'sha256': 'a' * 64,
            'storage_key': 'synthetic', 'status': 'draft', 'complete': True, 'rows': [self.line]}
        self.db.capture_batches.docs.append(self.batch)
        for name in ('transactions', 'provisional_transactions', 'deleted_movements', 'raw_events', 'audit_log'):
            self.db[name]

    def review(self, lines=None, **changes):
        return BatchReview(account_ref='ledger:' + str(self.account), rows=lines or [self.line], **changes)

    def candidate(self, **changes):
        return {'_id': ObjectId(), 'data_evento': datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
            'valor': Decimal128('-12.59'), 'estabelecimento_original': 'Mixpão Padaria', 'account_id': self.account, **changes}

    def confirm(self, review):
        return BatchCommit(**review.model_dump(), preview_token=preview(self.id, review, self.db)['preview_token'])

    def test_existing_ledger_capture_and_deleted_duplicates_block(self):
        self.line['bank_ref'] = 'BANK-EXISTING'
        for collection, document in (
            ('transactions', self.candidate(bank_transaction_ref='BANK-EXISTING')),
            ('provisional_transactions', {**self.candidate(bank_transaction_ref='BANK-EXISTING'), 'provisional_id': 'captured', 'status': 'confirmed'}),
            ('deleted_movements', {'_id': 'removed', 'document': self.candidate(bank_transaction_ref='BANK-EXISTING')}),
        ):
            with self.subTest(source=collection):
                self.db[collection].docs.append(document)
                result, _ = analyze(self.db, self.batch, self.review())
                self.assertEqual(result['rows'][0]['status'], 'duplicate')
                self.assertEqual(result['insert_count'], 0)
                self.db[collection].docs.clear()

    def test_different_account_is_not_a_duplicate(self):
        self.db.transactions.docs.append(self.candidate(account_id=ObjectId()))
        self.assertEqual(analyze(self.db, self.batch, self.review())[0]['insert_count'], 1)

    def test_description_bank_prefix_marks_probable_with_readable_evidence(self):
        self.db.transactions.docs.append(self.candidate(estabelecimento_original='PIX ENVIADO MIXPAO PADARIA'))
        report, _ = analyze(self.db, self.batch, self.review())
        self.assertEqual((report['new_count'], report['duplicate_count'], report['possible_duplicate_count']), (0, 0, 1))
        outcome = report['rows'][0]
        self.assertEqual(outcome['confidence'], 'high')
        self.assertFalse(outcome['will_insert'])
        self.assertEqual(outcome['matches'][0]['description'], 'PIX ENVIADO MIXPAO PADARIA')
        self.assertEqual(outcome['matches'][0]['amount'], '-12.59')
        self.assertIn('Mesma conta', outcome['reason'])

    def test_nearby_day_is_probable_and_requires_individual_confirmation(self):
        self.db.transactions.docs.append(self.candidate(data_evento=datetime(2026, 10, 2, 15, tzinfo=timezone.utc)))
        report, _ = analyze(self.db, self.batch, self.review())
        self.assertEqual(report['rows'][0]['confidence'], 'high')
        self.assertEqual(report['insert_count'], 0)
        result = commit(self.id, self.confirm(self.review([{**self.line, 'confirmed_distinct': True}])), self.db)
        self.assertEqual(result['inserted_count'], 1)

    def test_generic_bank_words_never_confirm_or_suggest_high_confidence(self):
        for left, right in [('PIX ENVIADO', 'PIX RECEBIDO'), ('PAGAMENTO', 'PAGAMENTO'), ('PIX 12345', 'PIX 12345')]:
            self.assertFalse(similar_description(left, right))
        self.db.transactions.docs.append(self.candidate(estabelecimento_original='PIX ENVIADO'))
        report, _ = analyze(self.db, self.batch, self.review([{**self.line, 'description': 'PIX ENVIADO'}]))
        self.assertEqual(report['rows'][0]['confidence'], 'possible')
        self.assertEqual(report['rows'][0]['status'], 'possible_duplicate')

    def test_signed_cents_are_exact_and_different_bank_refs_remain_distinct(self):
        self.line['bank_ref'] = 'BANK-123'
        for change in ({'valor': Decimal128('12.59')}, {'valor': Decimal128('-12.60')}, {'bank_transaction_ref': 'OTHER'}):
            self.db.transactions.docs[:] = [self.candidate(**change)]
            report, _ = analyze(self.db, self.batch, self.review([{**self.line, 'bank_ref': 'BANK-123'}]))
            self.assertEqual(report['rows'][0]['status'], 'new')
        second = {**self.line, 'row_id': 2, 'bank_ref': 'OTHER'}
        self.batch['rows'].append(second); self.db.transactions.docs.clear()
        self.assertEqual(analyze(self.db, self.batch, self.review([{**self.line, 'bank_ref': 'BANK-123'}, second]))[0]['insert_count'], 2)

    def test_discard_is_idempotent_retains_other_batch_source_and_financial_history(self):
        other = {**copy.deepcopy(self.batch), '_id': str(uuid4()), 'batch_id': str(uuid4())}
        self.db.capture_batches.docs.append(other)
        self.db.transactions.docs.append(self.candidate())
        before = {name: copy.deepcopy(self.db[name].docs) for name in ('transactions', 'provisional_transactions', 'raw_events', 'deleted_movements')}
        body = BatchDiscard(expected_sha256=self.batch['sha256'])
        self.assertFalse(discard_batch(self.id, body, self.db)['duplicate_request'])
        self.assertTrue(discard_batch(self.id, body, self.db)['duplicate_request'])
        self.assertEqual(self.batch['status'], 'discarded')
        self.assertEqual(other['status'], 'draft')
        self.assertEqual(self.batch['storage_key'], 'synthetic')
        self.assertEqual(self.batch['rows'], [self.line])
        self.assertEqual(len(self.db.audit_log.docs), 1)
        for name, documents in before.items(): self.assertEqual(self.db[name].docs, documents)

    def test_discard_rejects_changed_source_committed_or_missing_batch(self):
        with self.assertRaises(HTTPException) as failure:
            discard_batch(self.id, BatchDiscard(expected_sha256='b' * 64), self.db)
        self.assertEqual(failure.exception.status_code, 409)
        commit(self.id, self.confirm(self.review()), self.db)
        with self.assertRaises(HTTPException) as failure:
            discard_batch(self.id, BatchDiscard(expected_sha256=self.batch['sha256']), self.db)
        self.assertEqual(failure.exception.status_code, 409)
        with self.assertRaises(HTTPException) as failure:
            discard_batch(uuid4(), BatchDiscard(expected_sha256='a' * 64), self.db)
        self.assertEqual(failure.exception.status_code, 404)

    def test_discard_blocks_stale_preview_and_rolls_back_on_audit_outage(self):
        body = self.confirm(self.review())
        self.db['audit_log'] = FailsOnce()
        with patch('app.capture_batches.mongo_transaction', rollback_fake):
            with self.assertRaises(RuntimeError): discard_batch(self.id, BatchDiscard(expected_sha256=self.batch['sha256']), self.db)
        self.assertEqual(self.db.capture_batches.docs[0]['status'], 'draft')
        discard_batch(self.id, BatchDiscard(expected_sha256=self.batch['sha256']), self.db)
        with self.assertRaises(HTTPException) as failure: commit(self.id, body, self.db)
        self.assertEqual(failure.exception.status_code, 409)
        with self.assertRaises(HTTPException): preview(self.id, self.review(), self.db)
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_unknown_account_requires_individual_confirmation(self):
        self.db.transactions.docs.append(self.candidate(account_id=None))
        report, _ = analyze(self.db, self.batch, self.review())
        self.assertEqual(report['rows'][0]['status'], 'possible_duplicate')
        self.assertEqual(report['insert_count'], 0)
        checked = self.review([{**self.line, 'confirmed_distinct': True}])
        self.assertEqual(analyze(self.db, self.batch, checked)[0]['insert_count'], 1)

    def test_same_day_value_different_description_is_only_possible(self):
        self.db.transactions.docs.append(self.candidate(estabelecimento_original='Outra loja'))
        report, _ = analyze(self.db, self.batch, self.review())
        self.assertEqual(report['rows'][0]['status'], 'possible_duplicate')
        self.assertEqual(report['insert_count'], 0)

    def test_duplicate_in_same_batch_inserts_only_first_selected_row(self):
        second = {**self.line, 'row_id': 2}
        self.batch['rows'].append(second)
        review = self.review([self.line, second])
        report, _ = analyze(self.db, self.batch, review)
        self.assertEqual(report['insert_count'], 1)
        self.assertEqual(report['rows'][1]['status'], 'possible_duplicate')
        review = self.review([{**self.line, 'selected': False}, second])
        report, _ = analyze(self.db, self.batch, review)
        self.assertFalse(report['rows'][0]['will_insert'])
        self.assertTrue(report['rows'][1]['will_insert'])

    def test_bank_identifier_blocks_even_after_amount_date_edit(self):
        self.line['bank_ref'] = 'BANK-123'
        self.db.provisional_transactions.docs.append({**self.candidate(valor=Decimal128('-99.00'), data_evento=datetime(2025, 1, 1, tzinfo=timezone.utc)),
            'provisional_id': 'bank-id', 'status': 'confirmed', 'bank_transaction_ref': 'BANK-123'})
        report, _ = analyze(self.db, self.batch, self.review([{**self.line, 'bank_ref': 'BANK-123'}]))
        self.assertEqual(report['rows'][0]['status'], 'duplicate')

    def test_intrinsic_bank_identifier_duplicate_different_amount(self):
        self.line['bank_ref'] = 'BANK-123'
        second = {**self.line, 'row_id': 2, 'amount': '-42.99', 'bank_ref': 'BANK-123'}
        self.batch['rows'].append(second)
        report, _ = analyze(self.db, self.batch, self.review([{**self.line, 'bank_ref': 'BANK-123'}, second]))
        self.assertEqual(report['insert_count'], 1)
        self.assertEqual(report['rows'][1]['status'], 'duplicate')

    def test_partial_source_and_invalid_fields_are_not_silently_inserted(self):
        self.batch['complete'] = False
        self.assertEqual(analyze(self.db, self.batch, self.review())[0]['insert_count'], 0)
        self.assertEqual(analyze(self.db, self.batch, self.review(acknowledge_partial=True))[0]['insert_count'], 1)
        for change in ({'date': None}, {'amount': None}, {'kind': 'own_transfer'}, {'kind': 'income'}, {'description': ''}):
            report, _ = analyze(self.db, self.batch, self.review([{**self.line, **change}], acknowledge_partial=True))
            self.assertEqual(report['rows'][0]['status'], 'invalid')

    def test_truncated_candidate_scan_is_never_treated_as_new(self):
        self.db.transactions.docs = [self.candidate() for _ in range(1001)]
        report, _ = analyze(self.db, self.batch, self.review())
        self.assertFalse(report['scan_complete'])
        self.assertEqual(report['insert_count'], 0)

    def test_duplicate_arriving_after_preview_is_blocked_at_commit(self):
        self.line['bank_ref'] = 'BANK-ARRIVING'
        body = self.confirm(self.review())
        self.db.transactions.docs.append(self.candidate(bank_transaction_ref='BANK-ARRIVING'))
        result = commit(self.id, body, self.db)
        self.assertEqual(result['inserted_count'], 0)
        self.assertEqual(result['rows'][0]['status'], 'duplicate')

    def test_confirmed_insert_exact_cents_date_account_idempotent(self):
        body = self.confirm(self.review())
        result = commit(self.id, body, self.db)
        self.assertEqual(result['inserted_count'], 1)
        row = self.db.provisional_transactions.docs[0]
        self.assertEqual(row['valor'].to_decimal(), Decimal('-12.59'))
        self.assertEqual(row['data_evento'], datetime(2026, 10, 1, 15, tzinfo=timezone.utc))
        self.assertEqual(row['account_profile_id'], 'itau')
        self.assertEqual(row['status'], 'confirmed')
        self.assertEqual(row['status_revisao'], 'validado')
        self.assertEqual(row['revisado_em'], row['confirmed_at'])
        self.assertTrue(commit(self.id, body, self.db)['duplicate_request'])
        self.assertEqual(len(self.db.raw_events.docs), 1)
        self.assertEqual(len(self.db.audit_log.docs), 1)

    def test_duplicate_confirmation_preserves_existing_record_review_and_history(self):
        self.line['bank_ref'] = 'BANK-PRESERVED'
        existing = self.candidate(status_revisao='pendente', bank_transaction_ref='BANK-PRESERVED')
        self.db.transactions.docs.append(existing)
        before = copy.deepcopy(existing)
        result = commit(self.id, self.confirm(self.review()), self.db)
        self.assertEqual(result['inserted_count'], 0)
        self.assertEqual(self.db.transactions.docs, [before])
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_edited_or_forged_rows_cannot_reuse_confirmation(self):
        body = self.confirm(self.review())
        edited = BatchCommit(**{**body.model_dump(), 'rows': [{**self.line, 'amount': '-20.00'}]})
        with self.assertRaises(HTTPException) as failure: commit(self.id, edited, self.db)
        self.assertEqual(failure.exception.status_code, 409)
        with self.assertRaises(HTTPException): analyze(self.db, self.batch, self.review([{**self.line, 'row_id': 2}]))
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_audit_outage_rolls_back_all_postings_and_completion(self):
        body = self.confirm(self.review())
        self.db['audit_log'] = FailsOnce()
        with patch('app.capture_batches.mongo_transaction', rollback_fake):
            with self.assertRaises(RuntimeError): commit(self.id, body, self.db)
        self.assertFalse(self.db.provisional_transactions.docs)
        self.assertFalse(self.db.raw_events.docs)
        self.assertEqual(self.db.capture_batches.docs[0]['status'], 'draft')

    def test_bill_payment_is_economically_neutral_and_refund_offsets_expense(self):
        lines = [{**self.line, 'kind': 'bill_payment'}, {**self.line, 'row_id': 2, 'kind': 'refund', 'amount': '2.59'}]
        self.batch['rows'] = lines
        commit(self.id, self.confirm(self.review(lines)), self.db)
        totals = calculate_financial_totals([], self.db.provisional_transactions.docs)
        self.assertEqual(totals['income'], 0)
        self.assertEqual(totals['expenses'], Decimal('-2.59'))

    def test_csv_missing_year_direction_and_nonstatement_file_remain_reviewable(self):
        data = b'data;descricao;valor;tipo\n01/10/2026;Padaria;12,59;debito\n01/10;PIX Pessoa;50,00;\n'
        extracted = extract_table('statement.csv', data)
        self.assertEqual(extracted.rows[0].amount, Decimal('-12.59'))
        self.assertEqual(extracted.rows[1].date, None)
        self.assertEqual(extracted.rows[1].kind, 'unknown')
        with self.assertRaises(ValueError): extract_statement('not-image.png', b'not an image')

    def test_pdf_over_ten_pages_is_rejected_instead_of_truncated(self):
        import pymupdf as fitz
        pdf = fitz.open()
        for _ in range(11): pdf.new_page()
        data = pdf.tobytes(); pdf.close()
        with self.assertRaises(ValueError): extract_statement('statement.pdf', data)

    def test_text_pdf_sends_every_page_to_extraction_without_posting(self):
        import pymupdf as fitz
        pdf = fitz.open()
        for text in ['Fatura Itau - 01/10/2026 Padaria compra R$ 12,59 - primeira pagina',
                     'Fatura Itau - 02/10/2026 Mercado compra R$ 32,10 - segunda pagina']:
            pdf.new_page().insert_text((40, 60), text)
        data = pdf.tobytes(); pdf.close()
        expected = StatementExtraction(document_kind='card_statement', rows=[], complete=True)
        with patch('app.statement_extraction.extract_ai', return_value=expected) as extract:
            self.assertIs(extract_statement('itau_extrato_092026.pdf', data), expected)
        content = extract.call_args.args[0]
        self.assertEqual(content[0]['type'], 'input_text')
        self.assertIn('primeira pagina', content[0]['text'])
        self.assertIn('segunda pagina', content[0]['text'])
        self.assertFalse(self.db.raw_events.docs)
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_scanned_pdf_sends_every_page_as_image_without_posting(self):
        import pymupdf as fitz
        pdf = fitz.open()
        for _ in range(2):
            page = pdf.new_page(width=200, height=200)
            page.draw_rect(fitz.Rect(20, 20, 180, 180), color=(0, 0, 0))
        data = pdf.tobytes(); pdf.close()
        expected = StatementExtraction(document_kind='bank_statement', rows=[], complete=False)
        with patch('app.statement_extraction.extract_ai', return_value=expected) as extract:
            self.assertIs(extract_statement('scanned.pdf', data), expected)
        content = extract.call_args.args[0]
        self.assertEqual(len(content), 2)
        self.assertTrue(all(part['type'] == 'input_image' and part['image_url'].startswith('data:image/png;base64,') for part in content))
        self.assertFalse(self.db.raw_events.docs)
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_encrypted_pdf_requests_cpf_without_disclosing_it(self):
        import pymupdf as fitz
        pdf = fitz.open(); pdf.new_page()
        data = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='password')
        pdf.close()
        with self.assertRaisesRegex(ValueError, 'informe seu CPF'):
            extract_statement('encrypted.pdf', data)

    def test_brasilia_day_not_utc_day_is_used_for_duplicates(self):
        self.db.transactions.docs.append(self.candidate(data_evento=datetime(2026, 10, 2, 1, tzinfo=timezone.utc)))
        result = analyze(self.db, self.batch, self.review())[0]['rows'][0]
        self.assertEqual(result['status'], 'possible_duplicate')
        self.assertEqual(result['matches'][0]['date'], '2026-10-01')

    def test_corrupt_image_rejected_before_provider_call(self):
        with patch('app.statement_extraction.extract_ai') as provider:
            with self.assertRaises(ValueError): extract_statement('statement.png', b'\x89PNG\r\n\x1a\ninvalid')
            provider.assert_not_called()

    def test_oversized_pdf_page_rejected_before_rendering(self):
        import pymupdf as fitz
        pdf = fitz.open(); pdf.new_page(width=10000, height=10000)
        data = pdf.tobytes(); pdf.close()
        with patch('app.statement_extraction.extract_ai') as provider:
            with self.assertRaises(ValueError): extract_statement('huge.pdf', data)
            provider.assert_not_called()
