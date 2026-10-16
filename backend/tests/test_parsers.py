import sys
import unittest
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from app.parsers import parse_brl, parse_datetime

class ImportParserTests(unittest.TestCase):
    def test_brazilian_currency(self):
        self.assertEqual(parse_brl('R$ 1.234,56'),Decimal('1234.56'))
        self.assertEqual(parse_brl('-27,99'),Decimal('-27.99'))

    def test_iso_and_brazilian_dates(self):
        self.assertEqual(parse_datetime('23/09/2026 17:32').day,23)
        self.assertEqual(parse_datetime('23/09/2026 17:32').utcoffset().total_seconds(),-10800)
        self.assertEqual(parse_datetime('2026-09-23T17:32:00Z').tzinfo.utcoffset(None).total_seconds(),0)

    def test_rejects_unknown_currency_and_date(self):
        with self.assertRaises(ValueError): parse_brl('abc')
        with self.assertRaises(ValueError): parse_datetime('yesterday')

if __name__=='__main__': unittest.main()
