import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.main import dashboard, dashboard_overview, movements


def dashboard_db(transactions, captures=()):
    db = MagicMock()
    db.transactions.find.return_value.limit.return_value = list(transactions)
    db.provisional_transactions.find.return_value = list(captures)
    db.review_queue.count_documents.return_value = 0
    db.provisional_transactions.count_documents.return_value = 0
    db.obligations.find.return_value = []
    db.installment_plans.find.return_value = []
    db.commitments.find.return_value = []
    db.investments.count_documents.return_value = 0
    db.source_records.find_one.return_value = None
    return db


class PerformancePlanTests(unittest.TestCase):
    def test_dashboard_has_no_ten_thousand_record_cutoff(self):
        stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
        rows = [{"_id": index, "data_evento": stamp, "valor": Decimal128("-1.00"),
                 "fluxo": "saida", "categoria": "Teste",
                 "classification": {"migration_reason": "despesa_real"}}
                for index in range(10_001)]
        result = dashboard(period="all", date_from=None, date_to=None, db=dashboard_db(rows))
        self.assertEqual(result["legacy_transaction_count"], 10_001)
        self.assertEqual(result["expenses"], 10_001.0)

    def test_dashboard_overview_reads_one_transaction_snapshot(self):
        stamp = datetime(2026, 9, 10, tzinfo=timezone.utc)
        db = dashboard_db([{"_id": 1, "data_evento": stamp, "valor": Decimal128("-20.00"),
                            "fluxo": "saida", "categoria": "Mercado",
                            "classification": {"migration_reason": "despesa_real"}}])
        result = dashboard_overview(period="month", year_start=stamp.date().replace(month=1, day=1),
                                    month_start=stamp.date().replace(day=1), month_end=stamp.date(), db=db)
        self.assertEqual(db.transactions.find.call_count, 1)
        self.assertEqual(result["selected"]["expenses"], 20.0)
        self.assertEqual(result["month"]["expenses"], 20.0)
        self.assertEqual(result["year"]["expenses"], 20.0)
        self.assertEqual(db.commitments.find.call_count, 1)
        self.assertEqual(db.installment_plans.find.call_count, 1)
        self.assertEqual(db.review_queue.count_documents.call_count, 1)
        self.assertEqual(db.investments.count_documents.call_count, 1)
        self.assertEqual(db.obligations.find.call_count, 2)

    def test_movement_pages_cover_both_sources_without_duplicates(self):
        from bson import ObjectId
        from test_uncategorized_movements import CategoryCollection
        from test_record_review import RecordDB
        db=RecordDB();db['transactions']=CategoryCollection();db['provisional_transactions']=CategoryCollection()
        identities={}
        for label,day,captured in [('l3',30,False),('l2',28,False),('l1',26,False),('p2',29,True),('p1',27,True)]:
            identity=ObjectId();identities[str(identity)]=label
            row={'_id':identity,'data_evento':datetime(2026,9,day,10,tzinfo=timezone.utc),'status':'confirmed'}
            if captured:row['provisional_id']=str(identity)
            db['provisional_transactions' if captured else 'transactions'].docs.append(row)
        args={'limit':3,'search':None,'category':None,'account_id':None,'date_from':None,'date_to':None,'db':db}
        first = movements(offset=0,**args)
        second = movements(offset=3,**args)
        ids = [identities[item.get("id") or item.get("provisional_id")] for item in first["items"] + second["items"]]
        self.assertEqual(ids, ["l3", "p2", "l2", "p1", "l1"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(second["has_more"])


if __name__ == "__main__":
    unittest.main()
