import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from pydantic import ValidationError
from app.schemas_document import DocumentExtraction
from app.document_validation import validate_document_extraction

class DocumentValidationTests(unittest.TestCase):
    def document(self,total=26,items=None):
        return DocumentExtraction(document_type='receipt',merchant='Padaria ABC',total=total,currency='BRL',items=items or [{'description':'Café','amount':6},{'description':'Pão','amount':20}])
    def test_item_total_within_two_cent_tolerance(self):
        result=validate_document_extraction(self.document(26.01))
        self.assertFalse(result['review_required'])
    def test_item_total_divergence_requires_review(self):
        result=validate_document_extraction(self.document(27))
        self.assertTrue(result['review_required'])
        self.assertEqual(result['difference'],-1)
    def test_rejects_unknown_fields_from_model(self):
        with self.assertRaises(ValidationError): DocumentExtraction.model_validate({'document_type':'receipt','total':10,'invented_field':True})
    def test_rejects_missing_total_and_non_brl(self):
        doc=DocumentExtraction(document_type='receipt',total=None)
        with self.assertRaises(ValueError): validate_document_extraction(doc)
        doc=DocumentExtraction(document_type='receipt',total=10,currency='USD')
        with self.assertRaises(ValueError): validate_document_extraction(doc)

if __name__=='__main__':unittest.main()
