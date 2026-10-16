"""Dashboard category drilldown across ledger, captures and page contracts."""
import copy
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi.testclient import TestClient
from app.analytics import calculate_financial_totals
from app.auth import require_session
from app.main import app, database
from test_record_review import RecordCollection, RecordCursor, RecordDB


class UnlimitedCursor(RecordCursor):
    def sort(self, order):
        return UnlimitedCursor(super().sort(order))

    def limit(self, count):
        return UnlimitedCursor(self if count == 0 else self[:count])

    def skip(self, count):
        return UnlimitedCursor(self[count:])


class CategoryCollection(RecordCollection):
    def find(self, query, projection=None):
        return UnlimitedCursor(copy.deepcopy(row) for row in self.docs if self._matches(row, query))


class UncategorizedMovementTests(unittest.TestCase):
    def setUp(self):
        self.db = RecordDB()
        self.db['transactions'] = CategoryCollection()
        self.db['provisional_transactions'] = CategoryCollection()
        self.account = ObjectId()
        self.other_account = ObjectId()
        self.db.accounts.docs.append({'_id': self.account, 'tipo': 'conta_corrente', 'instituicao': 'Itaú'})
        self.expected = set()
        for source, field in [('transactions', 'categoria'), ('provisional_transactions', 'categoria_sugerida')]:
            for index, category in enumerate([None, '', 'Sem categoria', 'Alimentação']):
                identity = ObjectId()
                row = {'_id': identity, 'data_evento': datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
                       'valor': Decimal128('-10'), 'fluxo': 'saida', 'tipo': 'compra',
                       'classification': {'migration_reason': 'despesa_real'}, 'status': 'confirmed',
                       'account_id': self.account, 'status_revisao': 'validado', field: category}
                if source == 'provisional_transactions': row['provisional_id'] = str(identity)
                self.db[source].docs.append(row)
                if index < 3: self.expected.add(str(identity))
            # Missing field must belong to the same bucket as null and empty.
            missing = copy.deepcopy(self.db[source].docs[0]); missing['_id'] = ObjectId(); missing.pop(field)
            if source == 'provisional_transactions': missing['provisional_id'] = str(missing['_id'])
            self.db[source].docs.append(missing); self.expected.add(str(missing['_id']))
        self.before = copy.deepcopy(dict(self.db))
        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[database] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear(); app.dependency_overrides.update(self.overrides)
        for collection in ('transactions', 'provisional_transactions'):
            self.assertEqual(self.before[collection].docs, self.db[collection].docs)

    def page(self, path='/v1/movements', **params):
        query = {'category': 'Sem categoria', 'date_from': '2026-10-01', 'date_to': '2026-10-31', **params}
        response = self.client.get(path, params=query)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def identities(page):
        return {item.get('provisional_id') or item['id'] for item in page['items']}

    def test_dashboard_bucket_opens_all_uncategorized_records(self):
        totals = calculate_financial_totals(self.db.transactions.docs, self.db.provisional_transactions.docs)
        self.assertEqual(totals['categories']['Sem categoria'], Decimal('80'))
        for mode in (True, False):
            with self.subTest(cursor_mode=mode):
                page = self.page(cursor_mode=mode)
                self.assertEqual(page['total'], 8)
                self.assertEqual(self.identities(page), self.expected)
                self.assertEqual(sum(Decimal(str(row['valor'])) for row in page['items']), Decimal('-80'))

    def test_cursor_and_offset_pages_return_each_record_once(self):
        identities = []
        cursor = None
        while True:
            page = self.page(cursor_mode=True, limit=3, **({'cursor': cursor} if cursor else {}))
            self.assertEqual(page['total'], 8)
            identities.extend(row.get('provisional_id') or row['id'] for row in page['items'])
            if not page['has_more']: break
            cursor = page['next_cursor']
        self.assertEqual(set(identities), self.expected)
        self.assertEqual(len(identities), 8)
        offset_ids = set()
        for offset in (0, 3, 6): offset_ids.update(self.identities(self.page(limit=3, offset=offset)))
        self.assertEqual(offset_ids, self.expected)

    def test_ledger_and_review_use_the_same_bucket(self):
        ledger = self.page('/v1/transactions')
        self.assertEqual(ledger['total'], 4)
        self.assertTrue(self.identities(ledger) < self.expected)
        review = self.page('/v1/review-records', review_status='validado')
        self.assertEqual(review['total'], 8)
        self.assertEqual(self.identities(review), self.expected)

    def test_normal_categories_and_absent_filters_keep_their_meaning(self):
        for mode in (True, False):
            with self.subTest(cursor_mode=mode):
                self.assertEqual(self.page(cursor_mode=mode, category='Alimentação')['total'], 2)
                self.assertEqual(self.page(cursor_mode=mode, category='')['total'], 10)
                self.assertEqual(self.page(cursor_mode=mode, category='   ')['total'], 10)
                self.assertEqual(self.page(cursor_mode=mode, category=' Sem categoria ')['total'], 8)

    def test_category_intersects_account_dates_and_review_status(self):
        for mode in (True, False):
            with self.subTest(cursor_mode=mode):
                self.assertEqual(self.page(cursor_mode=mode, account_id=str(self.account))['total'], 8)
                self.assertEqual(self.page(cursor_mode=mode, account_id=str(self.other_account))['total'], 0)
                self.assertEqual(self.page(cursor_mode=mode, date_from='2026-10-03')['total'], 0)
        self.assertEqual(self.page('/v1/review-records', review_status='pendente')['total'], 0)


if __name__ == '__main__':
    unittest.main()
