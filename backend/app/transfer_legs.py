"""Resolve transfer evidence independently of a consumer's cash/date window."""
from bson import ObjectId
from app.economic_policy import amount


def resolved_transfer_legs(db, captures, legacy_by_key):
    ids = {str((row.get("transfer") or {}).get(field)) for row in captures
           for field in ("source_transaction_id", "destination_transaction_id")
           if (row.get("transfer") or {}).get(field)}
    lookup = [ObjectId(value) if ObjectId.is_valid(value) else value for value in ids]
    posted = {str(row["_id"]): row for row in db.transactions.find({"_id": {"$in": lookup}})} if lookup else {}
    result = {}
    for capture in captures:
        transfer = capture.get("transfer") or {}
        legs = {}
        magnitude = abs(amount(capture.get("valor")) or 0)
        for key_field, id_field, sign in (("from_account_key", "source_transaction_id", -1),
                                         ("to_account_key", "destination_transaction_id", 1)):
            identity = transfer.get(id_field)
            if not identity:
                legs[id_field] = {"status": "unlinked", "transaction": None}
                continue
            row = posted.get(str(identity))
            legacy = legacy_by_key.get(transfer.get(key_field))
            correct_account = bool(row and ((legacy and row.get("account_id") == legacy["_id"])
                or row.get("account_profile_id") == transfer.get(key_field)))
            valid = bool(row and correct_account and row.get("data_evento")
                         and amount(row.get("valor")) == magnitude * sign)
            legs[id_field] = {"status": "valid" if valid else "divergent" if row else "missing", "transaction": row}
        result[str(capture.get("_id"))] = legs
    return result
