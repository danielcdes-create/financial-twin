"""User-owned account positions, kept separate from the imported v1.2 ledger."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unicodedata

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from app.source_tokens import source_token
from app.account_profiles import ACCOUNT_CATALOG, ACCOUNT_KEYS, account_profiles, legacy_accounts_by_profile, validate_account_key
from app.account_profiles import effective_capture_account_key
from app.transfer_legs import resolved_transfer_legs
from app.economic_policy import duplicate_of_ledger


def account_key_for_legacy(account):
    if account.get("tipo") != "conta_corrente":
        return None
    plain = unicodedata.normalize("NFKD", str(account.get("instituicao") or ""))
    name = plain.encode("ascii", "ignore").decode().casefold()
    if "itau" in name: return "itau"
    if "santander" in name: return "santander"
    if "inter" in name: return "inter"
    if "btg" in name: return "btg"
    if "mercado pago" in name: return "mercado_pago"
    return None


def _decimal(value):
    if isinstance(value, Decimal128): return value.to_decimal()
    return Decimal(str(value))


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _linked_statement_inside_reference(db, capture, account_id, after_reference, linked_by_id=None):
    """A capture promoted from a statement leg already inside the reference is not a new movement."""
    link = str(capture.get("linked_transaction_id") or "")
    if not ObjectId.is_valid(link):
        return False
    linked = linked_by_id.get(link) if linked_by_id is not None else db.transactions.find_one({"_id": ObjectId(link)}, {"account_id": 1, "data_evento": 1})
    when = linked.get("data_evento") if linked and linked.get("account_id") == account_id else None
    return bool(when) and not after_reference(when)


def account_positions(db):
    """A reference balance plus posted movements; captures without an account stay visible as a gap."""
    profiles = account_profiles(db)
    legacy_by_key = legacy_accounts_by_profile(db)
    ambiguous_keys = {account_key_for_legacy(row) for row in db.accounts.find({"tipo":"conta_corrente"})} - set(legacy_by_key)
    keys = {item["key"] for item in profiles}
    snapshots = {item["account_key"]:item for item in db.account_balance_snapshots.find({}) if item.get("account_key") in keys}
    earliest=min((_aware(item['as_of']) for item in snapshots.values()),default=None)
    legacy_transactions = list(db.transactions.find({'data_evento':{'$gte':earliest}}, {"account_id":1,"data_evento":1,"valor":1})) if earliest else []
    # A capture can precede the cutoff while its linked bank leg follows it.
    captures = list(db.provisional_transactions.find({"status":"confirmed"}, {"data_evento":1,"valor":1,"natureza":1,"transfer":1,"account_profile_id":1,"account_id":1,"linked_transaction_id":1})) if earliest else []
    linked_ids = [ObjectId(str(c["linked_transaction_id"])) for c in captures if ObjectId.is_valid(str(c.get("linked_transaction_id") or ""))]
    linked_by_id = {str(row["_id"]): row for row in db.transactions.find({"_id": {"$in": linked_ids}}, {"account_id": 1, "data_evento": 1})} if linked_ids else {}
    transfer_legs = resolved_transfer_legs(db, captures, legacy_by_key)
    result = []
    for profile in profiles:
        key = profile["key"]
        snapshot = snapshots.get(key)
        legacy = legacy_by_key.get(key)
        amount = _decimal(snapshot["amount"]) if snapshot else None
        movement_count = 0
        pending_count = 0
        pending_transfer_count = 0
        if snapshot:
            cutoff = _aware(snapshot["as_of"])
            def after_reference(when):
                when = _aware(when)
                return when >= cutoff if snapshot.get("reference_kind") == "civil_day" else when > cutoff
            counted_legacy_ids=set()
            for tx in legacy_transactions:
                if legacy and tx.get("account_id") == legacy["_id"] and tx.get("data_evento") and after_reference(tx["data_evento"]):
                    amount += _decimal(tx["valor"])
                    movement_count += 1
                    counted_legacy_ids.add(str(tx["_id"]))
            for capture in captures:
                when = capture.get("data_evento")
                if not when: continue
                transfer = capture.get("transfer") or {}
                value = _decimal(capture["valor"])
                if transfer:
                    magnitude = abs(value)
                    for field, link, sign in (("from_account_key","source_transaction_id",-1),("to_account_key","destination_transaction_id",1)):
                        if transfer.get(field) != key: continue
                        leg = transfer_legs[str(capture.get("_id"))][link]
                        posted = leg["transaction"]
                        relevant = after_reference(posted["data_evento"]) if leg["status"] == "valid" else after_reference(when)
                        if leg["status"] in {"missing","divergent"} or (leg["status"] == "unlinked" and relevant):
                            pending_transfer_count += 1
                        if not relevant: continue
                        if leg["status"] == "unlinked":
                            amount += magnitude * sign
                            movement_count += 1
                        elif leg["status"] == "valid" and str(posted["_id"]) not in counted_legacy_ids:
                            amount += _decimal(posted["valor"])
                            movement_count += 1
                            counted_legacy_ids.add(str(posted["_id"]))
                elif not after_reference(when): continue
                elif duplicate_of_ledger(capture, counted_legacy_ids): continue
                elif legacy and _linked_statement_inside_reference(db, capture, legacy["_id"], after_reference, linked_by_id): continue
                elif effective_capture_account_key(capture, legacy_by_key) == key:
                    amount += value
                    movement_count += 1
                elif not effective_capture_account_key(capture, legacy_by_key):
                    pending_count += 1
        result.append({
            **profile, "legacy_account_id":str(legacy["_id"]) if legacy else None,
            "legacy_account_ambiguous":key in ambiguous_keys,
            "legacy_status":legacy.get("status") if legacy else None,
            "balance":float(amount) if amount is not None else None,
            "balance_reference":float(_decimal(snapshot["amount"])) if snapshot else None,
            "balance_reference_token":source_token(snapshot),
            "balance_as_of":_aware(snapshot["as_of"]).isoformat() if snapshot else None,
            "balance_reference_date":snapshot.get("as_of_date") if snapshot and snapshot.get("reference_kind") == "civil_day" else None,
            "movement_count":movement_count, "unassigned_capture_count":pending_count,"pending_transfer_count":pending_transfer_count,
            "balance_status":"sem_referencia" if snapshot is None else "capturas_sem_conta" if pending_count else "transferencias_pendentes" if pending_transfer_count else "calculado",
        })
    return result


def reconcile_capture_transfer(db, capture):
    """Link only unique opposite-signed bank legs; never infer a missing leg."""
    transfer = capture.get("transfer") or {}
    if not transfer or capture.get("status") != "confirmed":
        return transfer, False
    account_map = {key: row["_id"] for key, row in legacy_accounts_by_profile(db).items()}
    profiles = {item["key"]:item for item in account_profiles(db)}
    def bank_identity(key):
        institution=profiles.get(key,{}).get("instituicao","")
        return account_key_for_legacy({"tipo":"conta_corrente","instituicao":institution}) or unicodedata.normalize("NFKD",institution).encode("ascii","ignore").decode().casefold().strip()
    if bank_identity(transfer.get("from_account_key")) == bank_identity(transfer.get("to_account_key")):
        # A bank name in the statement cannot distinguish two accounts there.
        return transfer, False
    when = _aware(capture["data_evento"])
    magnitude = abs(_decimal(capture["valor"]))
    for peer in db.provisional_transactions.find({"status":"confirmed"}, {"_id":1,"data_evento":1,"valor":1,"transfer":1}):
        peer_transfer=peer.get("transfer") or {}
        if peer.get("_id")==capture.get("_id") or not peer_transfer: continue
        if (peer_transfer.get("from_account_key"),peer_transfer.get("to_account_key"))==(transfer.get("from_account_key"),transfer.get("to_account_key")) and abs(_decimal(peer["valor"]))==magnitude and abs((_aware(peer["data_evento"])-when).total_seconds())<=2*86400:
            return transfer,False
    updated = dict(transfer)
    for key_field, id_field, sign, counterparty in (
        ("from_account_key", "source_transaction_id", -1, transfer.get("to_account_key")),
        ("to_account_key", "destination_transaction_id", 1, transfer.get("from_account_key")),
    ):
        account_id = account_map.get(transfer.get(key_field))
        if not account_id or updated.get(id_field): continue
        expected = Decimal128(str(magnitude * sign))
        candidates = list(db.transactions.find({
            "account_id":account_id,
            "data_evento":{"$gte":when-timedelta(days=2),"$lte":when+timedelta(days=2)},
            "valor":expected,
        }, {"_id":1,"descricao_original":1,"estabelecimento_original":1}).limit(501))
        def supports_pair(candidate):
            plain = unicodedata.normalize("NFKD",str(candidate.get("descricao_original") or candidate.get("estabelecimento_original") or ""))
            description = plain.encode("ascii","ignore").decode().casefold()
            # Use the imported institution when available, so editing metadata
            # cannot rewrite or invalidate historical bank evidence.
            peer_account = legacy_accounts_by_profile(db).get(counterparty)
            name = peer_account.get("instituicao", "") if peer_account else profiles.get(counterparty, {}).get("instituicao", "")
            counterparty_name = unicodedata.normalize("NFKD",name).encode("ascii","ignore").decode().casefold()
            return bool(counterparty_name and counterparty_name in description)
        # A bounded scan cannot prove uniqueness when it overflows.
        if len(candidates)>500: continue
        candidates=[candidate for candidate in candidates if supports_pair(candidate)]
        if len(candidates) == 1:
            other = db.provisional_transactions.find_one({"transfer."+id_field:str(candidates[0]["_id"]),"_id":{"$ne":capture["_id"]}}, {"_id":1})
            if not other: updated[id_field] = str(candidates[0]["_id"])
    updated["reconciliation_status"] = (
        "conciliada" if updated.get("source_transaction_id") and updated.get("destination_transaction_id")
        else "parcial" if updated.get("source_transaction_id") or updated.get("destination_transaction_id")
        else "aguardando_extratos"
    )
    return updated, updated != transfer
