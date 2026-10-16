import io
import sys
import unittest
from pathlib import Path
from openpyxl import Workbook
sys.path.insert(0,str(Path(__file__).parents[1]))
from app.providers.import_provider import ImportFinancialProvider

class ImportProviderTests(unittest.TestCase):
    def test_semicolon_csv_and_utf8_bom(self):
        data='data;valor;descricao\n23/09/2026;-12,50;PADARIA ABC\n'.encode('utf-8-sig')
        rows=ImportFinancialProvider().read_rows('feed.csv',data)
        self.assertEqual(rows[0]['valor'],'-12,50')
        self.assertEqual(rows[0]['descricao'],'PADARIA ABC')
    def test_xlsx_rows(self):
        workbook=Workbook();sheet=workbook.active;sheet.append(['data','valor']);sheet.append(['2026-09-23',15.2])
        stream=io.BytesIO();workbook.save(stream)
        rows=ImportFinancialProvider().read_rows('feed.xlsx',stream.getvalue())
        self.assertEqual(rows[0]['valor'],15.2)
    def test_rejects_unsupported_format(self):
        with self.assertRaises(ValueError): ImportFinancialProvider().read_rows('feed.pdf',b'pdf')

if __name__=='__main__':unittest.main()
