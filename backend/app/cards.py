"""Card metadata and recoverable list deletion; invoice corrections are audited.

Imported card identities and purchases/payments are preserved. Metadata uses
namespaced documents in the existing account-profile store (no ledger schema migration).
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
import hashlib

from bson import ObjectId, json_util
from bson.decimal128 import Decimal128
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth import require_session
from app.atomic import mongo_snapshot, mongo_transaction, session_database
from app.financial_time import day_start, civil_date, today
from app.movement_insert_lock import lock_movement_insert
from app.mutation_identity import durable_mutation
from app.obligation_policy import bill_status

router = APIRouter(dependencies=[Depends(require_session)])


def database(request: Request):
    if request.app.state.db is None:
        raise HTTPException(503, "Banco indisponível.")
    return request.app.state.db


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id:UUID|None=None


class CardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID | None = None
    instituicao: str = Field(min_length=1, max_length=100)
    apelido: str = Field(default="", max_length=80)
    cartao_final: str = Field(default="", pattern=r"^(?:\d{4})?$")
    validade: str | None = Field(default=None, pattern=r"^[1-9]\d{3}-(0[1-9]|1[0-2])$")
    dia_vencimento: int | None = Field(default=None, ge=1, le=31, strict=True)
    dia_fechamento: int | None = Field(default=None, ge=1, le=31, strict=True)
    melhor_dia_compra: int | None = Field(default=None, ge=1, le=31, strict=True)
    observacoes: str | None = Field(default=None, max_length=1000)


class CardEdit(VersionIn):
    instituicao: str = Field(min_length=1, max_length=100)
    apelido: str = Field(default="", max_length=80)
    cartao_final: str = Field(default="", pattern=r"^(?:\d{4})?$")
    validade: str | None = Field(default=None, pattern=r"^[1-9]\d{3}-(0[1-9]|1[0-2])$")
    dia_vencimento: int | None = Field(default=None, ge=1, le=31, strict=True)
    dia_fechamento: int | None = Field(default=None, ge=1, le=31, strict=True)
    melhor_dia_compra: int | None = Field(default=None, ge=1, le=31, strict=True)
    observacoes: str | None = Field(default=None, max_length=1000)


class BillEdit(VersionIn):
    competencia: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    data_vencimento: date
    valor_total: Decimal = Field(ge=0, le=Decimal("999999999.99"), decimal_places=2, allow_inf_nan=False)
    observacoes: str = Field(default="", max_length=1000)


def identity(value):
    if not ObjectId.is_valid(value):
        raise HTTPException(404, "Cadastro não encontrado.")
    return ObjectId(value)


def token(*documents):
    return hashlib.sha256(json_util.dumps(documents, sort_keys=True).encode()).hexdigest()


def amount(value):
    return value.to_decimal() if isinstance(value, Decimal128) else Decimal(str(value or 0))


def metadata(db, kind, oid):
    return db.account_profiles.find_one({"_id": f"{kind}-profile:{oid}"}) or {}


def card_context(db, card_id):
    card = db.accounts.find_one({"_id": identity(card_id), "tipo": "cartao_credito"})
    if not card:
        raise HTTPException(404, "Cartão não encontrado.")
    return card, metadata(db, "card", card["_id"])


def card_item(card, meta):
    values = meta.get("values", {})
    return {"id": str(card["_id"]), "instituicao": values.get("instituicao", card.get("instituicao", "Cartão")),
            "apelido": values.get("apelido", ""), "cartao_final": values.get("cartao_final", card.get("cartao_final", "")),
            **{field: values.get(field) for field in ("validade", "dia_vencimento", "dia_fechamento", "melhor_dia_compra", "observacoes")},
            "deleted": bool(meta.get("deleted")), "token": token(card, meta)}


def bill_context(db, bill_id):
    bill = db.credit_card_bills.find_one({"_id": identity(bill_id)})
    if not bill:
        raise HTTPException(404, "Fatura não encontrada.")
    obligations = list(db.obligations.find({"tipo": "fatura_cartao", "origem_id": bill["_id"]}).sort("_id", 1))
    return bill, metadata(db, "bill", bill["_id"]), obligations


def bill_item(bill, meta, obligations):
    paid = amount(bill.get("valor_pago", bill.get("pagamentos")))
    return {"id": str(bill["_id"]), "card_id": str(bill["card_account_id"]), "competencia": bill["competencia"],
            "data_vencimento": civil_date(bill["data_vencimento"]).isoformat(),
            "valor_total": str(amount(bill["valor_total"])), "valor_pago": str(paid),
            "saldo_aberto": str(amount(bill.get("saldo_aberto", max(Decimal(0), amount(bill["valor_total"]) - paid)))),
            "status": bill["status"], "observacoes": meta.get("values", {}).get("observacoes", ""),
            "deleted": bool(meta.get("deleted")), "token": token(bill, meta, obligations)}


def check_version(body, current):
    if body.expected_token != current:
        raise HTTPException(409, "Este cadastro mudou. Atualize a lista e reabra a edição.")


def store(db, kind, oid, before, *, values=None, deleted=None):
    now = datetime.now(timezone.utc)
    key = f"{kind}-profile:{oid}"
    old = metadata(db, kind, oid)
    fields = {"key": key, "entity_type": kind, "updated_at": now, "revision": old.get("revision", 0) + 1}
    if values is not None:
        fields["values"] = values
    if deleted is not None:
        fields["deleted"] = deleted
    db.account_profiles.update_one({"_id": key}, {"$set": fields}, upsert=True)
    db.audit_log.insert_one({"event": f"{kind}_profile_changed", "entity_id": oid,
                             "before": before, "after": fields, "created_at": now})


@router.get("/v1/card-catalog")
def catalog(include_deleted: bool = False, db=Depends(database)):
    with mongo_snapshot(db) as (reading, _):
        metas = {row["_id"]: row for row in reading.account_profiles.find({"_id": {"$regex": "^(card|bill)-profile:"}})}
        counts = {}
        for bill in reading.credit_card_bills.find({}, {"card_account_id": 1}):
            if not metas.get(f'bill-profile:{bill["_id"]}', {}).get("deleted"):
                key = str(bill["card_account_id"])
                counts[key] = counts.get(key, 0) + 1
        items = [card_item(card, metas.get(f'card-profile:{card["_id"]}', {}))
                 for card in reading.accounts.find({"tipo": "cartao_credito"})]
        for item in items:
            item["bill_count"] = counts.get(item["id"], 0)
        return {"items": sorted((item for item in items if include_deleted or not item["deleted"]),
                                key=lambda item: (item["apelido"] or item["instituicao"]).casefold())}


@router.get("/v1/card-catalog/{card_id}/bills")
def bills(card_id: str, include_deleted: bool = False, db=Depends(database)):
    with mongo_snapshot(db) as (reading, _):
        card, meta = card_context(reading, card_id)
        rows = list(reading.credit_card_bills.find({"card_account_id": card["_id"]}).sort("data_vencimento", -1))
        metas = {row["_id"]: row for row in reading.account_profiles.find({"_id": {"$in": [f'bill-profile:{row["_id"]}' for row in rows]}})}
        linked = {}
        for row in reading.obligations.find({"tipo": "fatura_cartao", "origem_id": {"$in": [row["_id"] for row in rows]}}).sort("_id", 1):
            linked.setdefault(row["origem_id"], []).append(row)
        items = [bill_item(row, metas.get(f'bill-profile:{row["_id"]}', {}), linked.get(row["_id"], [])) for row in rows]
        return {"card": card_item(card, meta), "items": [item for item in items if include_deleted or not item["deleted"]]}


@router.post("/v1/card-catalog", status_code=201)
@durable_mutation('card_create', ())
def create_card(body: CardCreate, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        lock_movement_insert(writing)
        now = datetime.now(timezone.utc)
        card_doc = {
            "_id": ObjectId(),
            "tipo": "cartao_credito",
            "instituicao": body.instituicao,
            "cartao_final": body.cartao_final,
            "status": "ativa",
            "source": "mobile_manual",
            "created_at": now,
            "updated_at": now,
        }
        writing.accounts.insert_one(card_doc)
        values = body.model_dump(exclude={"request_id"}, exclude_unset=True)
        store(writing, "card", card_doc["_id"], None, values=values, deleted=False)
        item = card_item(card_doc, metadata(writing, "card", card_doc["_id"]))
        item["bill_count"] = 0
        return {"item": item}


@router.patch("/v1/card-catalog/{card_id}")
@durable_mutation('card_edit',('card_id',))
def edit_card(card_id: str, body: CardEdit, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        lock_movement_insert(writing)
        card, meta = card_context(writing, card_id)
        check_version(body, token(card, meta))
        if meta.get("deleted"):
            raise HTTPException(409, "Restaure o cartão antes de editar.")
        # Older clients omit the new optional fields. Preserve them; explicit
        # null clears them. Card settings never rewrite individual invoices.
        values = {**meta.get("values", {}), **body.model_dump(exclude={"expected_token","request_id"}, exclude_unset=True)}
        store(writing, "card", card["_id"], card_item(card, meta), values=values)
        return {"item": card_item(card, metadata(writing, "card", card["_id"]))}


@router.patch("/v1/card-bills/{bill_id}")
@durable_mutation('bill_edit',('bill_id',))
def edit_bill(bill_id: str, body: BillEdit, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        lock_movement_insert(writing)
        bill, meta, obligations = bill_context(writing, bill_id)
        card, card_meta = card_context(writing, str(bill["card_account_id"]))
        check_version(body, token(bill, meta, obligations))
        if meta.get("deleted") or card_meta.get("deleted"):
            raise HTTPException(409, "Restaure o cartão e a fatura antes de editar.")
        if body.competencia != bill["competencia"] and writing.credit_card_bills.find_one({
                "card_account_id": card["_id"], "competencia": body.competencia, "_id": {"$ne": bill["_id"]}}):
            raise HTTPException(409, "Este cartão já possui uma fatura nesta competência.")
        paid = amount(bill.get("valor_pago", bill.get("pagamentos")))
        delta = body.valor_total - amount(bill["valor_total"])
        if body.valor_total < paid:
            raise HTTPException(422, "O total não pode ser menor que os pagamentos já registrados.")
        if len(obligations) != 1:
            raise HTTPException(409, "Esta fatura precisa ter uma única obrigação vinculada antes de ser corrigida.")
        obligation = obligations[0]
        if (amount(obligation.get("valor_pago")) != paid
            or amount(obligation.get("valor_original")) != amount(bill["valor_total"])
            or bill.get("saldo_aberto") is None or obligation.get("saldo_aberto") is None
            or amount(bill["saldo_aberto"]) != amount(bill["valor_total"]) - paid
            or amount(obligation["saldo_aberto"]) != amount(bill["saldo_aberto"])):
            raise HTTPException(409, "Fatura e obrigação divergem. Concilie os valores antes de editar.")
        if (delta == 0 and body.competencia == bill["competencia"]
            and body.data_vencimento == civil_date(bill["data_vencimento"])):
            store(writing, "bill", bill["_id"], {"bill": bill, "obligations": obligations, "metadata": meta},
                  values={"observacoes": body.observacoes})
            return {"item": bill_item(bill, metadata(writing, "bill", bill["_id"]), obligations)}
        if amount(bill.get("novos_lancamentos")) + delta < 0 or amount(obligation.get("principal")) + delta < 0:
            raise HTTPException(422, "O total deve cobrir o saldo anterior e os encargos já registrados.")
        balance = body.valor_total - paid
        status = bill_status(balance, paid, body.data_vencimento, today())
        now = datetime.now(timezone.utc)
        fields = {"competencia": body.competencia, "data_vencimento": day_start(body.data_vencimento),
                  "saldo_aberto": Decimal128(balance), "status": status, "updated_at": now}
        writing.credit_card_bills.update_one({"_id": bill["_id"]}, {"$set": {**fields, "valor_total": Decimal128(body.valor_total),
            "novos_lancamentos": Decimal128(amount(bill.get("novos_lancamentos")) + delta)}})
        update = {"$set": {**fields, "valor_original": Decimal128(body.valor_total),
                           "principal": Decimal128(amount(obligation.get("principal")) + delta),
                           "dias_atraso": max(0, (today() - body.data_vencimento).days) if balance else 0}}
        if balance:
            update["$unset"] = {"data_liquidacao": ""}
        writing.obligations.update_one({"_id": obligation["_id"]}, update)
        store(writing, "bill", bill["_id"], {"bill": bill, "obligations": obligations, "metadata": meta},
              values={"observacoes": body.observacoes})
        updated, updated_meta, linked = bill_context(writing, bill_id)
        writing.audit_log.insert_one({"event": "card_bill_corrected", "entity_id": bill["_id"],
            "before": {"bill": bill, "obligations": obligations}, "after": {"bill": updated, "obligations": linked}, "created_at": now})
        return {"item": bill_item(updated, updated_meta, linked)}


def visibility(db, kind, entity_id, body, deleted):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        lock_movement_insert(writing)
        if kind == "card":
            row, meta = card_context(writing, entity_id)
            current = token(row, meta)
        else:
            row, meta, obligations = bill_context(writing, entity_id)
            current = token(row, meta, obligations)
        check_version(body, current)
        store(writing, kind, row["_id"], meta, deleted=deleted)
        return {"deleted": deleted, "history_preserved": True}


@router.delete("/v1/card-catalog/{card_id}")
@durable_mutation('card_delete',('card_id',))
def delete_card(card_id: str, body: VersionIn, db=Depends(database)):
    return visibility(db, "card", card_id, body, True)


@router.post("/v1/card-catalog/{card_id}/restore")
@durable_mutation('card_restore',('card_id',))
def restore_card(card_id: str, body: VersionIn, db=Depends(database)):
    return visibility(db, "card", card_id, body, False)


@router.delete("/v1/card-bills/{bill_id}")
@durable_mutation('bill_delete',('bill_id',))
def delete_bill(bill_id: str, body: VersionIn, db=Depends(database)):
    return visibility(db, "bill", bill_id, body, True)


@router.post("/v1/card-bills/{bill_id}/restore")
@durable_mutation('bill_restore',('bill_id',))
def restore_bill(bill_id: str, body: VersionIn, db=Depends(database)):
    return visibility(db, "bill", bill_id, body, False)
