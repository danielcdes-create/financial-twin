import sys,unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
from fastapi import HTTPException
from app.source_tokens import source_token,require_source_token
from app.main import validate_signed_flow
from app.schemas_document import DocumentExtraction
from app.providers.import_provider import ImportFinancialProvider
from app.synthetic_testing import require_synthetic_target
from scripts import check_account_security

class Audit1209Tests(unittest.TestCase):
    def test_missing_and_stale_source_tokens_fail_closed(self):
        row={'valor':'-10','natureza':'COMPRA'};token=source_token(row)
        require_source_token(row,token)
        for expected,code in [(None,428),(source_token({**row,'natureza':'TRANSFERENCIA'}),409)]:
            with self.assertRaises(HTTPException) as failure:require_source_token(row,expected)
            self.assertEqual(failure.exception.status_code,code)

    def test_document_extraction_retains_supported_datetime(self):
        value=datetime(2026,10,2,12,tzinfo=timezone.utc)
        result=DocumentExtraction(document_type='receipt',datetime=value)
        self.assertEqual(result.datetime,value)
        self.assertEqual(DocumentExtraction.model_validate_json(result.model_dump_json()).datetime,value)

    def test_corrupt_workbook_is_an_input_failure(self):
        with self.assertRaisesRegex(ValueError,'XLSX'):ImportFinancialProvider().read_rows('corrupt.xlsx',b'not-zip')

    def test_refund_requires_positive_direction(self):
        with self.assertRaises(HTTPException) as failure:validate_signed_flow({'natureza':'ESTORNO','tipo':'estorno','valor':'-10','fluxo':'saida'})
        self.assertEqual(failure.exception.status_code,422)
        validate_signed_flow({'natureza':'ESTORNO','tipo':'estorno','valor':'10','fluxo':'entrada'})

    def test_authentication_probe_checks_remote_target_before_connecting(self):
        with patch('scripts.check_account_security.get_settings') as settings,patch('scripts.check_account_security.configured_mongodb_uri',return_value='mongodb+srv://synthetic.invalid/test'),patch('scripts.check_account_security.MongoClient') as connect,patch.dict('os.environ',{'FIN_ALLOW_REMOTE_TEST_DATABASE':''}):
            settings.return_value.mongodb_uri='mongodb+srv://synthetic.invalid/test'
            with self.assertRaises(RuntimeError):check_account_security.main()
            connect.assert_not_called()

    def test_remote_loopback_prefix_cannot_bypass_guard(self):
        with patch.dict('os.environ',{'FIN_ALLOW_REMOTE_TEST_DATABASE':''}):
            for uri in ['mongodb://127.0.0.1.attacker.test/test','mongodb://127.0.0.1:27017,remote.test:27017/test']:
                with self.assertRaises(RuntimeError):require_synthetic_target(uri)

if __name__=='__main__':unittest.main()
