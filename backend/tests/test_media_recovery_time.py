"""A BSON naive UTC timestamp must not stop periodic media reconciliation."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from app.media_store import reconcile_media
from test_domain_ledger_fixes import DomainDB


class MediaRecoveryTimeTests(unittest.TestCase):
    def test_old_unreferenced_media_handles_bson_and_aware_utc_without_deleting_bytes(self):
        for naive in (True,False):
            with self.subTest(naive=naive),TemporaryDirectory() as folder:
                db=DomainDB();data=b'Synthetic retained media'
                path=Path(folder)/'original';path.write_bytes(data)
                created=datetime.now(timezone.utc)-timedelta(hours=2)
                if naive: created=created.replace(tzinfo=None)
                db.media_assets.docs.append({'_id':'old','sha256':sha256(data).hexdigest(),
                    'path':str(path),'created_at':created,'status':'media_ready'})
                result=reconcile_media(db)
                self.assertEqual(result,{'checked':1,'missing':0,'orphaned':1,'limited':False})
                self.assertEqual(path.read_bytes(),data)
                self.assertEqual(db.media_assets.docs[0]['status'],'orphaned')

    def test_recent_bson_media_is_retained_without_marking_orphan(self):
        with TemporaryDirectory() as folder:
            db=DomainDB();data=b'Recent retained bytes';path=Path(folder)/'original';path.write_bytes(data)
            db.media_assets.docs.append({'_id':'recent','sha256':sha256(data).hexdigest(),
                'path':str(path),'created_at':datetime.now(timezone.utc).replace(tzinfo=None),'status':'media_ready'})
            self.assertEqual(reconcile_media(db)['orphaned'],0)
            self.assertEqual(db.media_assets.docs[0]['status'],'media_ready')
            self.assertEqual(path.read_bytes(),data)


if __name__=='__main__': unittest.main()
