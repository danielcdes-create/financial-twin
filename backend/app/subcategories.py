"""Category-scoped catalog changes and record relabeling commit together."""
import re
import hashlib
from uuid import uuid4
from bson import json_util
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from app.auth import require_session
from app.atomic import mongo_snapshot, mongo_transaction, session_database
from app.taxonomy import normalized, subcategory_catalog
from app.movement_insert_lock import lock_movement_insert
from app.category_filter import ACTIVE_CAPTURES, catalog_category_names

router = APIRouter(prefix="/v1/categories/subcategories", dependencies=[Depends(require_session)])


def database(request: Request):
    if request.app.state.db is None:
        raise HTTPException(503, "Banco indisponível.")
    return request.app.state.db


def _has_collection(db, name: str) -> bool:
    return not isinstance(db, dict) or name in db


class SubcategoryCreate(BaseModel):
    category: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)


class SubcategoryRename(SubcategoryCreate):
    new_name: str = Field(min_length=1, max_length=120)
    expected_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def clean_name(name):
    name = re.sub(r"\s+", " ", name).strip()
    if not normalized(name):
        raise HTTPException(422, "Informe um nome de subcategoria válido.")
    return name


def context(db, category):
    names = catalog_category_names(db)
    name = next((value for value in reversed(names) if normalized(value) == normalized(category)), None)
    if not name:
        raise HTTPException(404, "Categoria não encontrada.")
    catalog = subcategory_catalog(db)
    items = next((items for parent, items in catalog.items() if normalized(parent) == normalized(name)), {})
    overrides = {normalized(item["name"]): item for doc in db.categories.find({})
                 if normalized(doc.get("name", "")) == normalized(name) for item in doc.get("subcategories", [])}
    usage = {}
    for collection, field in ((db.transactions, "categoria"), (db.provisional_transactions, "categoria_sugerida")):
        for row in collection.find({field: {"$nin": [None, ""]}, "subcategoria": {"$nin": [None, ""]}},
                                   {field: 1, "subcategoria": 1, "status": 1, "revision": 1}):
            if normalized(row.get(field, "")) != normalized(name): continue
            if field == "categoria_sugerida" and row.get("status") not in ACTIVE_CAPTURES: continue
            usage.setdefault(normalized(row["subcategoria"]), []).append([field, str(row["_id"]), row.get("revision"), row.get("status")])
    if _has_collection(db, "commitments"):
        for row in db.commitments.find({}):
            cat = row.get("category") or row.get("categoria") or ""
            sub = row.get("subcategoria") or ""
            if normalized(cat) == normalized(name) and normalized(sub):
                key = normalized(sub)
                if key in items:
                    items[key]["usage_count"] = items[key].get("usage_count", 0) + 1
                usage.setdefault(key, []).append(["commitment", str(row.get("_id")), row.get("revision")])
    if _has_collection(db, "future_schedule_overrides"):
        for row in db.future_schedule_overrides.find({}):
            profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
            cat = profile.get("categoria") or ""
            sub = profile.get("subcategoria") or ""
            if normalized(cat) == normalized(name) and normalized(sub):
                key = normalized(sub)
                if key in items:
                    items[key]["usage_count"] = items[key].get("usage_count", 0) + 1
                usage.setdefault(key, []).append(["override", str(row.get("_id")), row.get("revision")])
    for key, item in items.items():
        payload = [normalized(name), key, overrides.get(key), sorted(usage.get(key, []), key=str)]
        item["token"] = hashlib.sha256(json_util.dumps(payload, sort_keys=True).encode()).hexdigest()
    return name, items


def override(db, category, name, active, now, replacement=None):
    key = normalized(category).lower()
    document = db.categories.find_one({"key": key}) or {}
    items = [dict(item) for item in document.get("subcategories", []) if normalized(item["name"]) != normalized(name)]
    items.append({"name": name, "active": active, "replacement": replacement, "updated_at": now, "generation": uuid4().hex})
    db.categories.update_one({"key": key}, {
        "$set": {"name": category, "key": key, "active": True, "subcategories": items, "updated_at": now},
        "$setOnInsert": {"created_at": now, "source": "mobile_manual"},
    }, upsert=True)


def check_review(old, expected_token):
    if not expected_token or not isinstance(expected_token, str):
        raise HTTPException(428, "Atualize a lista para conferir a subcategoria e seus registros antes de alterar.")
    if old["token"] != expected_token:
        raise HTTPException(409, "A subcategoria ou seus registros mudaram. Atualize a lista e confira novamente; sua edição não foi aplicada.")


def relabel(db, category, name, replacement, now):
    counts = []
    for collection, field in ((db.transactions, "categoria"), (db.provisional_transactions, "categoria_sugerida")):
        # Existing labels can differ in accents/case. Capture the matching IDs
        # once, so a spelling correction cannot update the same row twice.
        query = {field: {"$nin": [None, ""]}, "subcategoria": {"$nin": [None, ""]}}
        if field == "categoria_sugerida":
            query["status"] = {"$in": ACTIVE_CAPTURES}
        identities = [row["_id"] for row in collection.find(query, {field: 1, "subcategoria": 1})
                      if normalized(row[field]) == normalized(category) and normalized(row["subcategoria"]) == normalized(name)]
        count = 0
        if identities:
            pair_query = {"_id": {"$in": identities}}
            update = {"$set": {"subcategoria": replacement, "updated_at": now}}
            if field == "categoria_sugerida":
                pair_query["status"] = {"$in": ACTIVE_CAPTURES}
                # Legacy captures use revision 1 when absent; advance them to 2.
                count += collection.update_many({**pair_query, "revision": {"$exists": True}},
                    {**update, "$inc": {"revision": 1}}).modified_count
                count += collection.update_many({**pair_query, "revision": {"$exists": False}},
                    {"$set": {**update["$set"], "revision": 2}}).modified_count
            else:
                count += collection.update_many(pair_query, update).modified_count
        counts.append(count)
    if _has_collection(db, "commitments"):
        for row in list(db.commitments.find({})):
            cat = row.get("category") or row.get("categoria") or ""
            sub = row.get("subcategoria") or ""
            if normalized(cat) == normalized(category) and normalized(sub) == normalized(name):
                db.commitments.update_one({"_id": row["_id"]}, {"$set": {"subcategoria": replacement, "updated_at": now}, "$inc": {"revision": 1}})
    if _has_collection(db, "future_schedule_overrides"):
        for row in list(db.future_schedule_overrides.find({})):
            profile = dict(row.get("profile") or {}) if isinstance(row.get("profile"), dict) else {}
            if normalized(profile.get("categoria") or "") == normalized(category) and normalized(profile.get("subcategoria") or "") == normalized(name):
                if replacement:
                    profile["subcategoria"] = replacement
                else:
                    profile.pop("subcategoria", None)
                db.future_schedule_overrides.update_one({"_id": row["_id"]}, {"$set": {"profile": profile, "updated_at": now}, "$inc": {"revision": 1}})
    return counts


@router.get("")
def list_subcategories(category: str = Query(min_length=1, max_length=120), db=Depends(database)):
    with mongo_snapshot(db) as (snapshot, _):
        name, items = context(snapshot, category)
        return {"category": name, "details": sorted(items.values(), key=lambda item: item["name"].casefold())}


@router.post("", status_code=201)
def create_subcategory(body: SubcategoryCreate, db=Depends(database)):
    with mongo_transaction(db) as session:
        target = session_database(db, session)
        lock_movement_insert(target)
        category, items = context(target, body.category)
        name = clean_name(body.name)
        if normalized(name) in items:
            raise HTTPException(409, "Esta subcategoria já existe nesta categoria.")
        now = datetime.now(timezone.utc)
        override(target, category, name, True, now)
        target.audit_log.insert_one({"event": "subcategory_created", "category": category, "name": name,
                                    "source": "mobile_manual", "created_at": now})
        return {"category": category, "name": name, "usage_count": 0}


@router.patch("")
def rename_subcategory(body: SubcategoryRename, db=Depends(database)):
    with mongo_transaction(db) as session:
        target = session_database(db, session)
        lock_movement_insert(target)
        category, items = context(target, body.category)
        old = items.get(normalized(body.name))
        if not old:
            raise HTTPException(404, "Subcategoria não encontrada. Recarregue a lista.")
        check_review(old, body.expected_token)
        name = clean_name(body.new_name)
        if normalized(name) != normalized(old["name"]) and normalized(name) in items:
            raise HTTPException(409, "Já existe uma subcategoria com este nome nesta categoria.")
        now = datetime.now(timezone.utc)
        transactions, captures = relabel(target, category, old["name"], name, now)
        if normalized(name) != normalized(old["name"]):
            override(target, category, old["name"], False, now, name)
        override(target, category, name, True, now)
        target.audit_log.insert_one({"event": "subcategory_renamed", "category": category, "name": old["name"],
                                    "replacement": name, "affected_transactions": transactions,
                                    "affected_provisionals": captures, "source": "mobile_manual", "created_at": now})
        return {"category": category, "name": name, "affected_count": transactions + captures}


@router.delete("")
def delete_subcategory(category: str = Query(min_length=1, max_length=120),
                       name: str = Query(min_length=1, max_length=120),
                       replacement: str | None = Query(default=None, min_length=1, max_length=120),
                       expected_token: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
                       db=Depends(database)):
    with mongo_transaction(db) as session:
        target = session_database(db, session)
        lock_movement_insert(target)
        category, items = context(target, category)
        old = items.get(normalized(name))
        if not old:
            raise HTTPException(404, "Subcategoria não encontrada. Recarregue a lista.")
        check_review(old, expected_token)
        substitute = None
        if replacement:
            substitute = items.get(normalized(replacement))
            if not substitute or normalized(replacement) == normalized(name):
                raise HTTPException(422, "Escolha outra subcategoria existente nesta categoria.")
        if old["usage_count"] and not substitute:
            raise HTTPException(409, "A subcategoria está em uso. Escolha uma subcategoria substituta.")
        now = datetime.now(timezone.utc)
        transactions, captures = relabel(target, category, old["name"], substitute["name"], now) if substitute else (0, 0)
        override(target, category, old["name"], False, now, substitute["name"] if substitute else None)
        target.audit_log.insert_one({"event": "subcategory_deleted", "category": category, "name": old["name"],
                                    "replacement": substitute["name"] if substitute else None,
                                    "affected_transactions": transactions, "affected_provisionals": captures,
                                    "source": "mobile_manual", "created_at": now})
        return {"category": category, "name": old["name"], "deleted": True,
                "affected_count": transactions + captures}
