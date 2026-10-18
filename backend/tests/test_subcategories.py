import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bson.decimal128 import Decimal128
from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.subcategories import (SubcategoryCreate, SubcategoryRename, create_subcategory,
                               rename_subcategory, delete_subcategory, list_subcategories)
from app.taxonomy import category_subcategories, infer_classification, taxonomy_overrides
from app.main import TransactionClassificationUpdate, update_transaction_classification
from test_ingestion import Collection, FakeDB, Cursor
from test_architecture_failures import rollback_fake, FailsOnce


from observed_contract import observed_update

class CatalogCollection(Collection):
    def _matches(self, doc, query):
        for field, expected in query.items():
            value = doc.get(field)
            if isinstance(expected, dict):
                if '$in' in expected and value not in expected['$in']: return False
                if '$nin' in expected and value in expected['$nin']: return False
                if '$ne' in expected and value == expected['$ne']: return False
                if '$exists' in expected and (field in doc) != expected['$exists']: return False
            elif value != expected: return False
        return True

    def find(self, query, projection=None):
        return Cursor(doc for doc in self.docs if self._matches(doc, query))

    def distinct(self, field, query=None):
        return list({doc.get(field) for doc in self.find(query or {})})

    def update_one(self, query, update, upsert=False):
        if not self.find_one(query) and upsert:
            self.insert_one({**query, **update.get('$setOnInsert', {})})
        return super().update_one(query, update)

    def update_many(self, query, update):
        documents = list(self.find(query))
        for document in documents:
            self.update_one({'_id': document['_id']}, update)
        return SimpleNamespace(modified_count=len(documents))


class CatalogDB(FakeDB):
    def __missing__(self, key):
        self[key] = CatalogCollection()
        return self[key]


class SubcategoryTests(unittest.TestCase):
    def setUp(self):
        self.db = CatalogDB()
        for name in ('transactions', 'provisional_transactions', 'categories', 'audit_log'):
            self.db[name]
        self.db.categories.insert_one({'key': 'habitacao', 'name': 'Habitação', 'active': True})
        self.db.categories.insert_one({'key': 'transporte', 'name': 'Transporte', 'active': True})

    def create(self, name, category='Habitação'):
        return create_subcategory(SubcategoryCreate(category=category, name=name), self.db)

    def rename(self, name, new_name, category='Habitação'):
        token = next(item['token'] for item in list_subcategories(category, self.db)['details'] if item['name'].casefold() == name.casefold())
        return rename_subcategory(SubcategoryRename(category=category, name=name, new_name=new_name, expected_token=token), self.db)

    def delete(self, name, replacement=None, category='Habitação'):
        token = next(item['token'] for item in list_subcategories(category, self.db)['details'] if item['name'].casefold() == name.casefold())
        return delete_subcategory(category=category, name=name, replacement=replacement, expected_token=token, db=self.db)

    def seed(self):
        for identity, category, label in (('home', 'Habitação', 'Manutenção'), ('alias', 'habitacao', 'manutencao'), ('car', 'Transporte', 'Manutenção')):
            self.db.transactions.insert_one({'_id': identity, 'categoria': category, 'subcategoria': label,
                'valor': Decimal128('-12.59'), 'descricao_original': 'ORIGINAL', 'account_id': 'account'})
        for identity, status, revision in (('new', 'provisional', None), ('confirmed', 'confirmed', 5), ('cancelled', 'cancelled', 3)):
            self.db.provisional_transactions.insert_one({'_id': identity, 'categoria_sugerida': 'Habitação', 'subcategoria': 'Manutenção',
                'status': status, **({'revision': revision} if revision else {})})

    def test_creation_without_usage_survives_reload_and_is_available_in_editor(self):
        self.create('  Energia   Solar  ')
        self.assertIn('Energia Solar', category_subcategories(self.db)['Habitação'])
        item = next(item for item in list_subcategories('habitacao', self.db)['details'] if item['name'] == 'Energia Solar')
        self.assertEqual(item['usage_count'], 0)
        identity = ObjectId()
        self.db.transactions.insert_one({'_id': identity, 'categoria': 'Habitação'})
        update_transaction_classification(str(identity), observed_update(TransactionClassificationUpdate(subcategoria='Energia Solar'), self.db, str(identity)), self.db)
        self.assertEqual(self.db.transactions.find_one({'_id': identity})['subcategoria'], 'Energia Solar')

    def test_duplicate_names_are_canonical_and_scoped_to_parent(self):
        self.create('Energia Solar')
        with self.assertRaises(HTTPException) as error: self.create('energia solar')
        self.assertEqual(error.exception.status_code, 409)
        self.create('energia solar', 'Transporte')
        with self.assertRaises(HTTPException): self.create('Energia')

    def test_rename_updates_aliases_and_live_captures_only_preserving_money_and_sources(self):
        self.seed()
        before = copy.deepcopy(self.db.transactions.docs)
        result = self.rename('Manutenção', 'Reparos')
        self.assertEqual(result['affected_count'], 4)
        for row in self.db.transactions.docs:
            original = next(item for item in before if item['_id'] == row['_id'])
            self.assertEqual({key: row[key] for key in original if key != 'subcategoria'}, {key: value for key, value in original.items() if key != 'subcategoria'})
            self.assertEqual(row['subcategoria'], 'Manutenção' if row['_id'] == 'car' else 'Reparos')
        self.assertEqual(self.db.provisional_transactions.find_one({'_id': 'new'})['revision'], 2)
        self.assertEqual(self.db.provisional_transactions.find_one({'_id': 'confirmed'})['revision'], 6)
        cancelled = self.db.provisional_transactions.find_one({'_id': 'cancelled'})
        self.assertEqual((cancelled['subcategoria'], cancelled['revision']), ('Manutenção', 3))
        self.assertNotIn('Manutenção', category_subcategories(self.db)['Habitação'])
        self.assertIn('Reparos', category_subcategories(self.db)['Habitação'])

    def test_case_only_rename_changes_display_without_duplicate_or_tombstone(self):
        self.rename('Energia', 'energia')
        options = category_subcategories(self.db)['Habitação']
        self.assertIn('energia', options)
        self.assertNotIn('Energia', options)
        self.assertEqual(len([name for name in options if name.lower() == 'energia']), 1)

    def test_delete_default_is_durable_and_explicit_recreation_restores_it(self):
        self.delete('Energia')
        self.assertNotIn('Energia', category_subcategories(self.db)['Habitação'])
        self.create('Energia')
        self.assertIn('Energia', category_subcategories(self.db)['Habitação'])

    def test_future_suggestions_follow_renames_and_do_not_recreate_deleted_defaults(self):
        self.rename('IPTU/Seguro', 'Imposto da casa')
        self.rename('Imposto da casa', 'Tributo municipal')
        suggestion = infer_classification({'descricao_original': 'PARCELA IPTU'}, taxonomy_overrides(self.db))
        self.assertEqual(suggestion['subcategoria'], 'Tributo municipal')
        self.assertEqual(suggestion['natureza'], 'IMPOSTO_TRIBUTO')
        self.delete('Tributo municipal')
        suggestion = infer_classification({'descricao_original': 'PARCELA IPTU'}, taxonomy_overrides(self.db))
        self.assertNotIn('subcategoria', suggestion)
        self.assertEqual(suggestion['natureza'], 'IMPOSTO_TRIBUTO')

    def test_case_only_rename_updates_each_capture_once(self):
        for identity, label in (('upper', 'IPTU'), ('lower', 'Iptu')):
            self.db.provisional_transactions.insert_one({'_id': identity, 'categoria_sugerida': 'Habitação',
                                                        'subcategoria': label, 'status': 'confirmed', 'revision': 3})
        self.assertEqual(self.rename('IPTU', 'Iptu')['affected_count'], 2)
        self.assertTrue(all(row['revision'] == 4 for row in self.db.provisional_transactions.docs))

    def test_stale_edit_cannot_reintroduce_a_deleted_custom_subcategory(self):
        self.db.categories.insert_one({'key': 'alimentacao', 'name': 'Alimentação', 'active': True})
        self.create('Lanches', 'Alimentação')
        self.delete('Lanches', category='Alimentação')
        identity = ObjectId()
        self.db.transactions.insert_one({'_id': identity, 'categoria': 'Alimentação'})
        with self.assertRaises(HTTPException) as error:
            update_transaction_classification(str(identity), observed_update(TransactionClassificationUpdate(subcategoria='Lanches'), self.db, str(identity)), self.db)
        self.assertEqual(error.exception.status_code, 422)
        self.assertNotIn('subcategoria', self.db.transactions.find_one({'_id': identity}))

    def test_in_use_delete_requires_valid_same_parent_replacement(self):
        self.seed()
        before = copy.deepcopy(self.db.transactions.docs)
        for replacement, status in ((None, 409), ('Manutenção', 422), ('Seguro', 422), ('Inexistente', 422)):
            with self.subTest(replacement=replacement), self.assertRaises(HTTPException) as error:
                self.delete('Manutenção', replacement)
            self.assertEqual(error.exception.status_code, status)
            self.assertEqual(self.db.transactions.docs, before)
        self.create('Reparos')
        self.assertEqual(self.delete('Manutenção', 'Reparos')['affected_count'], 4)
        self.assertEqual(self.db.transactions.find_one({'_id': 'car'})['subcategoria'], 'Manutenção')
        self.assertEqual(self.db.audit_log.docs[-1]['event'], 'subcategory_deleted')

    def test_invalid_names_and_unknown_parents_do_not_write(self):
        for name in ('   ', '!!!'):
            with self.assertRaises(HTTPException): self.create(name)
        with self.assertRaises(HTTPException): self.create('Nova', 'Não existe')
        self.assertEqual(self.db.audit_log.docs, [])

    def test_rename_to_existing_does_not_silently_merge(self):
        self.seed()
        before = copy.deepcopy(self.db.transactions.docs)
        with self.assertRaises(HTTPException): self.rename('Manutenção', 'Condomínio')
        self.assertEqual(self.db.transactions.docs, before)

    def test_audit_failure_rolls_back_labels_and_catalog(self):
        self.seed()
        self.db.audit_log = self.db['audit_log'] = FailsOnce()
        before = {key: copy.deepcopy(value.docs) for key, value in self.db.items()}
        with patch('app.subcategories.mongo_transaction', lambda db: rollback_fake(db)):
            with self.assertRaises(RuntimeError): self.rename('Manutenção', 'Reparos')
        self.assertEqual({key: value.docs for key, value in self.db.items()}, before)



    def test_commitments_and_future_overrides_participate_in_subcategory_usage_and_relabel(self):
        self.seed()
        self.db.commitments = self.db["commitments"] = copy.deepcopy(self.db.transactions)
        self.db.commitments.docs = [{"_id": "c1", "category": "Habitação", "subcategoria": "Manutenção"}]
        self.db.future_schedule_overrides = self.db["future_schedule_overrides"] = copy.deepcopy(self.db.transactions)
        self.db.future_schedule_overrides.docs = [{"_id": "o1", "profile": {"categoria": "Habitação", "subcategoria": "Manutenção"}}]

        listed = list_subcategories("Habitação", self.db)
        manut = next(item for item in listed["details"] if item["name"] == "Manutenção")
        self.assertEqual(manut["usage_count"], 6)

        self.rename("Manutenção", "Conservação")
        self.assertEqual(self.db.commitments.find_one({"_id": "c1"})["subcategoria"], "Conservação")
        self.assertEqual(self.db.future_schedule_overrides.find_one({"_id": "o1"})["profile"]["subcategoria"], "Conservação")


if __name__ == "__main__":
    unittest.main()
