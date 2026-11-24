import copy
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from bson import ObjectId
from bson.decimal128 import Decimal128
from app.transaction_service import EventConflict, TransactionService


def _path_values(doc, parts):
    if not parts:
        return [doc], True
    head, tail = parts[0], parts[1:]
    if isinstance(doc, list):
        vals = []
        any_exists = False
        for item in doc:
            sub_vals, exists = _path_values(item, parts)
            if exists:
                any_exists = True
                vals.extend(sub_vals)
        return vals, any_exists
    if not isinstance(doc, dict) or head not in doc:
        return [], False
    return _path_values(doc[head], tail)


def _set_path(doc, key, value):
    if '.' not in key:
        doc[key] = value
        return
    parts = key.split('.')
    cur = doc
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _unset_path(doc, key):
    if '.' not in key:
        doc.pop(key, None)
        return
    parts = key.split('.')
    cur = doc
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            return
        cur = cur[part]
    cur.pop(parts[-1], None)


def _sort_token(val):
    if val is None:
        return (0, '')
    if isinstance(val, datetime):
        ts = val.timestamp() if val.tzinfo else val.replace(tzinfo=timezone.utc).timestamp()
        return (2, ts)
    if isinstance(val, ObjectId):
        return (3, str(val))
    if isinstance(val, Decimal128):
        return (1, val.to_decimal())
    if isinstance(val, (int, float)):
        return (1, val)
    return (4, str(val))


class Result:
    def __init__(self, inserted_id=None):
        self.inserted_id = inserted_id


class Cursor(list):
    def sort(self, key_or_list, direction=None):
        specs = [(key_or_list, direction or 1)] if isinstance(key_or_list, str) else list(key_or_list)
        items = list(self)
        for field, dir_val in reversed(specs):
            items.sort(key=lambda d: _sort_token(d.get(field)), reverse=(dir_val < 0))
        return Cursor(items)

    def skip(self, n):
        return Cursor(self[n:])

    def limit(self, n):
        return Cursor(self if not n else self[:n])

    def batch_size(self, _n):
        return self


class Collection:
    _SUPPORTED_QUERY_OPS = {
        '$eq', '$ne', '$in', '$nin', '$gt', '$gte', '$lt', '$lte', '$exists', '$regex', '$options'
    }
    _SUPPORTED_UPDATE_OPS = {'$set', '$unset', '$inc', '$setOnInsert', '$push', '$pull', '$addToSet'}

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def create_index(self, *a, **k):
        pass

    def _matches(self, doc, query):
        if not query:
            return True
        for key, want in query.items():
            if key == '$or':
                if not any(self._matches(doc, branch) for branch in want):
                    return False
                continue
            if key == '$and':
                if not all(self._matches(doc, branch) for branch in want):
                    return False
                continue
            if key == '$nor':
                if any(self._matches(doc, branch) for branch in want):
                    return False
                continue
            if key.startswith('$'):
                raise NotImplementedError(f'Unsupported top-level query operator: {key}')

            vals, exists = _path_values(doc, key.split('.'))
            effective_vals = vals if exists else [None]

            if isinstance(want, dict) and any(op.startswith('$') for op in want):
                unknown = set(want) - self._SUPPORTED_QUERY_OPS
                if unknown:
                    raise NotImplementedError(f'Unsupported query operator(s) {sorted(unknown)} on {key!r}')
                if '$exists' in want and exists != bool(want['$exists']):
                    return False
                if '$ne' in want:
                    target = want['$ne']
                    if exists and any(v == target or (isinstance(v, list) and target in v) for v in vals):
                        return False
                if '$nin' in want:
                    disallowed = want['$nin']
                    if any(
                        v in disallowed or (isinstance(v, list) and any(e in disallowed for e in v))
                        for v in effective_vals
                    ):
                        return False

                pos_ops = {
                    op: expected
                    for op, expected in want.items()
                    if op in {'$eq', '$in', '$gt', '$gte', '$lt', '$lte', '$regex'}
                }
                if pos_ops:
                    flags = re.IGNORECASE if 'i' in str(want.get('$options', '')) else 0

                    def _check_val(v):
                        for op, expected in pos_ops.items():
                            if op == '$eq':
                                if not (v == expected or (isinstance(v, list) and expected in v)):
                                    return False
                            elif op == '$in':
                                if not (v in expected or (isinstance(v, list) and any(e in expected for e in v))):
                                    return False
                            elif op == '$gt':
                                if v is None or not (v > expected):
                                    return False
                            elif op == '$gte':
                                if v is None or not (v >= expected):
                                    return False
                            elif op == '$lt':
                                if v is None or not (v < expected):
                                    return False
                            elif op == '$lte':
                                if v is None or not (v <= expected):
                                    return False
                            elif op == '$regex':
                                pat = expected.pattern if isinstance(expected, re.Pattern) else str(expected)
                                if not isinstance(v, str) or re.search(pat, v, flags) is None:
                                    return False
                        return True

                    if not any(_check_val(v) for v in effective_vals):
                        return False
            else:
                if want is None:
                    if exists and not any(v is None for v in vals):
                        return False
                else:
                    if not exists:
                        return False
                    if not any(v == want or (isinstance(v, list) and want in v) for v in vals):
                        return False
        return True

    def find_one(self, query=None, projection=None, **_kw):
        return next((d for d in self.docs if self._matches(d, query or {})), None)

    def find(self, query=None, projection=None, **_kw):
        return Cursor(d for d in self.docs if self._matches(d, query or {}))

    def insert_one(self, doc, **_kw):
        if doc.get('event_id') and self.find_one({'event_id': doc['event_id']}):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError('duplicate')
        item = dict(doc)
        item.setdefault('_id', f'id-{len(self.docs) + 1}')
        self.docs.append(item)
        return Result(item['_id'])

    def _apply_mutations(self, target, update, *, is_insert=False):
        unknown = set(update) - self._SUPPORTED_UPDATE_OPS
        if unknown:
            raise NotImplementedError(f'Unsupported update operator(s): {sorted(unknown)}')
        if is_insert and '$setOnInsert' in update:
            for k, v in update['$setOnInsert'].items():
                _set_path(target, k, copy.deepcopy(v))
        for k, v in update.get('$set', {}).items():
            _set_path(target, k, v)
        for k in update.get('$unset', {}):
            _unset_path(target, k)
        for k, v in update.get('$inc', {}).items():
            target[k] = target.get(k, 0) + v
        for k, v in update.get('$push', {}).items():
            arr = target.setdefault(k, [])
            if isinstance(v, dict) and '$each' in v:
                arr.extend(v['$each'])
            else:
                arr.append(v)
        for k, v in update.get('$addToSet', {}).items():
            arr = target.setdefault(k, [])
            elems = v['$each'] if isinstance(v, dict) and '$each' in v else [v]
            for elem in elems:
                if elem not in arr:
                    arr.append(elem)
        for k, cond in update.get('$pull', {}).items():
            arr = target.get(k)
            if isinstance(arr, list):
                if isinstance(cond, dict):
                    target[k] = [
                        e for e in arr if not (isinstance(e, dict) and all(e.get(ck) == cv for ck, cv in cond.items()))
                    ]
                else:
                    target[k] = [e for e in arr if e != cond]

    def update_one(self, query, update, upsert=False, **_kw):
        doc = next((d for d in self.docs if self._matches(d, query or {})), None)
        if doc is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
            seed = {}
            for k, v in (query or {}).items():
                if not k.startswith('$') and not (isinstance(v, dict) and any(op.startswith('$') for op in v)):
                    _set_path(seed, k, copy.deepcopy(v))
            self._apply_mutations(seed, update, is_insert=True)
            seed.setdefault('_id', f'id-{len(self.docs) + 1}')
            self.docs.append(seed)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=seed['_id'])
        before = copy.deepcopy(doc)
        self._apply_mutations(doc, update, is_insert=False)
        modified = 1 if doc != before else 0
        return SimpleNamespace(matched_count=1, modified_count=modified, upserted_id=None)

    def delete_one(self, query, **_kw):
        doc = next((d for d in self.docs if self._matches(d, query or {})), None)
        if doc is None:
            return SimpleNamespace(deleted_count=0)
        self.docs.remove(doc)
        return SimpleNamespace(deleted_count=1)

    def count_documents(self, q=None, **_kw):
        return len(self.find(q or {}))


class FakeDB(dict):
    def __missing__(self, k):
        self[k] = Collection()
        return self[k]

    def __getattr__(self, k):
        return self[k]


def event(event_id, kind, stamp, merchant='Padaria ABC'):
    return {
        'event_id': event_id,
        'source_type': kind,
        'source_provider': kind,
        'captured_at': stamp,
        'raw_payload': {'text': merchant},
        'parsed_payload': {
            'amount': 26,
            'currency': 'BRL',
            'merchant_raw': merchant,
            'event_timestamp': stamp,
            'event_type': 'card_purchase',
            'direction': 'expense',
        },
    }


class IngestionTests(unittest.TestCase):
    def test_collection_fake_evaluates_query_and_update_operators_strictly(self):
        col = Collection([
            {'_id': 1, 'status': 'active', 'amount': 10, 'tag': 'a'},
            {'_id': 2, 'status': 'cancelled', 'amount': 25},
            {'_id': 3, 'status': 'active', 'amount': 40, 'tag': 'b'},
        ])
        self.assertEqual([d['_id'] for d in col.find({'status': {'$ne': 'cancelled'}})], [1, 3])
        self.assertEqual([d['_id'] for d in col.find({'amount': {'$gt': 10, '$lt': 40}})], [2])
        self.assertEqual([d['_id'] for d in col.find({'tag': {'$nin': ['a']}})], [2, 3])
        self.assertEqual([d['_id'] for d in col.find({'tag': {'$exists': False}})], [2])
        self.assertEqual(
            [d['_id'] for d in col.find({'$and': [{'status': 'active'}, {'$or': [{'amount': 10}, {'amount': 40}]}]})],
            [1, 3],
        )
        with self.assertRaises(NotImplementedError):
            col.find({'amount': {'$mod': [2, 0]}})
        with self.assertRaises(NotImplementedError):
            col.find({'$where': 'true'})

        up1 = col.update_one({'_id': 99}, {'$setOnInsert': {'created': True}, '$set': {'status': 'draft'}, '$inc': {'rev': 1}}, upsert=True)
        self.assertEqual(up1.matched_count, 0)
        self.assertEqual(up1.upserted_id, 99)
        doc99 = col.find_one({'_id': 99})
        self.assertEqual(doc99, {'_id': 99, 'created': True, 'status': 'draft', 'rev': 1})

        up2 = col.update_one({'_id': 99}, {'$setOnInsert': {'created': False}, '$unset': {'created': ''}, '$inc': {'rev': 2}}, upsert=True)
        self.assertEqual(up2.matched_count, 1)
        self.assertEqual(up2.modified_count, 1)
        self.assertEqual(col.find_one({'_id': 99}), {'_id': 99, 'status': 'draft', 'rev': 3})

    def test_manual_purchase_preserves_payment_account_and_is_ready_for_review(self):
        from unittest.mock import patch
        db = FakeDB()
        stamp = datetime(2026, 10, 9, 14, 50, 45, tzinfo=timezone.utc)
        captured = event('manual-pix', 'manual', stamp)
        captured['parsed_payload'].update({
            'amount': '32.90',
            'event_type': 'manual_purchase',
            'payment_method': 'pix',
            'account_profile_id': 'santander',
        })
        with patch('app.transaction_service.validate_account_key') as validate:
            result = TransactionService(db).process(captured)
        self.assertEqual(result['status'], 'provisional')
        validate.assert_called_once_with('santander', db, selectable=True)
        item = db.provisional_transactions.docs[0]
        self.assertEqual(item['tipo'], 'compra')
        self.assertEqual(item['metodo_pagamento'], 'pix')
        self.assertEqual(item['account_profile_id'], 'santander')
        self.assertEqual(item['valor'].to_decimal(), __import__('decimal').Decimal('-32.90'))
        self.assertEqual(item['data_evento'], stamp)

    def test_manual_income_has_explicit_type_and_positive_amount(self):
        db = FakeDB()
        stamp = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        captured = event('income', 'manual', stamp)
        captured['parsed_payload'].update({'event_type': 'manual_income', 'direction': 'income'})
        TransactionService(db).process(captured)
        self.assertEqual(db.provisional_transactions.docs[0]['tipo'], 'receita')
        self.assertGreater(db.provisional_transactions.docs[0]['valor'].to_decimal(), 0)

    def test_four_evidences_link_to_one_legacy_transaction(self):
        stamp = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
        account_id = ObjectId()
        ledger = Collection([{
            '_id': 'canonical-1',
            'account_id': account_id,
            'valor': Decimal128('-26.00'),
            'data_evento': stamp,
            'estabelecimento_original': 'PADARIA ABC',
            'tipo': 'compra',
            'bank_transaction_ref': 'purchase-1',
            'account_identity': 'account-1',
            'source_provider': 'com.itau',
        }])
        db = FakeDB(
            transactions=ledger,
            accounts=Collection([{'_id': account_id, 'instituicao': 'Itaú', 'tipo': 'conta_corrente'}]),
        )
        service = TransactionService(db)
        for i, source in enumerate(('android_notification', 'receipt', 'financial_feed', 'statement')):
            evidence = event(f'e-{i}', source, stamp)
            evidence['source_provider'] = 'com.itau'
            evidence['parsed_payload'].update(bank_transaction_ref='purchase-1', account_identity='account-1')
            if source == 'android_notification':
                evidence['source_provider'] = 'com.itau'
                evidence['raw_payload'].update({'package': 'com.itau', 'origin_verification': 'exact_package_allowlist'})
            result = service.process(evidence)
            self.assertEqual(result['status'], 'matched')
        self.assertEqual(len(db['transactions'].docs), 1)
        self.assertEqual(len(db['transaction_evidence'].docs), 4)
        self.assertEqual(db['provisional_transactions'].docs, [])

    def test_two_capture_sources_with_same_scoped_reference_enrich_one_provisional(self):
        stamp = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
        db = FakeDB()
        service = TransactionService(db)
        first = event('notification', 'android_notification', stamp)
        second = event('receipt', 'receipt', stamp)
        for evidence in (first, second):
            evidence['source_provider'] = 'synthetic-bank'
            evidence['parsed_payload'].update(bank_transaction_ref='purchase-1', account_identity='account-1')
        self.assertEqual(service.process(first)['status'], 'provisional')
        self.assertEqual(service.process(second)['status'], 'matched')
        self.assertEqual(len(db['provisional_transactions'].docs), 1)
        self.assertEqual(db['provisional_transactions'].docs[0]['status'], 'enriched')
        self.assertEqual(len(db['transaction_evidence'].docs), 1)

    def test_retries_are_idempotent_and_payload_conflicts_rejected(self):
        stamp = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
        service = TransactionService(FakeDB())
        first = event('retry-id', 'manual', stamp)
        service.process(first)
        again = service.process(first)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['status'], 'provisional')
        with self.assertRaises(EventConflict):
            service.process({**first, 'raw_payload': {'text': 'changed'}})

    def test_manual_own_transfer_remains_one_reviewable_capture(self):
        stamp = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        db = FakeDB()
        captured = event('own-transfer', 'manual', stamp, 'Itaú → Santander')
        captured['parsed_payload'].update({
            'amount': 200,
            'event_type': 'own_transfer',
            'from_account_key': 'itau',
            'to_account_key': 'santander',
        })
        result = TransactionService(db).process(captured)
        self.assertEqual(result['status'], 'provisional')
        item = db.provisional_transactions.docs[0]
        self.assertEqual(item['natureza'], 'TRANSFERENCIA')
        self.assertEqual(item['transfer']['from_account_key'], 'itau')
        self.assertEqual(item['transfer']['to_account_key'], 'santander')
        self.assertNotIn('merchant_id', item)


if __name__ == '__main__':
    unittest.main()
