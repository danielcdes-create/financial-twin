"""NF04-NF06 regressions using production ingestion and synthetic source bytes."""
import hashlib
import io
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).parents[1]))
from app import main
from app.capture_batches import BatchCommit, BatchReview, analyze, commit, preview, scan_sync, candidate_account
from app.statement_extraction import extract_table
from app.transaction_service import TransactionService
import test_capture_batches as batch_helpers
from test_architecture_failures import rollback_fake


class DomainBatchIngestionTests(unittest.TestCase):
    setUp = batch_helpers.CaptureBatchTests.setUp
    review = batch_helpers.CaptureBatchTests.review
    candidate = batch_helpers.CaptureBatchTests.candidate
    confirm = batch_helpers.CaptureBatchTests.confirm

    def test_table_foreign_currency_is_blocked_before_brl_confirmation(self):
        for currency in ('USD', 'EUR'):
            data = f'data;descricao;valor;tipo;moeda\n01/10/2026;Synthetic shop;-12.59;debito;{currency}\n'.encode()
            workbook = Workbook()
            workbook.active.append(['data', 'descricao', 'valor', 'tipo', 'moeda'])
            workbook.active.append(['2026-10-01', 'Synthetic shop', -12.59, 'debito', currency])
            stream = io.BytesIO(); workbook.save(stream); workbook.close()
            for filename, source in [('source.csv', data), ('source.xlsx', stream.getvalue())]:
                with self.subTest(currency=currency, format=filename):
                    parsed = extract_table(filename, source)
                    self.assertEqual(parsed.currency, currency)
                    line = {'row_id': 1, **parsed.rows[0].model_dump(mode='json')}
                    self.batch['rows'] = [line]
                    review = self.review([line], acknowledge_brl=True)
                    report, _ = analyze(self.db, self.batch, review)
                    self.assertEqual(report['insert_count'], 0)
                    with self.assertRaises(HTTPException) as error:
                        commit(self.id, self.confirm(review), self.db)
                    self.assertEqual(error.exception.status_code, 422)
                    self.assertEqual(self.batch['status'], 'draft')
                    self.assertFalse(self.db.provisional_transactions.docs)
                    self.assertFalse(self.db.raw_events.docs)

    def test_unknown_currency_requires_explicit_brl_acknowledgement(self):
        source = b'data;descricao;valor;tipo\n01/10/2026;Synthetic shop;-12.59;debito\n'
        line = {'row_id': 1, **extract_table('source.csv', source).rows[0].model_dump(mode='json')}
        self.batch['rows'] = [line]
        self.assertIsNone(line['currency'])
        with self.assertRaises(HTTPException): commit(self.id, self.confirm(self.review([line])), self.db)
        review = self.review([line], acknowledge_brl=True)
        result = commit(self.id, self.confirm(review), self.db)
        self.assertEqual(result['inserted_count'], 1)
        document = self.db.provisional_transactions.docs[0]
        self.assertEqual(document['valor'].to_decimal(), Decimal('-12.59'))
        self.assertEqual(document['moeda'], 'BRL')
        self.assertIsNone(document['source_currency'])
        self.assertTrue(document['currency_acknowledged_brl'])

    def test_foreign_currency_cannot_be_overridden_and_mixed_file_has_no_prefix(self):
        brl = {**self.line, 'currency': 'BRL'}
        usd = {**self.line, 'row_id': 2, 'currency': 'USD'}
        self.batch['rows'] = [brl, usd]
        with self.assertRaises(HTTPException):
            analyze(self.db, self.batch, self.review([brl, {**usd, 'currency': 'BRL'}], acknowledge_brl=True))
        with self.assertRaises(HTTPException): commit(self.id, self.confirm(self.review([brl, usd])), self.db)
        self.assertEqual(self.batch['status'], 'draft')
        self.assertFalse(self.db.provisional_transactions.docs)
        self.assertFalse(self.db.raw_events.docs)

    def test_equal_unreferenced_source_rows_allow_explicit_distinct_confirmation(self):
        second = {**self.line, 'row_id': 2, 'confirmed_distinct': True}
        self.batch['rows'] = [self.line, second]
        body = self.confirm(self.review([self.line, second]))
        report, _ = analyze(self.db, self.batch, body)
        self.assertEqual([row['status'] for row in report['rows']], ['new', 'possible_duplicate'])
        result = commit(self.id, body, self.db)
        self.assertEqual(result['inserted_count'], 2)
        self.assertEqual(sum(row['valor'].to_decimal() for row in self.db.provisional_transactions.docs), Decimal('-25.18'))
        self.assertTrue(self.db.provisional_transactions.docs[1]['confirmed_distinct'])
        self.assertTrue(commit(self.id, body, self.db)['duplicate_request'])
        self.assertEqual(len(self.db.provisional_transactions.docs), 2)
        self.assertEqual(len(self.db.raw_events.docs), 2)
        self.assertTrue(self.db.audit_log.docs[0]['rows'][1]['confirmed_distinct'])

    def test_equal_existing_unreferenced_record_allows_explicit_distinction(self):
        self.db.transactions.docs.append(self.candidate())
        result = commit(self.id, self.confirm(self.review([{**self.line, 'confirmed_distinct': True}])), self.db)
        self.assertEqual(result['inserted_count'], 1)
        self.assertEqual(len(self.db.transactions.docs), 1)

    def test_capture_profile_precedes_historical_account_and_transfer_endpoints(self):
        context = {'profile': 'santander', 'ids': [self.account]}
        capture = {'account_id': self.account, 'account_profile_id': 'itau'}
        self.assertEqual(candidate_account(capture, context, True), 'different')
        capture['account_profile_id'] = 'santander'
        context['ids'] = []
        self.assertEqual(candidate_account(capture, context, True), 'same')
        transfer = {**capture, 'tipo': 'transferencia', 'transfer': {'from_account_key': 'itau', 'to_account_key': 'santander'}}
        self.assertEqual(candidate_account(transfer, context, True), 'same')
        self.assertEqual(candidate_account(transfer, {**context, 'profile': 'nubank'}, True), 'different')

    def test_same_bank_reference_never_allows_distinct_override(self):
        first = {**self.line, 'bank_ref': 'BANK-1'}
        second = {**first, 'row_id': 2, 'confirmed_distinct': True}
        self.batch['rows'] = [first, second]
        result = commit(self.id, self.confirm(self.review([first, second])), self.db)
        self.assertEqual(result['inserted_count'], 1)
        self.assertEqual(result['rows'][1]['status'], 'duplicate')

    def test_original_bank_reference_cannot_be_changed_to_bypass_duplicate(self):
        self.line['bank_ref'] = 'BANK-ORIGINAL'
        for reference in (None, 'BANK-OTHER'):
            with self.subTest(reference=reference), self.assertRaises(HTTPException):
                analyze(self.db, self.batch, self.review([{**self.line, 'bank_ref': reference, 'confirmed_distinct': True}]))
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_unresolved_selected_coincidence_has_no_financial_prefix(self):
        second = {**self.line, 'row_id': 2}
        self.batch['rows'] = [self.line, second]
        with self.assertRaises(HTTPException) as error:
            commit(self.id, self.confirm(self.review([self.line, second])), self.db)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.batch['status'], 'draft')
        self.assertFalse(self.db.provisional_transactions.docs)
        self.assertFalse(self.db.raw_events.docs)

    def test_old_table_draft_recovers_currency_from_original_without_rewriting_history(self):
        source = b'data;descricao;valor;tipo;moeda\n01/10/2026;Synthetic shop;-12.59;debito;USD\n'
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'retained.csv'; path.write_bytes(source)
            self.batch.update(filename='source.csv', storage_key=str(path), sha256=hashlib.sha256(source).hexdigest())
            before = dict(self.batch['rows'][0])
            with self.assertRaises(HTTPException): analyze(self.db, self.batch, self.review())
            review = self.review([{**self.line, 'currency': 'USD'}], acknowledge_brl=True)
            report, _ = analyze(self.db, self.batch, review)
            self.assertEqual(report['insert_count'], 0)
            self.assertEqual(self.batch['rows'][0], before)
            self.assertEqual(path.read_bytes(), source)

    def test_pre_currency_receipt_replays_with_same_ack_and_rejects_changed_review(self):
        original_review = self.review().model_dump(mode='json')
        original_review.pop('acknowledge_brl')
        for row in original_review['rows']: row.pop('currency')
        old_token = 'f' * 64
        self.batch.update(status='committed', commit_token=old_token, review=original_review,
                          result={'persisted': True, 'batch_id': str(self.id), 'inserted_count': 1})
        body = BatchCommit(**original_review, preview_token=old_token)
        self.assertTrue(commit(self.id, body, self.db)['duplicate_request'])
        altered = BatchCommit(**{**original_review, 'rows': [{**self.line, 'amount': '-99.00'}]}, preview_token=old_token)
        with self.assertRaises(HTTPException): commit(self.id, altered, self.db)
        self.assertFalse(self.db.provisional_transactions.docs)

    def test_currency_scan_preserves_original_bytes_and_draft(self):
        data = b'data;descricao;valor;tipo;moeda\n01/10/2026;Synthetic shop;-12.59;debito;USD\n'
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=self.db)))
        identity = uuid4()
        with patch('app.capture_batches.store_media', return_value='retained-source.csv') as store, patch.object(self.db.statement_extractions, 'update_one'):
            result = scan_sync(UploadFile(filename='foreign.csv', file=io.BytesIO(data)), identity, request)
        self.assertTrue(result['draft_persisted'])
        self.assertEqual(result['batch']['rows'][0]['currency'], 'USD')
        self.assertEqual(store.call_args.args[1], data)
        self.assertEqual(store.call_args.args[2], hashlib.sha256(data).hexdigest())
        self.assertFalse(self.db.provisional_transactions.docs)


class DomainLegacyIngestionTests(unittest.TestCase):
    def setUp(self):
        self.db = batch_helpers.BatchDB(); self.service = TransactionService(self.db)
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=self.db, service=self.service)))
        self.identity = uuid4()

    def apply(self, data, identity=None):
        with patch('app.main.store_media', return_value='retained-original.csv') as store:
            result = main.import_statement_sync(UploadFile(filename='synthetic.csv', file=io.BytesIO(data)), 'synthetic-bank', self.request, event_id=identity or self.identity)
        self.assertEqual(store.call_args.args[1], data)
        return result

    def test_legacy_import_distinct_bank_refs_are_two_reviewable_movements(self):
        data = (b'data;descricao;valor;fluxo;moeda;conta;identificador_transacao\n'
                b'01/10/2026;Synthetic Coffee Shop;-12.59;saida;BRL;ACCOUNT-1;BANK-REAL-1\n'
                b'01/10/2026;Synthetic Coffee Shop;-12.59;saida;BRL;ACCOUNT-1;BANK-REAL-2\n')
        first = self.apply(data)
        self.assertEqual(first['accepted'], 2)
        self.assertEqual(len(self.db.provisional_transactions.docs), 2)
        self.assertEqual(sum(row['valor'].to_decimal() for row in self.db.provisional_transactions.docs), Decimal('-25.18'))
        self.assertEqual({row['bank_transaction_ref'] for row in self.db.provisional_transactions.docs}, {'BANK-REAL-1', 'BANK-REAL-2'})
        self.assertEqual({row['account_identity'] for row in self.db.provisional_transactions.docs}, {'ACCOUNT-1'})
        with patch('app.main.store_media') as store:
            again = main.import_statement_sync(UploadFile(filename='renamed.csv', file=io.BytesIO(data)), 'synthetic-bank', self.request, event_id=self.identity)
        self.assertEqual(first, again); store.assert_not_called()
        reexport = (b'identificador_transacao;conta;moeda;fluxo;valor;descricao;data\n'
                    b'BANK-REAL-2;ACCOUNT-1;BRL;saida;-12.59;Synthetic Coffee Shop;01/10/2026\n'
                    b'BANK-REAL-1;ACCOUNT-1;BRL;saida;-12.59;Synthetic Coffee Shop;01/10/2026\n')
        self.apply(reexport, uuid4())
        self.assertEqual(len(self.db.provisional_transactions.docs), 2)

    def test_legacy_foreign_unknown_and_mixed_source_never_apply_prefix(self):
        for currency in ('USD', 'EUR', ''):
            data = ('data;descricao;valor;fluxo;moeda\n'
                    '01/10/2026;Valid BRL shop;-12.59;saida;BRL\n'
                    f'01/10/2026;Other shop;-12.59;saida;{currency}\n').encode()
            with patch('app.main.store_media', return_value='retained-original.csv') as store:
                with self.assertRaises(HTTPException) as error:
                    main.import_statement_sync(UploadFile(filename='synthetic.csv', file=io.BytesIO(data)), 'synthetic-bank', self.request, event_id=uuid4())
            self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(store.call_args.args[1], data)
            self.assertFalse(self.db.raw_events.docs)
            self.assertFalse(self.db.provisional_transactions.docs)

    def test_failure_in_second_legacy_row_rolls_back_prefix_and_ack(self):
        for name in ('raw_events', 'provisional_transactions', 'transaction_evidence', 'review_queue', 'transactions', 'audit_log', 'batch_commands'):
            self.db[name]
        data = (b'data;descricao;valor;fluxo;moeda;conta;identificador_transacao\n'
                b'01/10/2026;Synthetic Coffee Shop;-12.59;saida;BRL;ACCOUNT-1;BANK-REAL-1\n'
                b'01/10/2026;Synthetic Coffee Shop;-12.59;saida;BRL;ACCOUNT-1;BANK-REAL-2\n')
        original = self.service._finish; calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise RuntimeError('synthetic second-row outage')
            return original(*args, **kwargs)
        with patch('app.main.mongo_transaction', rollback_fake), patch.object(TransactionService, '_finish', side_effect=fail_second):
            with self.assertRaises(RuntimeError): self.apply(data)
        self.assertFalse(self.db.raw_events.docs)
        self.assertFalse(self.db.provisional_transactions.docs)
        self.assertEqual(self.db.batch_commands.docs[0]['status'], 'awaiting_upload')
        result = self.apply(data)
        self.assertTrue(result['persisted']); self.assertEqual(result['accepted'], 2)


if __name__ == '__main__': unittest.main()
