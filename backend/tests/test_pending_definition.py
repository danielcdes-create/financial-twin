"""Nota v6 pending totals. Amounts are fictional. The clock is injected."""
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from bson import ObjectId
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitment_projection import monthly_projection
from app.commitments import CommitmentIn, commitment_restante, stored_document
from test_capture_batches import BatchDB


AS_OF = date(2026, 10, 12)


class PendingDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.db = BatchDB()

    def add(self, **fields):
        body = CommitmentIn(**fields)
        row = stored_document(body)
        row['_id'] = ObjectId()
        self.db.commitments.docs.append(row)
        return row

    def monthly(self, name, amount, due, **extra):
        fields = dict(kind='assinatura', name=name, purchase_date='2026-10-01', next_due_date=due,
                      amount=amount, frequency='mensal', payment_method='pix', renewal_status='automatica', status='ativo')
        fields.update(extra)
        return self.add(**fields)

    def cash(self, name, amount, due):
        return self.add(kind='obrigacao', obligation_plan='a_vista', name=name, purchase_date='2026-09-01',
                        next_due_date=due, cash_due_date=due, amount=amount, frequency='mensal',
                        payment_method='pix', renewal_status='nao_aplicavel', status='ativo')

    def project(self, as_of=AS_OF, months=12):
        return monthly_projection(self.db, months, as_of=as_of)

    def test_t1_month_kpi_keeps_overdue_and_splits_labels(self):
        self.monthly('A', '100.00', '2026-10-05')
        self.monthly('B', '50.00', '2026-10-20')
        linked = self.monthly('C', '70.00', '2026-10-05')
        linked['linked_transactions'] = [{'due_date': '2026-10-05', 'transaction_id': 'c', 'settled_next': True}]
        result = self.project()
        summary = result['summary']
        self.assertEqual(Decimal(summary['this_month']), Decimal('150.00'))
        self.assertEqual(Decimal(summary['overdue']), Decimal('100.00'))
        self.assertEqual(Decimal(summary['upcoming']), Decimal('50.00'))
        self.assertEqual(Decimal(result['items'][0]['total']), Decimal(summary['this_month']))
        self.assertGreaterEqual(Decimal(summary['next_12']), Decimal(summary['this_month']))
        november = Decimal(result['items'][1]['total'])
        self.assertEqual(november, Decimal('220.00'))

    def test_t2_linking_removes_and_unlinking_restores(self):
        item = self.monthly('A', '100.00', '2026-10-05')
        self.monthly('B', '50.00', '2026-10-20')
        self.assertEqual(Decimal(self.project()['summary']['this_month']), Decimal('150.00'))
        item['linked_transactions'] = [{'due_date': '2026-10-05', 'transaction_id': 'a'}]
        self.assertEqual(Decimal(self.project()['summary']['this_month']), Decimal('50.00'))
        item['linked_transactions'] = []
        self.assertEqual(Decimal(self.project()['summary']['this_month']), Decimal('150.00'))

    def test_t4_day_roll_does_not_change_restante(self):
        item = self.add(kind='parcelamento', name='E', purchase_date='2026-10-01', next_due_date='2026-10-05',
                        amount='30.00', frequency='mensal', payment_method='pix', renewal_status='nao_aplicavel',
                        installment_count=3, next_installment_number=1, status='ativo')
        for day in (date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 20)):
            restante = commitment_restante(item, day)
            self.assertEqual(restante['count'], 3)
            self.assertEqual(restante['amount'], Decimal('90.00'))
            projected = self.project(day)
            self.assertEqual(Decimal(projected['summary']['this_month']), Decimal('30.00'))
            self.assertEqual(Decimal(projected['items'][1]['total']), Decimal('30.00'))

    def test_t7_pre_cutoff_unlinked_stays_out(self):
        self.cash('Antiga', '70.00', '2026-09-25')
        result = self.project()
        self.assertEqual(Decimal(result['summary']['this_month']), Decimal('0'))
        self.assertEqual(Decimal(result['summary']['overdue']), Decimal('0'))
        self.assertTrue(all(Decimal(row['total']) == 0 for row in result['items']))

    def test_t8_november_kpi_includes_october_and_the_bar_does_not(self):
        self.cash('Outubro', '100.00', '2026-10-05')
        self.cash('Novembro', '60.00', '2026-11-10')
        result = self.project(date(2026, 11, 2))
        summary = result['summary']
        self.assertEqual(result['items'][0]['month'], '2026-11')
        self.assertEqual(Decimal(result['items'][0]['total']), Decimal('60.00'))
        self.assertEqual(Decimal(summary['this_month']), Decimal('160.00'))
        self.assertEqual(Decimal(summary['overdue']), Decimal('100.00'))
        self.assertEqual(Decimal(summary['upcoming']), Decimal('60.00'))
        self.assertNotEqual(Decimal(result['items'][0]['total']), Decimal(summary['this_month']))
        self.assertGreaterEqual(Decimal(summary['next_12']), Decimal(summary['this_month']))
        self.assertTrue(all(row['month'] >= '2026-11' for row in result['items']))
        self.assertTrue(all('2026-10-05' not in str(row['items']) for row in result['items']))

    def test_t9_october_bar_matches_the_month_kpi(self):
        self.monthly('A', '100.00', '2026-10-05')
        self.monthly('B', '50.00', '2026-10-20')
        october = self.project()
        self.assertEqual(Decimal(october['items'][0]['total']), Decimal(october['summary']['this_month']))
        self.assertGreaterEqual(Decimal(october['summary']['next_12']), Decimal(october['summary']['this_month']))
        november = self.project(date(2026, 11, 2))
        self.assertGreater(Decimal(november['summary']['this_month']), Decimal(november['items'][0]['total']))


if __name__ == '__main__':
    unittest.main()
