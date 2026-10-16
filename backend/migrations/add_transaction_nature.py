"""Add the optional economic nature field and deterministically backfill safe records.

Dry run is the default. Use --apply only against the intended configured database.
"""
import argparse
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient, UpdateOne

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.config import Settings
from app.main import configured_mongodb_uri
from app.taxonomy import NATURES, infer_classification


def plan(db):
    operations=[];counts={nature:0 for nature in NATURES}
    projection={"tipo":1,"natureza":1,"categoria":1,"subcategoria":1,"descricao_original":1,"estabelecimento_original":1,"classification.migration_reason":1}
    for document in db.transactions.find({"$or":[{"natureza":{"$exists":False}},{"natureza":None}]},projection):
        suggestion=infer_classification(document)
        nature=suggestion.get("natureza")
        if not nature: continue
        changes={"natureza":nature,"updated_at":datetime.now(timezone.utc)}
        # These center-cost changes are limited to explicit descriptor rules.
        if suggestion.get("categoria"): changes["categoria"]=suggestion["categoria"]
        if suggestion.get("subcategoria"): changes["subcategoria"]=suggestion["subcategoria"]
        operations.append(UpdateOne({"_id":document["_id"],"$or":[{"natureza":{"$exists":False}},{"natureza":None}]},{"$set":changes}))
        counts[nature]+=1
    return operations,counts


def validator_with_nature(db):
    info=next(db.list_collections(filter={"name":"transactions"}),None)
    validator=copy.deepcopy(((info or {}).get("options") or {}).get("validator") or {})
    schema=validator.get("$jsonSchema")
    if not schema: raise RuntimeError("transactions does not have a $jsonSchema validator")
    schema.setdefault("properties",{})["natureza"]={
        "enum":list(NATURES),
        "description":"Natureza econômica transversal; mantém o tipo operacional legado.",
    }
    return validator


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    settings=Settings(_env_file=Path(__file__).parents[2]/".env")
    client=MongoClient(configured_mongodb_uri(settings),serverSelectionTimeoutMS=5000)
    db=client[settings.mongodb_database]
    client.admin.command("ping")
    operations,counts=plan(db)
    print({"database":settings.mongodb_database,"mode":"apply" if args.apply else "dry-run","eligible":len(operations),"by_nature":counts})
    if args.apply:
        validator=validator_with_nature(db)
        db.command({"collMod":"transactions","validator":validator,"validationLevel":"strict","validationAction":"error"})
        result=db.transactions.bulk_write(operations,ordered=False) if operations else None
        db.audit_log.insert_one({"event":"transaction_nature_migration","source":"NT-FIN-001-R2","eligible":len(operations),"modified":result.modified_count if result else 0,"by_nature":counts,"created_at":datetime.now(timezone.utc)})
        print({"modified":result.modified_count if result else 0,"validator_updated":True})
    client.close()


if __name__=="__main__": main()
