import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from app.matching import amount_similarity, merchant_similarity, temporal_similarity, score_evidence, classify

class MatchingTests(unittest.TestCase):
    def test_amount_similarity_normalizes_sign_and_currency_decimals(self):
        self.assertEqual(amount_similarity("26,00".replace(",","."), -26), 1.0)
        self.assertEqual(amount_similarity(25, 0), 0.0)

    def test_time_similarity_decays_and_handles_timezone(self):
        a=datetime(2026,9,23,12,tzinfo=timezone.utc)
        self.assertEqual(temporal_similarity(a,a),1.0)
        self.assertEqual(temporal_similarity(a,a+timedelta(days=3)),0.0)

    def test_merchant_similarity_ignores_punctuation_and_case(self):
        self.assertEqual(merchant_similarity("UBER *TRIP","Uber Trip"),1.0)

    def test_perfect_match_scores_high(self):
        timestamp=datetime(2026,9,23,12,tzinfo=timezone.utc)
        event={"captured_at":timestamp,"parsed_payload":{"amount":26,"merchant_raw":"Padaria ABC","event_timestamp":timestamp,"event_type":"card_purchase"}}
        tx={"valor":-26,"estabelecimento_original":"PADARIA ABC","data_evento":timestamp,"tipo":"compra"}
        score,_=score_evidence(event,tx)
        self.assertGreaterEqual(score,.86)
        self.assertEqual(classify(score),"HIGH")

    def test_thresholds_are_ordered(self):
        self.assertEqual(classify(.86),"HIGH")
        self.assertEqual(classify(.62),"MEDIUM")
        self.assertEqual(classify(.61),"LOW")

if __name__=="__main__": unittest.main()
