"""Evidence-backed, transactional repair of the 2026-09-26 Mongo audit findings.

Run from financial-app with --apply after inspecting the default dry-run.
The source documents are backed up locally before any database write.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from bson.decimal128 import Decimal128
from pymongo import MongoClient, UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.main import configured_mongodb_uri  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "data" / "mongodb-audit-repair-2026-09-26-before.jsonl"
PLATFORM_NAMES = {"iFood", "Mercado Livre", "Rappi", "99Food"}


def plan(db):
    merchants = {doc["_id"]: doc.get("nome_padrao") for doc in db.merchants.find({}, {"nome_padrao": 1})}
    same = [doc for doc in db.transactions.find({"merchant_id": {"$exists": True}, "platform_id": {"$exists": True}})
            if doc.get("merchant_id") == doc.get("platform_id") and merchants.get(doc["merchant_id"]) in PLATFORM_NAMES]
    direct_service = [doc for doc in same if merchants[doc["merchant_id"]] == "iFood"
                      and "IFOOD CLUB" in str(doc.get("estabelecimento_original") or "").upper()]
    platform_only = [doc for doc in same if doc not in direct_service]
    conflicting = [doc for doc in db.provisional_transactions.find({"status": "confirmed"})
                   if isinstance(doc.get("valor"), Decimal128)
                   and ((doc["valor"].to_decimal() < 0 and doc.get("fluxo") == "entrada")
                        or (doc["valor"].to_decimal() > 0 and doc.get("fluxo") == "saida"))]
    missing_type = list(db.provisional_transactions.find({"status": "confirmed", "tipo": {"$exists": False}}))
    evidence = {}
    for doc in [*conflicting, *missing_type]:
        event = db.raw_events.find_one({"event_id": doc.get("event_id")})
        parsed = (event or {}).get("parsed_payload") or {}
        if parsed.get("event_type") != "card_purchase" or parsed.get("direction") != "expense" or doc["valor"].to_decimal() >= 0:
            raise RuntimeError(f"A evidência de {doc.get('provisional_id')} não confirma uma compra de saída")
        evidence[doc["_id"]] = event["_id"]
    return platform_only, direct_service, conflicting, missing_type, evidence, merchants


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.mongodb_uri:
        raise SystemExit("MONGODB_URI não configurada")
    client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=10000)
    try:
        db = client[settings.mongodb_database]
        platform_only, direct_service, conflicting, missing_type, evidence, merchants = plan(db)
        summary = {"mode": "apply" if args.apply else "dry-run", "database": settings.mongodb_database,
                   "clear_platform_only_vendor": len(platform_only), "retain_direct_service_vendor": len(direct_service),
                   "fix_flow": len(conflicting), "fill_card_purchase_type": len(missing_type),
                   "platforms": dict(Counter(merchants[d["merchant_id"]] for d in platform_only))}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not args.apply:
            return
        targets = [*platform_only, *conflicting, *missing_type]
        if not targets:
            print("Nenhuma alteração necessária; rotina idempotente.")
            return
        if BACKUP.exists():
            raise RuntimeError(f"Backup anterior já existe: {BACKUP}. Inspecione-o antes de aplicar novamente.")
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        with BACKUP.open("x", encoding="utf-8") as stream:
            for doc in targets:
                stream.write(json_util.dumps({"collection": "transactions" if doc in platform_only else "provisional_transactions", "document": doc}) + "\n")
        now = datetime.now(timezone.utc)
        audits = []
        ops = []
        for doc in platform_only:
            ops.append(UpdateOne({"_id": doc["_id"], "merchant_id": doc["merchant_id"], "platform_id": doc["platform_id"], "status_revisao": {"$ne": "validado"}},
                                 {"$unset": {"merchant_id": ""}, "$set": {"updated_at": now}}))
            audits.append({"event": "platform_only_vendor_cleared", "transaction_id": doc["_id"], "source": "mongodb_audit_2026_09_26",
                           "before": {"merchant_id": doc["merchant_id"], "platform_id": doc["platform_id"]},
                           "after": {"merchant_id": None, "platform_id": doc["platform_id"]},
                           "change_reason": "source_descriptor_identifies_platform_only", "created_at": now})
        for doc in conflicting:
            audits.append({"event": "provisional_flow_corrected", "provisional_id": doc["provisional_id"], "raw_event_id": evidence[doc["_id"]],
                           "source": "mongodb_audit_2026_09_26", "before": {"fluxo": doc.get("fluxo")},
                           "after": {"fluxo": "saida"}, "change_reason": "raw_card_purchase_and_negative_amount", "created_at": now})
        for doc in missing_type:
            audits.append({"event": "provisional_type_filled", "provisional_id": doc["provisional_id"], "raw_event_id": evidence[doc["_id"]],
                           "source": "mongodb_audit_2026_09_26", "before": {"tipo": None},
                           "after": {"tipo": "compra"}, "change_reason": "raw_event_card_purchase", "created_at": now})
        with client.start_session() as session:
            with session.start_transaction():
                if ops:
                    result = db.transactions.bulk_write(ops, ordered=True, session=session)
                    if result.modified_count != len(ops):
                        raise RuntimeError(f"Atualização concorrente em transactions: {result.modified_count}/{len(ops)}")
                for doc in conflicting:
                    result = db.provisional_transactions.update_one(
                        {"_id": doc["_id"], "fluxo": doc["fluxo"], "valor": doc["valor"], "status": "confirmed"},
                        {"$set": {"fluxo": "saida", "updated_at": now}}, session=session)
                    if result.modified_count != 1:
                        raise RuntimeError("Atualização concorrente na correção de fluxo")
                for doc in missing_type:
                    result = db.provisional_transactions.update_one(
                        {"_id": doc["_id"], "tipo": {"$exists": False}, "valor": doc["valor"], "status": "confirmed"},
                        {"$set": {"tipo": "compra", "updated_at": now}}, session=session)
                    if result.modified_count != 1:
                        raise RuntimeError("Atualização concorrente no preenchimento de tipo")
                db.audit_log.insert_many(audits, ordered=True, session=session)
        print(json.dumps({"applied": len(targets), "audit_events": len(audits), "backup": str(BACKUP)}, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    main()
