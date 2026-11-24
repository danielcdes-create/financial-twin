"""Canonical MongoDB BSON $jsonSchema validator harness for unit/contract tests."""
from __future__ import annotations

import copy
import datetime
import json
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bson import ObjectId
from bson.decimal128 import Decimal128
from bson.int64 import Int64
from pymongo.errors import DuplicateKeyError, WriteError

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALIDATORS_V1_2_PATH = FIXTURES_DIR / "validators-v1.2.json"


class BsonSchemaValidationError(AssertionError, ValueError):
    """Raised when a Python/BSON document violates a MongoDB $jsonSchema contract."""


@lru_cache(maxsize=1)
def load_canonical_validators() -> dict[str, dict[str, Any]]:
    raw = json.loads(VALIDATORS_V1_2_PATH.read_text(encoding="utf-8"))
    collections = raw.get("collections") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, entry in collections.items():
        out[name] = unwrap_json_schema(entry)
    return out


def unwrap_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise TypeError(f"Expected dict schema, got {type(schema).__name__}")
    if "validator" in schema and isinstance(schema["validator"], dict):
        schema = schema["validator"]
    if "$jsonSchema" in schema and isinstance(schema["$jsonSchema"], dict):
        schema = schema["$jsonSchema"]
    return schema


def get_collection_schema(collection_name: str) -> dict[str, Any]:
    schemas = load_canonical_validators()
    if collection_name not in schemas:
        raise KeyError(
            f"No canonical $jsonSchema registered for collection {collection_name!r}. "
            f"Available: {sorted(schemas)}"
        )
    return schemas[collection_name]


def _matches_single_bson_type(btype: str, value: Any) -> bool:
    if btype == "null":
        return value is None
    if btype == "bool":
        return isinstance(value, bool)
    if btype == "int":
        return isinstance(value, int) and not isinstance(value, (bool, Int64))
    if btype == "long":
        return isinstance(value, (int, Int64)) and not isinstance(value, bool)
    if btype == "double":
        return isinstance(value, float)
    if btype == "decimal":
        return isinstance(value, Decimal128)
    if btype == "number":
        return (
            isinstance(value, (int, float, Decimal128, Int64))
            and not isinstance(value, bool)
        )
    if btype == "string":
        return isinstance(value, str)
    if btype == "objectId":
        return isinstance(value, ObjectId)
    if btype == "date":
        # Must be datetime.datetime, never a bare datetime.date
        return isinstance(value, datetime.datetime)
    if btype == "array":
        return isinstance(value, list)
    if btype == "object":
        return isinstance(value, dict)
    raise ValueError(f"Unsupported bsonType {btype!r} in schema")


def _to_decimal_number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal128):
        dec = value.to_decimal()
        return None if dec.is_nan() else dec
    if isinstance(value, Decimal):
        return None if value.is_nan() else value
    if isinstance(value, (int, Int64)):
        return Decimal(int(value))
    if isinstance(value, float):
        dec = Decimal(str(value))
        return None if dec.is_nan() else dec
    return None


def _enum_contains(enum_values: list[Any], candidate: Any) -> bool:
    for item in enum_values:
        if isinstance(item, bool) or isinstance(candidate, bool):
            if type(item) is type(candidate) and item == candidate:
                return True
        elif item == candidate:
            return True
    return False


def validate_bson_document(schema: dict[str, Any], doc: Any, path: str = "") -> None:
    """Strictly validate `doc` (or nested field value) against a MongoDB `$jsonSchema`."""
    spec = unwrap_json_schema(schema)
    loc = path or "<root>"

    if "bsonType" in spec:
        btype = spec["bsonType"]
        allowed_types = btype if isinstance(btype, list) else [btype]
        if not any(_matches_single_bson_type(t, doc) for t in allowed_types):
            raise BsonSchemaValidationError(
                f"{loc}: expected bsonType {btype!r}, got {type(doc).__name__} ({doc!r})"
            )

    if "enum" in spec:
        if not _enum_contains(spec["enum"], doc):
            raise BsonSchemaValidationError(
                f"{loc}: value {doc!r} not in enum {spec['enum']!r}"
            )

    num = _to_decimal_number(doc)
    if num is not None:
        if "minimum" in spec:
            bound = Decimal(str(spec["minimum"]))
            if spec.get("exclusiveMinimum") is True:
                if not (num > bound):
                    raise BsonSchemaValidationError(
                        f"{loc}: {num} must be strictly > minimum {bound}"
                    )
            else:
                if not (num >= bound):
                    raise BsonSchemaValidationError(
                        f"{loc}: {num} is less than minimum {bound}"
                    )
        if "exclusiveMinimum" in spec and not isinstance(spec["exclusiveMinimum"], bool):
            bound = Decimal(str(spec["exclusiveMinimum"]))
            if not (num > bound):
                raise BsonSchemaValidationError(
                    f"{loc}: {num} must be strictly > exclusiveMinimum {bound}"
                )
        if "maximum" in spec:
            bound = Decimal(str(spec["maximum"]))
            if spec.get("exclusiveMaximum") is True:
                if not (num < bound):
                    raise BsonSchemaValidationError(
                        f"{loc}: {num} must be strictly < maximum {bound}"
                    )
            else:
                if not (num <= bound):
                    raise BsonSchemaValidationError(
                        f"{loc}: {num} exceeds maximum {bound}"
                    )
        if "exclusiveMaximum" in spec and not isinstance(spec["exclusiveMaximum"], bool):
            bound = Decimal(str(spec["exclusiveMaximum"]))
            if not (num < bound):
                raise BsonSchemaValidationError(
                    f"{loc}: {num} must be strictly < exclusiveMaximum {bound}"
                )
    elif isinstance(doc, Decimal128) and (
        "minimum" in spec or "maximum" in spec or "exclusiveMinimum" in spec or "exclusiveMaximum" in spec
    ):
        raise BsonSchemaValidationError(f"{loc}: NaN Decimal128 violates numeric bounds")

    if isinstance(doc, str):
        if "minLength" in spec and len(doc) < int(spec["minLength"]):
            raise BsonSchemaValidationError(
                f"{loc}: string length {len(doc)} < minLength {spec['minLength']} ({doc!r})"
            )
        if "maxLength" in spec and len(doc) > int(spec["maxLength"]):
            raise BsonSchemaValidationError(
                f"{loc}: string length {len(doc)} > maxLength {spec['maxLength']}"
            )
        if "pattern" in spec and re.search(spec["pattern"], doc) is None:
            raise BsonSchemaValidationError(
                f"{loc}: {doc!r} does not match pattern {spec['pattern']!r}"
            )

    if isinstance(doc, list):
        if "minItems" in spec and len(doc) < int(spec["minItems"]):
            raise BsonSchemaValidationError(
                f"{loc}: array length {len(doc)} < minItems {spec['minItems']}"
            )
        if "maxItems" in spec and len(doc) > int(spec["maxItems"]):
            raise BsonSchemaValidationError(
                f"{loc}: array length {len(doc)} > maxItems {spec['maxItems']}"
            )
        if spec.get("uniqueItems") is True:
            seen: list[Any] = []
            for idx, elem in enumerate(doc):
                if any(type(prev) is type(elem) and prev == elem for prev in seen):
                    raise BsonSchemaValidationError(
                        f"{loc}[{idx}]: duplicate item {elem!r} violates uniqueItems"
                    )
                seen.append(elem)
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for idx, elem in enumerate(doc):
                validate_bson_document(item_spec, elem, f"{loc}[{idx}]")

    if isinstance(doc, dict):
        required = spec.get("required") or []
        missing = [req for req in required if req not in doc]
        if missing:
            raise BsonSchemaValidationError(
                f"{loc}: missing required fields {sorted(missing)}"
            )
        props = spec.get("properties") or {}
        addl = spec.get("additionalProperties", True)
        if addl is False:
            extra = [k for k in doc if k not in props]
            if extra:
                raise BsonSchemaValidationError(
                    f"{loc}: forbidden additionalProperties {sorted(extra)}"
                )
        for key, val in doc.items():
            child_path = f"{path}.{key}" if path else key
            if key in props:
                validate_bson_document(props[key], val, child_path)
            elif isinstance(addl, dict):
                validate_bson_document(addl, val, child_path)


def assert_valid_collection_doc(collection_name: str, doc: dict[str, Any]) -> None:
    schema = get_collection_schema(collection_name)
    validate_bson_document(schema, doc, path=collection_name)


def _extract_path_values(document: Any, parts: list[str]) -> tuple[list[Any], bool]:
    """Return `(values, field_exists)` supporting dotted paths and arrays of subdocuments."""
    if not parts:
        return [document], True
    head, tail = parts[0], parts[1:]
    if isinstance(document, list):
        collected: list[Any] = []
        any_exists = False
        for elem in document:
            vals, exists = _extract_path_values(elem, parts)
            if exists:
                any_exists = True
                collected.extend(vals)
        return collected, any_exists
    if not isinstance(document, dict):
        return [], False
    if head not in document:
        return [], False
    return _extract_path_values(document[head], tail)


def _apply_projection(doc: dict[str, Any], projection: dict[str, Any] | None) -> dict[str, Any]:
    if not projection:
        return doc
    include_keys = [k for k, v in projection.items() if k != "_id" and v]
    if not include_keys:
        out = dict(doc)
        for k, v in projection.items():
            if not v:
                out.pop(k, None)
        return out
    out: dict[str, Any] = {}
    if "_id" in doc and projection.get("_id", 1) != 0:
        out["_id"] = doc["_id"]
    for key in include_keys:
        if "." in key:
            top, sub = key.split(".", 1)
            if top in doc:
                top_val = doc[top]
                if isinstance(top_val, list):
                    out[top] = [
                        {sub: elem[sub]} if isinstance(elem, dict) and sub in elem else {}
                        for elem in top_val
                    ]
                elif isinstance(top_val, dict) and sub in top_val:
                    out.setdefault(top, {})[sub] = top_val[sub]
        elif key in doc:
            out[key] = copy.deepcopy(doc[key])
    return out


def _set_dotted(target: dict[str, Any], key: str, value: Any) -> None:
    if "." not in key:
        target[key] = value
        return
    parts = key.split(".")
    cur = target
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _unset_dotted(target: dict[str, Any], key: str) -> None:
    if "." not in key:
        target.pop(key, None)
        return
    parts = key.split(".")
    cur = target
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            return
        cur = nxt
    cur.pop(parts[-1], None)


def _apply_update_operators(doc: dict[str, Any], update: dict[str, Any], *, is_insert: bool = False) -> None:
    supported = {"$set", "$unset", "$inc", "$setOnInsert", "$push", "$pull", "$addToSet"}
    unknown = set(update) - supported
    if unknown:
        raise NotImplementedError(f"Unsupported MongoDB update operators: {sorted(unknown)}")
    if is_insert and "$setOnInsert" in update:
        for k, v in update["$setOnInsert"].items():
            _set_dotted(doc, k, copy.deepcopy(v))
    if "$set" in update:
        for k, v in update["$set"].items():
            _set_dotted(doc, k, copy.deepcopy(v))
    if "$unset" in update:
        for k in update["$unset"]:
            _unset_dotted(doc, k)
    if "$inc" in update:
        for k, v in update["$inc"].items():
            doc[k] = doc.get(k, 0) + v
    if "$push" in update:
        for k, v in update["$push"].items():
            arr = doc.setdefault(k, [])
            if isinstance(v, dict) and "$each" in v:
                arr.extend(copy.deepcopy(v["$each"]))
            else:
                arr.append(copy.deepcopy(v))
    if "$addToSet" in update:
        for k, v in update["$addToSet"].items():
            arr = doc.setdefault(k, [])
            items = v["$each"] if isinstance(v, dict) and "$each" in v else [v]
            for elem in items:
                if elem not in arr:
                    arr.append(copy.deepcopy(elem))
    if "$pull" in update:
        for k, cond in update["$pull"].items():
            arr = doc.get(k)
            if isinstance(arr, list):
                if isinstance(cond, dict):
                    doc[k] = [
                        elem
                        for elem in arr
                        if not (isinstance(elem, dict) and all(elem.get(ck) == cv for ck, cv in cond.items()))
                    ]
                else:
                    doc[k] = [elem for elem in arr if elem != cond]


class ValidatingCursor(list):
    def sort(self, key_or_list: Any, direction: int | None = None) -> "ValidatingCursor":
        specs = [(key_or_list, direction or 1)] if isinstance(key_or_list, str) else list(key_or_list)
        items = list(self)
        for field, dir_val in reversed(specs):
            rev = dir_val < 0
            items.sort(key=lambda d: _sort_key(d.get(field)), reverse=rev)
        return ValidatingCursor(items)

    def skip(self, count: int) -> "ValidatingCursor":
        return ValidatingCursor(self[count:])

    def limit(self, count: int) -> "ValidatingCursor":
        if not count:
            return ValidatingCursor(self)
        return ValidatingCursor(self[:count])

    def batch_size(self, _size: int) -> "ValidatingCursor":
        return self


def _sort_key(val: Any) -> tuple[int, Any]:
    if val is None:
        return (0, "")
    if isinstance(val, datetime.datetime):
        ts = val.timestamp() if val.tzinfo else val.replace(tzinfo=datetime.timezone.utc).timestamp()
        return (2, ts)
    if isinstance(val, ObjectId):
        return (3, str(val))
    if isinstance(val, Decimal128):
        return (1, val.to_decimal())
    if isinstance(val, (int, float, Decimal)):
        return (1, Decimal(str(val)))
    return (4, str(val))


def matches_mongo_query(doc: dict[str, Any], query: dict[str, Any] | None) -> bool:
    if not query:
        return True
    for key, wanted in query.items():
        if key == "$or":
            if not any(matches_mongo_query(doc, branch) for branch in wanted):
                return False
            continue
        if key == "$and":
            if not all(matches_mongo_query(doc, branch) for branch in wanted):
                return False
            continue
        if key == "$nor":
            if any(matches_mongo_query(doc, branch) for branch in wanted):
                return False
            continue
        if key.startswith("$"):
            raise NotImplementedError(f"Unsupported top-level query operator: {key}")

        vals, exists = _extract_path_values(doc, key.split("."))
        effective_vals = vals if exists else [None]

        if isinstance(wanted, dict) and any(k.startswith("$") for k in wanted):
            supported_ops = {
                "$eq", "$ne", "$in", "$nin", "$gt", "$gte", "$lt", "$lte", "$exists", "$regex", "$options"
            }
            unknown_ops = set(wanted) - supported_ops
            if unknown_ops:
                raise NotImplementedError(f"Unsupported field operator(s) {sorted(unknown_ops)} on {key!r}")

            if "$exists" in wanted:
                want_exists = bool(wanted["$exists"])
                if exists != want_exists:
                    return False

            if "$ne" in wanted:
                target = wanted["$ne"]
                if exists and any(v == target or (isinstance(v, list) and target in v) for v in vals):
                    return False

            if "$nin" in wanted:
                disallowed = wanted["$nin"]
                if any(
                    v in disallowed or (isinstance(v, list) and any(elem in disallowed for elem in v))
                    for v in effective_vals
                ):
                    return False

            positive_ops = {k: v for k, v in wanted.items() if k in {"$eq", "$in", "$gt", "$gte", "$lt", "$lte", "$regex"}}
            if positive_ops:
                flags = re.IGNORECASE if "i" in str(wanted.get("$options", "")) else 0

                def _val_matches(v: Any) -> bool:
                    if isinstance(v, list) and "$eq" in positive_ops and positive_ops["$eq"] in v:
                        return True
                    for op, expected in positive_ops.items():
                        if op == "$eq":
                            if v != expected:
                                return False
                        elif op == "$in":
                            if v in expected:
                                continue
                            if isinstance(v, list) and any(elem in expected for elem in v):
                                continue
                            return False
                        elif op == "$gt":
                            if v is None or not (v > expected):
                                return False
                        elif op == "$gte":
                            if v is None or not (v >= expected):
                                return False
                        elif op == "$lt":
                            if v is None or not (v < expected):
                                return False
                        elif op == "$lte":
                            if v is None or not (v <= expected):
                                return False
                        elif op == "$regex":
                            pat = expected.pattern if isinstance(expected, re.Pattern) else str(expected)
                            if not isinstance(v, str) or re.search(pat, v, flags) is None:
                                return False
                    return True

                if not any(_val_matches(v) for v in effective_vals):
                    return False
        else:
            if wanted is None:
                if exists and not any(v is None for v in vals):
                    return False
            else:
                if not exists:
                    return False
                if not any(v == wanted or (isinstance(v, list) and wanted in v) for v in vals):
                    return False
    return True


class ValidatingCollection:
    """In-memory MongoDB collection fake that strictly enforces canonical `$jsonSchema` on writes."""

    def __init__(
        self,
        collection_name: str,
        docs: list[dict[str, Any]] | None = None,
        *,
        schema: dict[str, Any] | None = None,
        validate_initial: bool = False,
    ) -> None:
        self.collection_name = collection_name
        self.schema = unwrap_json_schema(schema) if schema is not None else get_collection_schema(collection_name)
        self.docs: list[dict[str, Any]] = list(docs or [])
        if validate_initial:
            for idx, d in enumerate(self.docs):
                validate_bson_document(self.schema, d, path=f"{collection_name}[{idx}]")

    def _raise_write_error(self, exc: BsonSchemaValidationError) -> None:
        raise WriteError(
            f"Document failed validation ({self.collection_name}): {exc}",
            121,
            {"code": 121, "errmsg": str(exc)},
        ) from exc

    def _validate_or_write_error(self, candidate: dict[str, Any]) -> None:
        try:
            validate_bson_document(self.schema, candidate, path=self.collection_name)
        except BsonSchemaValidationError as exc:
            self._raise_write_error(exc)

    def create_index(self, *_args: Any, **_kwargs: Any) -> str:
        return "idx_synthetic"

    def _matches(self, doc: dict[str, Any], query: dict[str, Any] | None) -> bool:
        return matches_mongo_query(doc, query)

    def find_one(
        self,
        query: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._matches(doc, query):
                return _apply_projection(doc, projection) if projection else doc
        return None

    def find(
        self,
        query: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> ValidatingCursor:
        matched = [doc for doc in self.docs if self._matches(doc, query)]
        if projection:
            matched = [_apply_projection(doc, projection) for doc in matched]
        return ValidatingCursor(matched)

    def count_documents(self, query: dict[str, Any] | None = None, **_kwargs: Any) -> int:
        return sum(1 for doc in self.docs if self._matches(doc, query))

    def distinct(self, field: str, query: dict[str, Any] | None = None, **_kwargs: Any) -> list[Any]:
        out: list[Any] = []
        for doc in self.find(query or {}):
            vals, exists = _extract_path_values(doc, field.split("."))
            if not exists:
                continue
            for v in vals:
                if isinstance(v, list):
                    for elem in v:
                        if elem not in out:
                            out.append(elem)
                elif v not in out:
                    out.append(v)
        return out

    def insert_one(self, doc: dict[str, Any], **_kwargs: Any) -> SimpleNamespace:
        item = dict(doc)
        item.setdefault("_id", ObjectId())
        if any(existing.get("_id") == item["_id"] for existing in self.docs):
            raise DuplicateKeyError(f"Duplicate _id {item['_id']!r} in {self.collection_name}")
        self._validate_or_write_error(item)
        doc.setdefault("_id", item["_id"])
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        target = next((d for d in self.docs if self._matches(d, query)), None)
        if target is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
            seed: dict[str, Any] = {}
            for k, v in (query or {}).items():
                if not k.startswith("$") and not (isinstance(v, dict) and any(op.startswith("$") for op in v)):
                    _set_dotted(seed, k, copy.deepcopy(v))
            seed.setdefault("_id", ObjectId())
            _apply_update_operators(seed, update, is_insert=True)
            self._validate_or_write_error(seed)
            self.docs.append(seed)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=seed["_id"])

        candidate = copy.deepcopy(target)
        _apply_update_operators(candidate, update, is_insert=False)
        self._validate_or_write_error(candidate)
        modified = 1 if candidate != target else 0
        target.clear()
        target.update(candidate)
        return SimpleNamespace(matched_count=1, modified_count=modified, upserted_id=None)

    def update_many(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        **_kwargs: Any,
    ) -> SimpleNamespace:
        targets = [d for d in self.docs if self._matches(d, query)]
        staged: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        for target in targets:
            candidate = copy.deepcopy(target)
            _apply_update_operators(candidate, update, is_insert=False)
            self._validate_or_write_error(candidate)
            staged.append((target, candidate, candidate != target))
        modified = 0
        for target, candidate, changed in staged:
            target.clear()
            target.update(candidate)
            if changed:
                modified += 1
        return SimpleNamespace(matched_count=len(targets), modified_count=modified)

    def delete_one(self, query: dict[str, Any], **_kwargs: Any) -> SimpleNamespace:
        target = next((d for d in self.docs if self._matches(d, query)), None)
        if target is None:
            return SimpleNamespace(deleted_count=0)
        self.docs.remove(target)
        return SimpleNamespace(deleted_count=1)
