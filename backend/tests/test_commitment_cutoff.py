import sys
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitments import COMMITMENT_CUTOFF, commitment_cutoff, presentation_state, schedule


class CutoffTests(unittest.TestCase):
    def test_cutoff_rolls_forward_from_the_october_floor(self):
        self.assertEqual(COMMITMENT_CUTOFF, date(2026, 10, 1))
        self.assertEqual(commitment_cutoff(date(2026, 9, 30)), date(2026, 10, 1))
        self.assertEqual(commitment_cutoff(date(2026, 12, 15)), date(2026, 12, 1))
        december = commitment_cutoff(date(2026, 12, 15))
        self.assertEqual(presentation_state(date(2026, 9, 30), False, date(2026, 12, 1), december), 'Pago (assumido)')
        self.assertEqual(presentation_state(date(2026, 10, 10), False, date(2026, 12, 1), december), 'Pago (assumido)')
        self.assertEqual(presentation_state(date(2026, 12, 1), False, date(2026, 12, 11), december), 'Vencido · sem vínculo')
        self.assertEqual(presentation_state(date(2026, 12, 11), False, date(2026, 12, 11), december), 'Previsto')
        self.assertEqual(presentation_state(date(2026, 10, 11), True, date(2026, 12, 1), december), 'Pago vinculado')

    def test_partial_future_edit_changes_one_row_and_drops_a_cancelled_one(self):
        item = {'status': 'ativo', 'kind': 'assinatura', 'name': 'Revista', 'frequency': 'mensal',
                'renewal_status': 'automatica', 'amount': '40', 'next_due_date': date(2026, 12, 10),
                'installment_overrides': {
                    '2026-12-10': {'amount': '15.00', 'due_date': '2026-12-12', 'cancelled': False},
                    '2027-01-10': {'amount': '40.00', 'due_date': '2027-01-10', 'cancelled': True}}}
        rows = schedule(item, 3)
        self.assertEqual(rows[0]['due_date'], '2026-12-12')
        self.assertEqual(rows[0]['amount'], 15.0)
        self.assertNotIn('2027-01-10', [row['due_date'] for row in rows])
        self.assertEqual(rows[1]['due_date'], '2027-02-10')


if __name__ == '__main__':
    unittest.main()
