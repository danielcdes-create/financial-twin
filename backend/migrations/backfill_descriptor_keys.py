"""Build an additive exact-descriptor index outside the validated ledger.

Dry run by default. --apply is idempotent and never edits transactions.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient, ReplaceOne
from app.config import get_settings
from app.main import canonical_key, configured_mongodb_uri


def backfill(db, apply=False):
    index=db.transaction_descriptor_index
    previous={item["_id"]:item.get("key") for item in index.find({},{"key":1})}
    seen_ids=set()
    scanned=changed=0
    operations=[]
    for item in db.transactions.find({}, {"estabelecimento_original":1,"descricao_original":1}).sort("_id",1):
        scanned+=1
        tx_id=item["_id"]
        seen_ids.add(tx_id)
        key=canonical_key(str(item.get("estabelecimento_original") or item.get("descricao_original") or ""))
        if previous.get(tx_id)==key: continue
        changed+=1
        operations.append(ReplaceOne({"_id":tx_id},{"_id":tx_id,"key":key,"indexed_at":datetime.now(timezone.utc)},upsert=True))
        if apply and len(operations)>=500:
            index.bulk_write(operations,ordered=False)
            operations=[]
    orphan_ids=set(previous)-seen_ids
    changed+=len(orphan_ids)
    if apply:
        if operations: index.bulk_write(operations,ordered=False)
        if orphan_ids: index.delete_many({"_id":{"$in":list(orphan_ids)}})
        index.create_index([("key",1),("_id",1)],name="exact_descriptor_batch")
        db.schema_migrations.update_one({"_id":"descriptor_index_v1"},{"$set":{"complete":True,"scanned":scanned,"changed":changed,"completed_at":datetime.now(timezone.utc)}},upsert=True)
    return {"scanned":scanned,"changed":changed,"applied":apply}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    settings=get_settings()
    with MongoClient(configured_mongodb_uri(settings),serverSelectionTimeoutMS=5000) as client:
        print(backfill(client[settings.mongodb_database],args.apply))
