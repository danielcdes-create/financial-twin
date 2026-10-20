from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import re
from app.financial_time import utc_instant
from app.economic_policy import role
from app.matching_scope import compatible, strong_reference

WEIGHTS = {"amount": .35, "time": .20, "merchant": .25, "instrument": .08, "type": .07, "history": .05}
HIGH_THRESHOLD = .86
MEDIUM_THRESHOLD = .62


def decimal_value(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def normalized_merchant(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def amount_similarity(a, b):
    left, right = decimal_value(a), decimal_value(b)
    if left is None or right is None or left == 0 or right == 0:
        return 0.0
    return 1.0 if abs(left) == abs(right) else max(0.0, 1 - float(abs(abs(left) - abs(right)) / max(abs(left), abs(right))))


def temporal_similarity(a, b):
    try:
        a, b = utc_instant(a), utc_instant(b)
    except (ValueError, TypeError):
        return 0.0
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    hours = abs((a - b).total_seconds()) / 3600
    return max(0.0, 1 - hours / 72)


def merchant_similarity(a, b):
    left, right = normalized_merchant(a), normalized_merchant(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def score_evidence(evidence, transaction):
    p = evidence.get("parsed_payload") or {}
    scored = {
        "amount": amount_similarity(p.get("amount"), transaction.get("valor")),
        "time": temporal_similarity(p.get("event_timestamp") or evidence.get("captured_at"), transaction.get("data_evento")),
        "merchant": merchant_similarity(p.get("merchant_raw") or p.get("merchant"), transaction.get("estabelecimento_original") or transaction.get("descricao_original") or transaction.get("merchant_raw")),
        "instrument": 1.0 if p.get("payment_instrument_hint") and p.get("payment_instrument_hint") in str(transaction.get("instrument_hint", "")) else 0.0,
        "type": 1.0 if p.get("event_type") and p.get("event_type") in str(transaction.get("tipo", "")) else 0.5,
        "history": 0.5,
    }
    available = {k: v for k, v in scored.items() if v > 0 or k in ("amount", "time", "merchant")}
    weights = {k: WEIGHTS[k] for k in available}
    total = sum(weights.values())
    return round(sum(scored[k] * weights[k] for k in weights) / total, 4), scored


def classify(score):
    if score >= HIGH_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def automatic_eligible(event, candidate, db=None, *, profiles=None, accounts_by_id=None):
    if not compatible(event, candidate, db, profiles=profiles, accounts_by_id=accounts_by_id):
        return False
    # Similarity proposes review; only scoped durable identity can absorb money.
    # A human declaration is a new occurrence even if its words are identical.
    if event.get('source_provider') == 'mobile_voice' or not strong_reference(event, candidate):
        return False
    parsed = event.get('parsed_payload') or {}
    left, right = decimal_value(parsed.get('amount')), decimal_value(candidate.get('valor'))
    if left is None or right is None or abs(left) != abs(right):
        return False
    if (parsed.get('direction', 'expense') == 'expense') != (right < 0):
        return False
    if role(candidate, 'provisional_id' in candidate) == 'neutral':
        return False
    if event.get('source_type') == 'manual':
        return False
    instrument = parsed.get('payment_instrument_hint')
    if instrument and instrument not in str(candidate.get('instrument_hint') or ''):
        return False
    return True
