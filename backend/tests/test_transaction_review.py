import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.main import TransactionClassificationUpdate, TransactionReviewUpdate, transactions, update_transaction_classification, update_transaction_review
from app.main import classification_match_preview, supplier_match_preview
from test_ingestion import Collection, FakeDB


from observed_contract import observed_update

class ReviewCursor(list):
    def sort(self, *_): return self
    def skip(self, count): return ReviewCursor(self[count:])
    def limit(self, count): return ReviewCursor(self[:count])


class ReviewCollection(Collection):
    def _matches(self, doc, query):
        for key, wanted in query.items():
            if key == "$or":
                if not any(self._matches(doc, condition) for condition in wanted): return False
                continue
            value = doc.get(key)
            if isinstance(wanted, dict):
                if "$ne" in wanted and value == wanted["$ne"]: return False
                if "$exists" in wanted and (key in doc) != wanted["$exists"]: return False
                if "$in" in wanted and value not in wanted["$in"]: return False
            elif value != wanted: return False
        return True

    def find(self, query, projection=None):
        return ReviewCursor(doc for doc in self.docs if self._matches(doc, query))

    def update_many(self, query, update):
        matched = list(self.find(query))
        for doc in matched:
            doc.update(update.get("$set", {}))
            for key in update.get("$unset", {}): doc.pop(key, None)
        return type("UpdateResult", (), {"modified_count": len(matched)})()


class TransactionReviewTests(unittest.TestCase):
    def setUp(self):
        self.first, self.second = ObjectId(), ObjectId()
        self.db = FakeDB(transactions=ReviewCollection([
            {"_id": self.first, "data_evento": datetime(2026, 9, 25, tzinfo=timezone.utc), "descricao_original": "IFD*GVG ALIM", "categoria": "Alimentação"},
            {"_id": self.second, "data_evento": datetime(2026, 9, 24, tzinfo=timezone.utc), "descricao_original": "Padaria"},
        ]))

    def listing(self, status):
        return transactions(limit=50, offset=0, search=None, category=None, review_status=status,
                            date_from=None, date_to=None, db=self.db)

    def test_validation_is_explicit_and_filter_treats_missing_as_pending(self):
        pending = self.listing("pendente")
        self.assertEqual(pending["total"], 2)
        self.assertEqual(pending["items"][0]["status_revisao"], "pendente")
        result = update_transaction_review(str(self.first), observed_update(TransactionReviewUpdate(status_revisao="validado"), self.db, str(self.first)), self.db)
        self.assertTrue(result["changed"])
        self.assertEqual(self.listing("pendente")["total"], 1)
        self.assertEqual(self.listing("validado")["total"], 1)
        self.assertEqual(self.db.transactions.docs[0]["descricao_original"], "IFD*GVG ALIM")
        self.assertIn("revisado_em", self.db.transactions.docs[0])
        self.assertEqual(self.db.audit_log.docs[-1]["event"], "transaction_review_status_updated")

    def test_validation_resolves_pending_possible_match_without_creating_evidence(self):
        review = {"_id": ObjectId(), "review_id": "match-1", "status": "pending",
                  "candidate_type": "transaction", "candidate_transaction_id": self.first}
        self.db.review_queue = ReviewCollection([review])
        result = update_transaction_review(
            str(self.first), observed_update(TransactionReviewUpdate(status_revisao="validado"), self.db, str(self.first)), self.db
        )
        self.assertEqual(result["resolved_review_count"], 1)
        self.assertEqual(review["status"], "resolved")
        self.assertEqual(review["decision"], "candidate_validated")
        self.assertEqual(self.db.transaction_evidence.docs, [])

    def test_transaction_listing_filters_by_account(self):
        account_id = ObjectId()
        self.db.transactions.docs[0]["account_id"] = account_id
        self.db.accounts = ReviewCollection([{"_id": account_id, "instituicao": "Itaú", "tipo": "conta_corrente"}])
        result = transactions(limit=50, offset=0, search=None, category=None,
                              account_id=str(account_id), review_status="todos",
                              date_from=None, date_to=None, db=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], str(self.first))

    def test_edit_reopens_validated_record_without_touching_original_text(self):
        update_transaction_review(str(self.first), observed_update(TransactionReviewUpdate(status_revisao="validado"), self.db, str(self.first)), self.db)
        update_transaction_classification(str(self.first), observed_update(TransactionClassificationUpdate(categoria="Restaurantes"), self.db, str(self.first)), self.db)
        edited = self.db.transactions.docs[0]
        self.assertEqual(edited["status_revisao"], "pendente")
        self.assertNotIn("revisado_em", edited)
        self.assertIn("editado_em", edited)
        self.assertEqual(edited["descricao_original"], "IFD*GVG ALIM")
        self.assertEqual(self.listing("pendente")["total"], 2)

    def test_validating_selected_classification_batch_validates_exact_descriptor_peers(self):
        self.db.transactions.docs[1]["descricao_original"] = "IFD*GVG ALIM"
        unrelated = {"_id": ObjectId(), "descricao_original": "Outra loja", "categoria": "Serviços"}
        self.db.transactions.docs.append(unrelated)
        result = update_transaction_classification(
            str(self.first),
            observed_update(TransactionClassificationUpdate(categoria="Restaurantes", apply_similar_classification=True, validate_review=True, classification_preview_token=classification_match_preview(str(self.first),self.db)['preview_token']), self.db, str(self.first)),
            self.db,
        )
        self.assertEqual(result["batch_validated_count"], 1)
        self.assertEqual(result["validated_count"], 2)
        self.assertEqual(self.db.transactions.docs[0]["status_revisao"], "validado")
        self.assertEqual(self.db.transactions.docs[1]["status_revisao"], "validado")
        self.assertEqual(self.db.transactions.docs[1]["categoria"], "Restaurantes")
        self.assertIn("revisado_em", self.db.transactions.docs[1])
        self.assertNotIn("status_revisao", unrelated)
        self.assertEqual(self.db.audit_log.docs[-1]["validated_peer_count"], 1)

    def test_validation_without_batch_leaves_exact_descriptor_peer_pending(self):
        self.db.transactions.docs[1]["descricao_original"] = "IFD*GVG ALIM"
        result = update_transaction_classification(
            str(self.first),
            observed_update(TransactionClassificationUpdate(categoria="Restaurantes", validate_review=True), self.db, str(self.first)),
            self.db,
        )
        self.assertEqual(result["batch_validated_count"], 0)
        self.assertEqual(self.db.transactions.docs[0]["status_revisao"], "validado")
        self.assertNotIn("status_revisao", self.db.transactions.docs[1])

    def test_validating_unchanged_source_with_batch_validates_exact_descriptor_peers(self):
        self.db.transactions.docs[1]["descricao_original"] = "IFD*GVG ALIM"
        result = update_transaction_classification(
            str(self.first),
            observed_update(TransactionClassificationUpdate(apply_similar_classification=True, validate_review=True, classification_preview_token=classification_match_preview(str(self.first),self.db)['preview_token']), self.db, str(self.first)),
            self.db,
        )
        self.assertEqual(result["batch_validated_count"], 1)
        self.assertEqual(self.db.transactions.docs[1]["status_revisao"], "validado")
        self.assertEqual(self.db.transactions.docs[1]["descricao_original"], "IFD*GVG ALIM")

    def test_supplier_batch_validates_transactions_but_not_unconfirmed_captures(self):
        self.db.transactions.docs[1]["descricao_original"] = "IFD*GVG ALIM"
        self.db.merchants = ReviewCollection([])
        self.db.provisional_transactions = ReviewCollection([
            {"_id": ObjectId(), "provisional_id": "capture-1", "status": "provisional", "merchant_raw": "IFD*GVG ALIM"}
        ])
        result = update_transaction_classification(
            str(self.first),
            observed_update(TransactionClassificationUpdate(vendedor="GVG Restaurante", apply_similar_supplier=True, validate_review=True, supplier_preview_token=supplier_match_preview(str(self.first),'GVG Restaurante',self.db)['preview_token']), self.db, str(self.first)),
            self.db,
        )
        self.assertEqual(result["batch_validated_count"], 1)
        self.assertEqual(result["propagated_provisionals"], 1)
        self.assertEqual(self.db.transactions.docs[1]["status_revisao"], "validado")
        self.assertNotIn("status_revisao", self.db.provisional_transactions.docs[0])


if __name__ == "__main__": unittest.main()
