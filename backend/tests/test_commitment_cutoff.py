import sys
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitments import COMMITMENT_CUTOFF, presentation_state


class CutoffTests(unittest.TestCase):
    def test_fixed_cutoff_marks_later_unlinked_dues_overdue(self):
        self.assertEqual(COMMITMENT_CUTOFF, date(2026, 10, 1))
        self.assertEqual(presentation_state(date(2026, 9, 30), False, date(2026, 12, 1)), 'Pago (assumido)')
        self.assertEqual(presentation_state(date(2026, 10, 1), False, date(2026, 10, 11)), 'Vencido · sem vínculo')
        self.assertEqual(presentation_state(date(2026, 10, 10), False, date(2026, 12, 1)), 'Vencido · sem vínculo')
        self.assertEqual(presentation_state(date(2026, 10, 11), False, date(2026, 10, 11)), 'Previsto')
        self.assertEqual(presentation_state(date(2026, 10, 11), True, date(2026, 12, 1)), 'Pago vinculado')


if __name__ == '__main__':
    unittest.main()
