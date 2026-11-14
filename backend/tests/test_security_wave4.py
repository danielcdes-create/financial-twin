import hashlib
import io
import os
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import create_apk_download_token, require_session, verify_apk_download_token
from app.future_commitments import database as future_database
from app.main import app
from app.media_store import reconcile_media, store_media
from app.pdf_input import redact_pdf_pii
from app.providers.import_provider import ImportFinancialProvider
from test_capture_batches import BatchDB
from test_domain_ledger_fixes import DomainDB


class SecurityWave4Tests(unittest.TestCase):
    def test_ota_apk_routes_require_session_or_signed_token(self):
        client = TestClient(app)
        for path in ("/v1/app-update", "/v1/app-update/apk", "/api/v1/apk/info", "/apk", "/financial-twin.apk"):
            res = client.get(path)
            self.assertIn(res.status_code, (401, 503), f"{path} accepted unauthenticated request: {res.status_code}")

    def test_apk_download_token_binds_digest_version_and_expiry(self):
        secret = "s" * 40
        sha = "A" * 64
        with patch("app.auth.get_settings", return_value=SimpleNamespace(jwt_secret=secret)):
            now = int(time.time())
            tok = create_apk_download_token(sha, 131, now=now, ttl=60)
            self.assertTrue(verify_apk_download_token(tok, sha, 131))
            self.assertFalse(verify_apk_download_token(tok, "B" * 64, 131))
            self.assertFalse(verify_apk_download_token(tok, sha, 130))
            expired = create_apk_download_token(sha, 131, now=now - 120, ttl=60)
            self.assertFalse(verify_apk_download_token(expired, sha, 131))

    def test_non_auth_validation_error_never_echoes_raw_input(self):
        client = TestClient(app)
        secret_marker = "CPF-SECRET-12345678901"
        app.dependency_overrides[require_session] = lambda: {"session_id": "test", "account": {"_id": "owner"}}
        app.dependency_overrides[future_database] = lambda: BatchDB()
        try:
            res = client.get(f"/v1/commitment-projection?months={secret_marker}")
            self.assertEqual(res.status_code, 422)
            self.assertNotIn(secret_marker, res.text)
            self.assertNotIn('"input"', res.text)
        finally:
            app.dependency_overrides.pop(require_session, None)
            app.dependency_overrides.pop(future_database, None)

    def test_xlsx_rejects_xml_dtd_entities_and_excessive_entries(self):
        provider = ImportFinancialProvider()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe "secret">]><Types></Types>',
            )
        with self.assertRaises(ValueError) as ctx:
            provider.read_rows("evil.xlsx", buf.getvalue())
        self.assertIn("XLSX", str(ctx.exception))

    def test_redact_pdf_pii_masks_cpf_cnpj_and_account_headers(self):
        raw = "Cliente: CPF 123.456.789-01 | CNPJ: 12.345.678/0001-99 | Agência: 0001-9 Conta: 98765-4\nCOMPRA PIX R$ 45,00"
        cleaned = redact_pdf_pii(raw)
        self.assertNotIn("123.456.789-01", cleaned)
        self.assertNotIn("12.345.678/0001-99", cleaned)
        self.assertNotIn("98765-4", cleaned)
        self.assertIn("COMPRA PIX R$ 45,00", cleaned)

    def test_media_store_revives_purged_sha256_and_ignores_discarded_batches(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MEDIA_STORAGE_PATH": tmp}):
                db = DomainDB()
                payload = b"receipt-bytes-123"
                sha = hashlib.sha256(payload).hexdigest()
                old_time = datetime.now(timezone.utc) - timedelta(days=10)
                db.media_assets.docs.append({
                    "_id": "asset-1",
                    "sha256": sha,
                    "path": str(Path(tmp) / sha),
                    "status": "purged",
                    "created_at": old_time,
                    "updated_at": old_time,
                })
                db.capture_batches.docs.append({
                    "_id": "batch-discarded",
                    "sha256": sha,
                    "status": "discarded",
                })
                stored_path = store_media(db, payload, sha, ".pdf")
                asset = db.media_assets.find_one({"sha256": sha})
                self.assertEqual(asset["status"], "media_ready")
                self.assertTrue(stored_path.exists())
                # Even if aged > 2 hours, a discarded batch does not count as active batch reference (marks orphan),
                # while a freshly re-uploaded asset (< 1h updated_at) stays media_ready!
                reconcile_media(db)
                asset_after = db.media_assets.find_one({"sha256": sha})
                self.assertEqual(asset_after["status"], "media_ready")
                self.assertTrue(stored_path.exists())
                # Now age updated_at > 2 hours: because batch is 'discarded', reconcile_media must see 0 active refs and mark orphaned
                db.media_assets.docs[0]["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=3)
                reconcile_media(db)
                self.assertEqual(db.media_assets.docs[0]["status"], "orphaned")


if __name__ == "__main__":
    unittest.main()
