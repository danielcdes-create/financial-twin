import copy
import sys
import unittest
from pathlib import Path

from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.installment_cards import attach_cards, bind_reviewed_card
from app.cards import token
from test_cards import CardDB


class InstallmentCardTests(unittest.TestCase):
    def setUp(self):
        self.db = CardDB()
        self.card, self.plan, self.other = ObjectId(), ObjectId(), ObjectId()
        self.db.accounts.insert_one({'_id': self.card, 'tipo': 'cartao_credito',
                                    'instituicao': 'Itaú', 'cartao_final': '4242'})
        self.db.installment_plans.insert_one({'_id': self.plan, 'descricao': 'AMAZON',
            'status': 'ativo', 'installments': [{'numero': 1, 'status': 'paga'}, {'numero': 2, 'status': 'projetada'}]})
        self.db.installment_plans.insert_one({'_id': self.other, 'descricao': 'OTHER'})

    def bind(self):
        plan = self.db.installment_plans.find_one({'_id': self.plan})
        previous = self.db.installment_card_links.find_one({'_id': str(self.plan)})
        return bind_reviewed_card(self.db, str(self.plan), str(self.card), token(plan, previous), 'Reviewed statement')

    def test_link_preserves_history_is_idempotent_and_uses_current_card_name(self):
        before = copy.deepcopy(self.db.installment_plans.docs)
        self.bind(); self.bind()
        self.assertEqual(len(self.db.installment_card_links.docs), 1)
        self.assertEqual(len(self.db.audit_log.docs), 1)
        self.assertEqual(self.db.installment_plans.docs, before)
        self.db.account_profiles.insert_one({'_id': f'card-profile:{self.card}',
            'values': {'apelido': 'Cartão exemplo 4242'}, 'deleted': True})
        items = attach_cards(self.db, self.db.installment_plans.docs)
        self.assertEqual(items[0]['card_id'], str(self.card))
        self.assertEqual(items[0]['card_label'], 'Cartão exemplo 4242')
        self.assertTrue(items[0]['card_deleted'])
        self.assertIsNone(items[1]['card_id'])
        self.assertIsNone(items[1]['card_label'])
        self.assertEqual(self.db.installment_plans.docs, before)

    def test_stale_review_missing_card_and_deleted_card_are_rejected(self):
        plan = self.db.installment_plans.docs[0]
        stale = token(plan, None)
        plan['descricao'] = 'Changed'
        with self.assertRaises(HTTPException) as failure:
            bind_reviewed_card(self.db, str(self.plan), str(self.card), stale, 'Old review')
        self.assertEqual(failure.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            bind_reviewed_card(self.db, str(self.plan), str(ObjectId()), token(plan, None), 'Missing card')
        self.db.account_profiles.insert_one({'_id': f'card-profile:{self.card}', 'deleted': True})
        with self.assertRaises(HTTPException): self.bind()
        self.assertEqual(self.db.installment_card_links.docs, [])

    def test_missing_card_is_reviewable_without_silently_reassigning(self):
        self.bind(); self.db.accounts.docs.clear()
        item = attach_cards(self.db, self.db.installment_plans.docs)[0]
        self.assertEqual(item['card_id'], str(self.card))
        self.assertIsNone(item['card_label'])
        self.assertTrue(item['card_link_requires_review'])


if __name__ == '__main__': unittest.main()
