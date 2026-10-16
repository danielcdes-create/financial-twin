import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitments import automatic_suggestion, suggestion_window


class SuggestionWindowTests(unittest.TestCase):
    def test_window_is_seven_days_before_and_ten_after(self):
        start, end = suggestion_window(date(2026, 10, 10))
        self.assertEqual(start, date(2026, 10, 3))
        self.assertEqual(end, date(2026, 10, 20))

    def test_previous_month_with_the_same_name_and_amount_is_not_suggested(self):
        due = date(2026, 10, 10)
        amount = Decimal("35.99")
        self.assertFalse(automatic_suggestion(date(2026, 9, 10), due, related=True, amount=amount, expected=amount))
        self.assertTrue(automatic_suggestion(date(2026, 10, 10), due, related=True, amount=amount, expected=amount))
        self.assertTrue(automatic_suggestion(date(2026, 10, 9), due, related=False, amount=amount, expected=amount))
        self.assertFalse(automatic_suggestion(date(2026, 10, 10), due, related=False, amount=Decimal("80"), expected=amount))


if __name__ == "__main__":
    unittest.main()
