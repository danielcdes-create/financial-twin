"""Every category selector and mutation uses the same persisted live catalogue."""
import copy
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi import HTTPException
from app.main import (CategoryCreate, _atomic_create_category, _atomic_delete_category,
                      build_classification_options, categories)
from app.subcategories import context
from test_subcategories import CatalogCollection, CatalogDB


class CountCollection(CatalogCollection):
    def aggregate(self, pipeline):
        rows = self.docs
        for stage in pipeline:
            if '$match' in stage:
                rows = [row for row in rows if self._matches(row, stage['$match'])]
            if '$group' in stage:
                field = stage['$group']['_id'][1:]
                return [{'_id': key, 'count': count} for key, count in Counter(row.get(field) for row in rows).items()]
        raise AssertionError('Unexpected catalogue aggregation')


class CountDB(CatalogDB):
    def __missing__(self, key):
        self[key] = CountCollection()
        return self[key]


class CategoryCatalogConsistencyTests(unittest.TestCase):
    def test_all_selectors_include_active_capture_only_categories_and_persisted_values(self):
        db = CountDB()
        db.transactions.insert_one({'categoria': 'Histórica'})
        db.categories.insert_one({'key': 'nova', 'name': 'Nova', 'active': True})
        for status in ('provisional', 'enriched', 'confirmed', 'cancelled', 'discarded'):
            db.provisional_transactions.insert_one({'status': status, 'categoria_sugerida': status,
                'metodo_pagamento': status, 'categoria_inferida': 'Inferida'})
        original = copy.deepcopy(db.provisional_transactions.docs)
        expected = {'Histórica', 'Nova', 'provisional', 'enriched', 'confirmed'}
        catalogue = categories(db)
        options = build_classification_options(db)
        self.assertEqual(set(catalogue['items']), expected)
        self.assertEqual(set(options['categoria']), expected)
        self.assertEqual(set(options['metodo_pagamento']), {'provisional', 'enriched', 'confirmed'})
        for status in ('provisional', 'enriched', 'confirmed'):
            self.assertEqual(context(db, status)[0], status)
            self.assertEqual(next(row for row in catalogue['details'] if row['name'] == status)['usage_count'], 1)
        for status in ('cancelled', 'discarded'):
            with self.assertRaises(HTTPException) as error:
                context(db, status)
            self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(db.provisional_transactions.docs, original)

    def test_deleted_category_does_not_reappear_from_cancelled_history(self):
        db = CountDB()
        for name in ('Antiga', 'Destino'):
            _atomic_create_category(CategoryCreate(name=name), db)
        db.provisional_transactions.insert_one({'status': 'confirmed', 'categoria_sugerida': 'Antiga'})
        db.provisional_transactions.insert_one({'status': 'cancelled', 'categoria_sugerida': 'Antiga', 'valor': '-17'})
        cancelled = copy.deepcopy(db.provisional_transactions.docs[-1])
        result = _atomic_delete_category('Antiga', 'Destino', db)
        self.assertEqual(result['affected_count'], 1)
        self.assertEqual(categories(db)['items'], ['Destino'])
        self.assertEqual(build_classification_options(db)['categoria'], ['Destino'])
        self.assertEqual(db.provisional_transactions.docs[-1], cancelled)
        with self.assertRaises(HTTPException) as error:
            context(db, 'Antiga')
        self.assertEqual(error.exception.status_code, 404)

    def test_explicit_recreation_is_allowed_without_changing_discarded_history(self):
        db = CountDB()
        db.categories.insert_one({'key': 'antiga', 'name': 'Antiga', 'active': False})
        db.provisional_transactions.insert_one({'status': 'discarded', 'categoria_sugerida': 'Antiga'})
        original = copy.deepcopy(db.provisional_transactions.docs)
        _atomic_create_category(CategoryCreate(name='Antiga'), db)
        self.assertEqual(categories(db)['items'], ['Antiga'])
        self.assertEqual(categories(db)['details'][0]['usage_count'], 0)
        self.assertEqual(db.provisional_transactions.docs, original)

    def test_discarded_only_category_cannot_be_a_replacement(self):
        db = CountDB()
        db.transactions.insert_one({'categoria': 'Origem'})
        db.provisional_transactions.insert_one({'status': 'discarded', 'categoria_sugerida': 'Descartada'})
        original = copy.deepcopy(db.transactions.docs)
        with self.assertRaises(HTTPException) as error:
            _atomic_delete_category('Origem', 'Descartada', db)
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(db.transactions.docs, original)
        self.assertEqual(db.audit_log.docs, [])

    def test_active_capture_category_prevents_duplicate_creation(self):
        for status in ('provisional', 'enriched', 'confirmed'):
            with self.subTest(status=status):
                db = CountDB()
                db.provisional_transactions.insert_one({'status': status, 'categoria_sugerida': 'Só captura'})
                with self.assertRaises(HTTPException) as error:
                    _atomic_create_category(CategoryCreate(name='so captura'), db)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(db.audit_log.docs, [])


if __name__ == '__main__':
    unittest.main()
