"""Editable account metadata, additive to the imported ledger.

Profiles live in a separate collection; the strict imported account schema remains intact.
Their stable keys survive renames, closing and archiving. Deleted empty profiles
retain a tombstone so a built-in account never reappears on the next read.
"""
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from app.atomic import with_batch_size

ACCOUNT_CATALOG = (
    ("itau", "Itaú", "conta_corrente", "BRL"),
    ("santander", "Santander", "conta_corrente", "BRL"),
    ("inter", "Inter", "conta_corrente", "BRL"),
    ("btg", "BTG Pactual", "conta_corrente", "BRL"),
    ("mercado_pago", "Mercado Pago", "carteira", "BRL"),
    ("binance", "Binance", "criptoativos", "BRL"),
)
ACCOUNT_KEYS = {row[0] for row in ACCOUNT_CATALOG}


class AccountProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    apelido: str = Field(default="", max_length=80)
    instituicao: str = Field(min_length=1, max_length=100)
    tipo: Literal["conta_corrente", "poupanca", "pagamento", "investimento", "carteira", "criptoativos", "outra"]
    agencia: str = Field(default="", max_length=20)
    agencia_digito: str = Field(default="", max_length=5)
    numero: str = Field(default="", max_length=30)
    numero_digito: str = Field(default="", max_length=5)
    status: Literal["aberta", "encerrada"] = "aberta"
    moeda: Literal["BRL"] = "BRL"
    data_abertura: date | None = None
    data_encerramento: date | None = None
    observacoes: str = Field(default="", max_length=1000)
    archived: bool = False

    @model_validator(mode="after")
    def check_dates(self):
        if self.status == "aberta" and self.data_encerramento:
            raise ValueError("Uma conta aberta não pode ter data de encerramento.")
        if self.data_abertura and self.data_encerramento and self.data_encerramento < self.data_abertura:
            raise ValueError("O encerramento deve ser posterior à abertura.")
        if self.numero_digito and not self.numero:
            raise ValueError("Informe o número da conta antes do dígito.")
        if self.agencia_digito and not self.agencia:
            raise ValueError("Informe a agência antes do dígito.")
        return self


class AccountProfileCreate(AccountProfileIn):
    request_id: UUID


class AccountProfileUpdate(AccountProfileIn):
    request_id: UUID | None = None
    expected_revision: int = Field(ge=1)


class AccountProfileDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    request_id: UUID | None = None


def profile_document_id(key):
    return "account-profile:" + key


def account_profiles(db, profiles=None, *, stored_rows=None):
    if profiles is not None:
        return list(profiles)
    cached = getattr(db, "__dict__", {}).get("_account_profiles_cache") if (db is not None and stored_rows is None) else None
    if isinstance(cached, list):
        return list(cached)
    defaults = {key: {"key": key, "instituicao": institution, "apelido": "", "tipo": kind,
        "moeda": currency, "status": "aberta", "archived": False, "revision": 1,
        "agencia": "", "agencia_digito": "", "numero": "", "numero_digito": "",
        "data_abertura": None, "data_encerramento": None, "observacoes": ""}
        for key, institution, kind, currency in ACCOUNT_CATALOG}
    source_rows = stored_rows if stored_rows is not None else with_batch_size(db.account_profiles.find({}))
    for stored in source_rows:
        if stored.get("entity_type") in {"card", "bill", "month_close"}:
            continue
        key = stored["key"]
        if stored.get("deleted"):
            defaults.pop(key, None)
        else:
            defaults[key] = {**defaults.get(key, {}), **stored["profile"], "key": key,
                "revision": stored["revision"]}
    return list(defaults.values())


def validate_account_key(key, db=None, *, selectable=False, profiles=None):
    resolved_profiles = (
        account_profiles(db, profiles=profiles)
        if (db is not None or profiles is not None)
        else [{"key": k} for k in ACCOUNT_KEYS]
    )
    profile = next((item for item in resolved_profiles if item["key"] == key), None)
    if profile is None:
        raise HTTPException(422, "Selecione uma conta cadastrada.")
    if selectable and (profile.get("archived") or profile.get("status") == "encerrada"):
        raise HTTPException(422, "Selecione uma conta aberta e não arquivada para uma nova movimentação.")
    return profile


def legacy_accounts_by_profile(db, rows=None):
    from app.account_positions import account_key_for_legacy
    cached = getattr(db, "__dict__", {}).get("_legacy_accounts_by_profile_cache") if (rows is None and db is not None) else None
    if isinstance(cached, dict):
        return dict(cached)
    grouped = {}
    source_rows = rows if rows is not None else with_batch_size(db.accounts.find({"tipo": "conta_corrente"}))
    for row in source_rows:
        key = account_key_for_legacy(row)
        if key:
            grouped.setdefault(key, []).append(row)
    # Ambiguity must never assign two accounts in a bank to the same balance.
    return {key: group[0] for key, group in grouped.items() if len(group) == 1}


def profile_for_legacy(db, account, *, profiles=None, legacy_by_profile=None):
    from app.account_positions import account_key_for_legacy
    key = account_key_for_legacy(account) if account else None
    by_profile = legacy_by_profile if legacy_by_profile is not None else legacy_accounts_by_profile(db)
    matched = by_profile.get(key)
    resolved_profiles = account_profiles(db, profiles=profiles)
    return key if matched and matched["_id"] == account["_id"] and any(p["key"] == key for p in resolved_profiles) else None


def movement_account_context(db, reference):
    from bson import ObjectId
    if reference.startswith("profile:"):
        key = reference.removeprefix("profile:")
        validate_account_key(key, db)
        legacy = legacy_accounts_by_profile(db).get(key)
        return (legacy["_id"] if legacy else None), key
    if ObjectId.is_valid(reference):
        identity = ObjectId(reference)
        account = db.accounts.find_one({"_id": identity})
        return identity, profile_for_legacy(db, account)
    return None, None


def effective_capture_account_key(capture, legacy_by_key):
    """Explicit human assignment wins; original statement identity is provenance."""
    if capture.get('natureza') == 'TRANSFERENCIA' or capture.get('transfer'):
        return None
    if capture.get('account_profile_id'):
        return capture['account_profile_id']
    identity = capture.get('account_id')
    return next((key for key, row in legacy_by_key.items() if identity == row['_id']), None)


def capture_account_query(identity, key):
    branches = []
    if key:
        branches.extend([{'account_profile_id': key, 'transfer': {'$in': [None, {}]}}, {'transfer.from_account_key': key},
                         {'transfer.to_account_key': key}])
    if identity is not None:
        branches.append({'account_id': identity, 'account_profile_id': {'$in': [None, '']}, 'transfer': {'$in': [None, {}]},
                         'natureza': {'$ne': 'TRANSFERENCIA'}})
    return {'$or': branches or [{'account_profile_id': '__invalid__'}]}


def account_usage(db, key):
    from app.account_positions import account_key_for_legacy
    legacy = [row for row in with_batch_size(db.accounts.find({"tipo": "conta_corrente"})) if account_key_for_legacy(row) == key]
    references = ["profile:" + key]
    for row in legacy:
        references.extend([row['_id'], str(row['_id'])])
    checks = {
        # A completed/cancelled schedule and its estimation rule are history too.
        "compromissos": db.commitments.count_documents({"account_id": {"$in": references}}),
        "regras_de_valor": db.commitment_amount_rules.count_documents({"account_id": {"$in": references}}),
        "saldos": db.account_balance_snapshots.count_documents({"account_key": key}),
        "capturas": db.provisional_transactions.count_documents({"$or": [
            {"account_profile_id": key}, {"transfer.from_account_key": key}, {"transfer.to_account_key": key}]}),
        "eventos": db.raw_events.count_documents({"$or": [
            {"parsed_payload.account_profile_id": key}, {"parsed_payload.from_account_key": key},
            {"parsed_payload.to_account_key": key}]}),
        "lotes": db.capture_batches.count_documents({"account_ref": "profile:" + key}),
        "movimentos_removidos": db.deleted_movements.count_documents({"$or": [
            {"document.account_profile_id": key}, {"document.transfer.from_account_key": key},
            {"document.transfer.to_account_key": key}]}),
        "experimentos": db.saving_experiments.count_documents({"account_key": key}),
    }
    identity = "account:" + key
    monthly = 0
    for state in with_batch_size(db.account_profiles.find({"entity_type": "month_close"})):
        snapshot = state.get("closed_snapshot") or {}
        if (identity in state.get("balances", {})
            or any(item.startswith(identity + ":") for item in state.get("items", {}))
            or any(item.get("group_id") == identity for item in state.get("custom", []))
            or any(group.get("id") == identity for group in snapshot.get("groups", []))):
            monthly += 1
    checks["fechamentos_mensais"] = monthly
    if legacy:
        # The imported identity itself is a historical link; do not remove it.
        checks["cadastro_historico"] = len(legacy)
    return {name: count for name, count in checks.items() if count}


def _stored_values(body):
    values = body.model_dump(exclude={"request_id", "expected_revision"})
    for field in ("data_abertura", "data_encerramento"):
        if values[field]:
            values[field] = values[field].isoformat()
    return values


def _clear_profile_cache(db):
    if db is not None and "_account_profiles_cache" in getattr(db, "__dict__", {}):
        db.__dict__.pop("_account_profiles_cache", None)


def create_profile(db, body):
    from app.movement_insert_lock import lock_movement_insert
    lock_movement_insert(db)
    _clear_profile_cache(db)
    key = "account_" + body.request_id.hex
    identity = profile_document_id(key)
    previous = db.account_profiles.find_one({"_id": identity})
    values = _stored_values(body)
    if previous:
        if previous.get("creation_values") != values or previous.get("deleted"):
            raise HTTPException(409, "Este pedido já foi utilizado. Reabra o cadastro para criar outra conta.")
        return validate_account_key(key, db)
    now = datetime.now(timezone.utc)
    db.account_profiles.insert_one({"_id": identity, "key": key,
        "profile": values, "creation_values": values, "revision": 1, "created_at": now, "updated_at": now})
    db.audit_log.insert_one({"event": "account_profile_created", "account_key": key, "after": values, "created_at": now})
    return validate_account_key(key, db)


def update_profile(db, key, body):
    from app.movement_insert_lock import lock_movement_insert
    lock_movement_insert(db)
    _clear_profile_cache(db)
    before = validate_account_key(key, db)
    if before["revision"] != body.expected_revision:
        raise HTTPException(409, "A conta mudou. Reabra os detalhes antes de salvar.")
    values = _stored_values(body)
    identity = profile_document_id(key)
    now = datetime.now(timezone.utc)
    stored = db.account_profiles.find_one({"_id": identity})
    if stored:
        result = db.account_profiles.update_one({"_id": identity, "revision": body.expected_revision},
            {"$set": {"profile": values, "updated_at": now}, "$inc": {"revision": 1}})
        if result is not None and result.matched_count != 1:
            raise HTTPException(409, "A conta mudou. Reabra os detalhes antes de salvar.")
    else:
        db.account_profiles.insert_one({"_id": identity, "key": key,
            "profile": values, "revision": 2, "created_at": now, "updated_at": now})
    db.audit_log.insert_one({"event": "account_profile_updated", "account_key": key,
        "before": before, "after": values, "created_at": now})
    return validate_account_key(key, db)


def delete_profile(db, key, body):
    from app.movement_insert_lock import lock_movement_insert
    lock_movement_insert(db)
    _clear_profile_cache(db)
    before = validate_account_key(key, db)
    if before["revision"] != body.expected_revision:
        raise HTTPException(409, "A conta mudou. Reabra os detalhes antes de excluir.")
    usage = account_usage(db, key)
    if usage:
        raise HTTPException(409, "Esta conta tem histórico ou vínculos. Arquive para preservá-los.")
    identity = profile_document_id(key)
    now = datetime.now(timezone.utc)
    tombstone = {"deleted": True, "revision": before["revision"] + 1, "updated_at": now}
    if db.account_profiles.find_one({"_id": identity}):
        result = db.account_profiles.update_one({"_id": identity, "revision": before["revision"]},
            {"$set": tombstone, "$unset": {"profile": "", "creation_values": ""}})
        if result is not None and result.matched_count != 1:
            raise HTTPException(409, "A conta mudou. Reabra os detalhes antes de excluir.")
    else:
        db.account_profiles.insert_one({"_id": identity, "key": key, **tombstone})
    db.audit_log.insert_one({"event": "account_profile_deleted", "account_key": key, "before": before, "created_at": now})
    return {"deleted": True, "key": key}
