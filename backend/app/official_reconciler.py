"""Reconciliation engine for official bank statements and credit card invoices.

Matches official statement rows against existing MongoDB records (`transactions`
and `provisional_transactions`), accounting for popular establishment names,
acquirer prefixes, weekend settlement lags, and PgConta/PIX card financing fees.
Preserves user-chosen popular names and validated categories, obeys strict
collection `$jsonSchema` validators (`transactions`, `accounts`), and records
idempotent links in `official_statement_reconciliations`.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from bson import ObjectId
from bson.decimal128 import Decimal128

from app.account_profiles import legacy_accounts_by_profile, profile_for_legacy
from app.atomic import AtomicUnavailable, _SessionDatabase, mongo_transaction, session_database
from app.economic_policy import duplicate_of_ledger
from app.financial_time import LOCAL_ZONE, day_start, local_date
from app.movement_insert_lock import lock_movement_insert
from app.obligation_policy import bill_status
from app.official_statement_parsers import (
    OfficialStatementDocument,
    OfficialStatementItem,
    _parse_installment_suffix,
    deduplicate_overlapping_bank_statements,
)
from app.platforms import platform_identity
from app.popular_merchants import brand_keys_for_text, normalize_merchant_text, popular_alias_match

_NOISE_WORDS = {
    "PIX", "PAGAMENTO", "PAG", "PGTO", "COMPRA", "DEBITO", "CREDITO", "CARTAO",
    "TRANSFERENCIA", "TRANSF", "TED", "DOC", "ENVIADO", "RECEBIDO", "BOLETO",
    "QRS", "MC", "DEB", "AUT", "DE", "DA", "DO", "EM", "NO", "NA", "LTDA", "SA",
    "BELO", "HORIZONTE", "HORIZONT", "CONTAGEM", "BETIM", "OSASCO", "BARUERI", "PAULO",
}


def _dec(val: Any) -> Decimal:
    if isinstance(val, Decimal128):
        return val.to_decimal()
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val or "0"))


def _aware_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _informative_tokens(text: str) -> set[str]:
    norm = normalize_merchant_text(text)
    return {w for w in norm.split() if len(w) >= 2 and not w.isdigit() and w not in _NOISE_WORDS}


def deterministic_ids(seed_key: str) -> tuple[str, str]:
    event_id = str(uuid5(NAMESPACE_URL, f"official-comprovante:{seed_key}"))
    provisional_id = str(uuid5(NAMESPACE_URL, f"provisional:{event_id}"))
    return event_id, provisional_id


def _get_collection_schema(db: Any, collection_name: str) -> dict[str, Any] | None:
    """Fetch live MongoDB $jsonSchema validator for `collection_name` without triggering Collection.__getattr__."""
    raw_db = db.db if isinstance(db, _SessionDatabase) else db
    if not hasattr(raw_db, "list_collections"):
        return None
    try:
        info = next(raw_db.list_collections(filter={"name": collection_name}), None)
    except Exception:
        return None
    schema = (((info or {}).get("options") or {}).get("validator") or {}).get("$jsonSchema")
    return schema if isinstance(schema, dict) else None


_MIN_COMPETITION_MARGIN = 15

_CANONICAL_TX_PROPS = frozenset(
    {
        "_id",
        "external_key",
        "schema_version",
        "created_at",
        "updated_at",
        "status_revisao",
        "editado_em",
        "revisado_em",
        "account_id",
        "data_evento",
        "data_lancamento",
        "competencia",
        "valor",
        "moeda",
        "fluxo",
        "tipo",
        "natureza",
        "descricao_original",
        "estabelecimento_original",
        "merchant_id",
        "platform_id",
        "merchant_location_id",
        "operator_entity_id",
        "categoria",
        "subcategoria",
        "localidade",
        "localidade_fornecedor",
        "localidade_descritor",
        "localidade_contexto",
        "localidade_real_validada",
        "origem_localidade",
        "metodo_pagamento",
        "bill_id",
        "installment",
        "foreign_amount",
        "balance_after",
        "purchase_channel",
        "purchase_origin",
        "detalhe",
        "recorrencia",
        "classification",
        "source",
        "status_conciliacao",
    }
)


@contextmanager
def _atomic_unit(db: Any, *, allow_standalone: bool = False):
    """Execute a short write unit inside a MongoDB transaction holding `lock_movement_insert`."""
    if isinstance(db, _SessionDatabase):
        lock_movement_insert(db)
        yield db
        return
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            lock_movement_insert(writing)
            yield writing
    except AtomicUnavailable:
        if allow_standalone:
            yield db
        else:
            raise


def _translate_status_for_schema(val: str, enums: list[Any] | None, *, label: str = "status") -> str:
    if not isinstance(enums, list) or not enums or val in enums:
        return val
    if val == "aberta" and "futura" in enums:
        return "futura"
    if val == "paga" and "liquidada" in enums:
        return "liquidada"
    if val == "vencida" and "atrasada" in enums:
        return "atrasada"
    raise ValueError(f"{label}: status {val!r} incompatível com enum do schema {enums}")


def _conform_document_to_schema(
    db: Any,
    collection_name: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Filter `candidate` to allowed `$jsonSchema` properties and canonical status enums without fabricating values."""
    schema = _get_collection_schema(db, collection_name)
    if schema is None:
        return {k: v for k, v in candidate.items() if v is not None}

    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    strict_props = schema.get("additionalProperties") is False

    out: dict[str, Any] = {}
    for k, v in candidate.items():
        if strict_props and k not in props:
            continue
        spec = props.get(k) or {}
        btype = spec.get("bsonType")
        allows_null = isinstance(btype, list) and "null" in btype
        if v is None and not allows_null:
            continue
        out[k] = v

    for key, val in list(out.items()):
        spec = props.get(key) or {}
        enums = spec.get("enum")
        if isinstance(enums, list) and enums and val is not None:
            if key == "status" and isinstance(val, str):
                out[key] = _translate_status_for_schema(val, enums, label=f"{collection_name}.status")
            elif val not in enums:
                raise ValueError(f"{collection_name}.{key}: valor {val!r} fora do enum {enums}")

    missing = required - set(out.keys())
    if missing:
        raise ValueError(f"{collection_name}: campos obrigatórios ausentes para o validador: {sorted(missing)}")
    return out


@dataclass
class TargetResolution:
    target: str
    kind: str  # "checking" | "card"
    institution: str
    profile_key: str | None = None
    account_id: ObjectId | None = None
    card_final: str | None = None
    additional_finals: list[str] = field(default_factory=list)
    due_day: int | None = None
    will_create_card: bool = False


@dataclass
class ExistingCandidate:
    source: str  # "ledger" | "capture"
    doc_id: Any
    identity: str
    document: dict[str, Any]
    date: date
    amount: Decimal
    instrument_target: str | None
    institution: str | None
    popular_name: str
    display_label: str
    raw_texts: list[str]
    normalized_texts: set[str]
    brand_keys: set[str]
    categoria: str | None
    subcategoria: str | None
    reconciled_keys: set[str]


@dataclass
class ItemMatchPlan:
    item: OfficialStatementItem
    doc: OfficialStatementDocument
    status: str  # "already_reconciled" | "matched_exact" | "matched_principal" | "missing" | "skipped_prior_payment" | "skipped_deleted_by_user" | "skipped_overlap_duplicate" | "ambiguous_skip"
    candidate: ExistingCandidate | None = None
    fee_candidate: ExistingCandidate | None = None
    score: int = 0
    reason: str = ""
    insert_missing_fee: bool = False
    strong_identity: bool = False


def resolve_targets(db: Any, docs: list[OfficialStatementDocument]) -> dict[str, TargetResolution]:
    legacy_checking = legacy_accounts_by_profile(db)
    cards = list(db.accounts.find({"tipo": "cartao_credito"}))
    card_metas = {
        row["_id"]: row
        for row in db.account_profiles.find({"_id": {"$regex": "^card-profile:"}})
    }

    resolutions: dict[str, TargetResolution] = {}
    for doc in docs:
        if doc.kind == "bank_statement" and doc.account_profile_key:
            key = f"profile:{doc.account_profile_key}"
            leg = legacy_checking.get(doc.account_profile_key)
            resolutions[key] = TargetResolution(
                target=key,
                kind="checking",
                institution=doc.institution,
                profile_key=doc.account_profile_key,
                account_id=leg["_id"] if leg else None,
            )
        elif doc.kind == "card_statement" and doc.card_final:
            key = doc.items[0].target if doc.items else f"card:{doc.institution.lower()}:{doc.card_final}"
            wanted_finals = {doc.card_final, *(doc.additional_finals or [])}
            inst_norm = normalize_merchant_text(doc.institution)
            matched_card = None
            inst_candidates = []
            for card in cards:
                meta = card_metas.get(f"card-profile:{card['_id']}", {})
                if meta.get("deleted"):
                    continue
                vals = meta.get("values") or {}
                c_final = str(vals.get("cartao_final") or card.get("cartao_final") or "").strip()
                c_inst = normalize_merchant_text(
                    f"{vals.get('instituicao') or card.get('instituicao') or ''} {vals.get('apelido') or ''}"
                )
                if c_final and c_final in wanted_finals:
                    matched_card = card
                    break
                if inst_norm and inst_norm in c_inst:
                    inst_candidates.append((card, c_final))
            if matched_card is None and len(inst_candidates) == 1 and not inst_candidates[0][1]:
                matched_card = inst_candidates[0][0]

            due_day = date.fromisoformat(doc.due_date).day if doc.due_date else None
            resolutions[key] = TargetResolution(
                target=key,
                kind="card",
                institution=doc.institution,
                account_id=matched_card["_id"] if matched_card else None,
                card_final=doc.card_final,
                additional_finals=list(doc.additional_finals or []),
                due_day=due_day,
                will_create_card=matched_card is None,
            )
    return resolutions


def _build_account_lookup(db: Any, targets: dict[str, TargetResolution]) -> dict[Any, tuple[str, str]]:
    mapping: dict[Any, tuple[str, str]] = {}
    for res in targets.values():
        if res.account_id is not None:
            mapping[res.account_id] = (res.target, res.institution)

    for acc in db.accounts.find({}):
        aid = acc["_id"]
        if aid in mapping:
            continue
        inst = str(acc.get("instituicao") or "")
        if acc.get("tipo") == "conta_corrente":
            prof = profile_for_legacy(db, acc)
            if prof:
                mapping[aid] = (f"profile:{prof}", inst)
        elif acc.get("tipo") == "cartao_credito":
            c_final = str(acc.get("cartao_final") or "0000")
            inst_slug = normalize_merchant_text(inst).lower().split()[0] if inst else "card"
            mapping[aid] = (f"card:{inst_slug}:{c_final}", inst)
    return mapping


def _rows_to_candidates(
    rows_with_source: list[tuple[str, dict[str, Any]]],
    *,
    merchants_by_id: dict[Any, dict[str, Any]],
    account_lookup: dict[Any, tuple[str, str]],
    keys_by_identity: dict[str, set[str]],
) -> list[ExistingCandidate]:
    profile_inst = {
        "itau": "Itaú",
        "santander": "Santander",
        "inter": "Inter",
        "btg": "BTG",
        "mercado_pago": "Mercado Pago",
    }
    candidates: list[ExistingCandidate] = []
    seen_identities: set[str] = set()
    for source, row in rows_with_source:
        if not row.get("data_evento") or row.get("valor") is None:
            continue
        identity = str(row.get("provisional_id") or row.get("_id") or "")
        if not identity or f"{source}:{identity}" in seen_identities:
            continue
        seen_identities.add(f"{source}:{identity}")
        mdoc = merchants_by_id.get(row.get("merchant_id")) or {}
        popular_name = str(mdoc.get("nome_padrao") or row.get("vendedor") or "").strip()
        alias_vals = [
            str(a.get("valor") or "").strip()
            for a in (mdoc.get("aliases") or [])
            if isinstance(a, dict) and a.get("valor")
        ]
        raw_fields = [
            popular_name,
            str(row.get("merchant_name") or "").strip(),
            str(row.get("merchant_raw") or "").strip(),
            str(row.get("estabelecimento_original") or "").strip(),
            str(row.get("descricao_original") or "").strip(),
            *alias_vals,
        ]
        raw_texts = [t for t in raw_fields if t]
        norm_texts = {normalize_merchant_text(t) for t in raw_texts} - {""}
        bkeys: set[str] = set()
        for t in raw_texts:
            bkeys |= brand_keys_for_text(t)

        inst_target: str | None = None
        inst_name: str | None = None
        if row.get("account_profile_id"):
            prof = str(row["account_profile_id"])
            inst_target = f"profile:{prof}"
            inst_name = profile_inst.get(prof, prof)
        elif row.get("account_id") in account_lookup:
            inst_target, inst_name = account_lookup[row["account_id"]]

        rec_keys = set(keys_by_identity.get(identity, set()))
        inline_rec = row.get("official_reconciliation") or {}
        if isinstance(inline_rec, dict) and inline_rec.get("item_key"):
            rec_keys.add(str(inline_rec["item_key"]))

        cat = row.get("categoria_sugerida") if source == "capture" else row.get("categoria")
        candidates.append(
            ExistingCandidate(
                source=source,
                doc_id=row.get("_id"),
                identity=identity,
                document=row,
                date=local_date(row["data_evento"]),
                amount=_dec(row["valor"]).quantize(Decimal("0.01")),
                instrument_target=inst_target,
                institution=inst_name,
                popular_name=popular_name,
                display_label=next((t for t in raw_texts if t), "Sem descrição"),
                raw_texts=raw_texts,
                normalized_texts=norm_texts,
                brand_keys=bkeys,
                categoria=str(cat) if cat else None,
                subcategoria=str(row["subcategoria"]) if row.get("subcategoria") else None,
                reconciled_keys=rec_keys,
            )
        )
    return candidates


def _load_candidates_and_tombstones(
    db: Any,
    docs: list[OfficialStatementDocument],
    targets: dict[str, TargetResolution],
) -> tuple[list[ExistingCandidate], list[ExistingCandidate]]:
    all_dates: list[date] = []
    all_rec_keys: list[str] = []
    card_competencias: list[str] = []
    for doc in docs:
        if doc.kind == "card_statement" and doc.competencia and doc.competencia not in card_competencias:
            card_competencias.append(doc.competencia)
        for item in doc.items:
            if item.skip_insert:
                continue
            all_dates.append(date.fromisoformat(item.date))
            all_dates.append(date.fromisoformat(item.purchase_date_hint))
            for base_k in {item.item_key, item.legacy_item_key}:
                all_rec_keys.append(base_k)
                all_rec_keys.append(f"{base_k}:principal")
                if item.fee_amount:
                    all_rec_keys.append(f"{base_k}:fee")
    if not all_dates:
        return [], []

    min_dt = min(all_dates) - timedelta(days=15)
    max_dt = max(all_dates) + timedelta(days=10)
    window = {"$gte": day_start(min_dt), "$lt": day_start(max_dt + timedelta(days=1))}

    rec_links = list(db.official_statement_reconciliations.find({"_id": {"$in": all_rec_keys}}))
    keys_by_identity: dict[str, set[str]] = {}
    linked_tx_oids: list[Any] = []
    linked_prov_ids: list[str] = []
    for link in rec_links:
        ident = str(link.get("movement_identity") or "")
        if ident:
            keys_by_identity.setdefault(ident, set()).add(str(link["_id"]))
        if link.get("movement_source") == "ledger" and link.get("movement_doc_id") is not None:
            linked_tx_oids.append(link["movement_doc_id"])
        elif link.get("movement_source") == "capture" and ident:
            linked_prov_ids.append(ident)

    det_prov_ids: list[str] = []
    for rk in all_rec_keys:
        _, pid = deterministic_ids(rk)
        det_prov_ids.append(pid)

    tx_branches: list[dict[str, Any]] = [{"data_evento": window}]
    if linked_tx_oids:
        tx_branches.append({"_id": {"$in": linked_tx_oids}})
    if card_competencias:
        tx_branches.append({"competencia": {"$in": card_competencias}})
    tx_query = {"$or": tx_branches} if len(tx_branches) > 1 else tx_branches[0]
    tx_rows = list(db.transactions.find(tx_query))
    tx_id_strs = {str(r["_id"]) for r in tx_rows}

    prov_branches: list[dict[str, Any]] = [{"data_evento": window}]
    if linked_prov_ids or det_prov_ids:
        prov_branches.append({"provisional_id": {"$in": list({*linked_prov_ids, *det_prov_ids})}})
    if card_competencias:
        prov_branches.append({"competencia": {"$in": card_competencias}})
    prov_window_filter: dict[str, Any] = {"$or": prov_branches} if len(prov_branches) > 1 else prov_branches[0]

    prov_rows = [
        r
        for r in db.provisional_transactions.find(
            {"status": {"$in": ["provisional", "enriched", "confirmed"]}, **prov_window_filter}
        )
        if not duplicate_of_ledger(r, tx_id_strs)
    ]

    cancelled_prov_rows = list(
        db.provisional_transactions.find({"status": "cancelled", **prov_window_filter})
    )
    deleted_archive_rows = list(db.deleted_movements.find({}))
    tombstone_pairs: list[tuple[str, dict[str, Any]]] = [
        ("capture", r) for r in cancelled_prov_rows
    ]
    for arch in deleted_archive_rows:
        snap = arch.get("document")
        if isinstance(snap, dict):
            src = "capture" if arch.get("source") == "capture" else "ledger"
            tombstone_pairs.append((src, snap))

    all_raw_rows = [*tx_rows, *prov_rows, *(r for _, r in tombstone_pairs)]
    merchant_ids = {
        r.get(field_name)
        for r in all_raw_rows
        for field_name in ("merchant_id", "platform_id")
        if r.get(field_name)
    }
    merchants_by_id: dict[Any, dict[str, Any]] = {}
    if merchant_ids:
        for mdoc in db.merchants.find({"_id": {"$in": list(merchant_ids)}}):
            merchants_by_id[mdoc["_id"]] = mdoc

    account_lookup = _build_account_lookup(db, targets)
    active_candidates = _rows_to_candidates(
        [*[("ledger", r) for r in tx_rows], *[("capture", r) for r in prov_rows]],
        merchants_by_id=merchants_by_id,
        account_lookup=account_lookup,
        keys_by_identity=keys_by_identity,
    )
    deleted_candidates = _rows_to_candidates(
        tombstone_pairs,
        merchants_by_id=merchants_by_id,
        account_lookup=account_lookup,
        keys_by_identity=keys_by_identity,
    )
    return active_candidates, deleted_candidates


def load_existing_candidates(
    db: Any,
    docs: list[OfficialStatementDocument],
    targets: dict[str, TargetResolution],
) -> list[ExistingCandidate]:
    active, _ = _load_candidates_and_tombstones(db, docs, targets)
    return active


def _candidate_installment(cand: ExistingCandidate) -> tuple[int | None, int | None]:
    raw_doc = cand.document
    inst_obj = raw_doc.get("installment")
    if isinstance(inst_obj, dict):
        num = inst_obj.get("numero")
        tot = inst_obj.get("total")
        if isinstance(num, int) and isinstance(tot, int):
            return num, tot
    p_num = raw_doc.get("parcela_numero")
    p_tot = raw_doc.get("parcela_total")
    if isinstance(p_num, int) and isinstance(p_tot, int):
        return p_num, p_tot
    for text in cand.raw_texts:
        num, tot = _parse_installment_suffix(text)
        if num is not None and tot is not None:
            return num, tot
    return None, None


def _is_fee_like_candidate(cand: ExistingCandidate, target: str) -> bool:
    if cand.instrument_target not in {target, None}:
        return False
    nat = str(cand.document.get("natureza") or "").upper()
    if nat in {"TARIFA_ENCARGO", "IMPOSTO_TRIBUTO"}:
        return True
    if cand.categoria == "Finanças/Impostos":
        return True
    joined = " ".join(cand.normalized_texts)
    return any(tok in joined for tok in ("ENCARGO", "JUROS", "IOF", "TARIFA", "TAXA"))


def _score_candidate_pair(
    item: OfficialStatementItem,
    doc: OfficialStatementDocument,
    cand: ExistingCandidate,
) -> tuple[int, str, str, bool] | None:
    item_amt = Decimal(item.amount).quantize(Decimal("0.01"))
    princ_amt = Decimal(item.principal_amount).quantize(Decimal("0.01")) if item.principal_amount else None

    if cand.amount == item_amt:
        mode = "matched_exact"
    elif princ_amt is not None and cand.amount == princ_amt:
        mode = "matched_principal"
    else:
        return None

    # Enforce installment number/total and competence cycle isolation (P0-04)
    cand_inst_num, cand_inst_tot = _candidate_installment(cand)
    if item.installment_number is not None and cand_inst_num is not None and item.installment_number != cand_inst_num:
        return None
    if item.installment_total is not None and cand_inst_tot is not None and item.installment_total != cand_inst_tot:
        return None

    cand_comp = str(cand.document.get("competencia") or "").strip()
    if doc.kind == "card_statement" and doc.competencia:
        if cand_comp and cand_comp != doc.competencia:
            return None
        if (
            item.prior_cycle_installment
            and (item.installment_number or 1) > 1
            and cand_comp != doc.competencia
            and cand_inst_num != item.installment_number
            and cand.date.strftime("%Y-%m") != doc.competencia
        ):
            return None

    item_dt = date.fromisoformat(item.date)
    hint_dt = date.fromisoformat(item.purchase_date_hint)
    day_gap = min(abs((cand.date - item_dt).days), abs((cand.date - hint_dt).days))
    max_gap = 6 if item.prior_cycle_installment else 4
    if day_gap > max_gap:
        if (
            item.prior_cycle_installment
            and (cand.date.strftime("%Y-%m") == item.statement_month or cand_comp == doc.competencia)
        ):
            day_gap = 2
        else:
            return None

    same_target = cand.instrument_target == item.target
    unassigned = cand.instrument_target is None

    item_norm = normalize_merchant_text(item.description)
    vendor_norm = normalize_merchant_text(item.popular_vendor)
    item_brands = brand_keys_for_text(item.description) | brand_keys_for_text(item.popular_vendor)
    for alias in item.brand_aliases:
        na = normalize_merchant_text(alias)
        if na:
            item_brands.add(na)

    exact_desc = item_norm in cand.normalized_texts or (len(vendor_norm) >= 4 and vendor_norm in cand.normalized_texts)

    # Disjoint commercial brands must NEVER merge, regardless of category/date/amount (P0-03)
    if item_brands and cand.brand_keys and not (item_brands & cand.brand_keys) and not exact_desc:
        return None

    brand_hit = bool(item_brands and cand.brand_keys and (item_brands & cand.brand_keys)) or any(
        popular_alias_match(item.description, raw_t) for raw_t in cand.raw_texts
    )

    item_tokens = _informative_tokens(item.description) | _informative_tokens(item.popular_vendor)
    cand_tokens: set[str] = set()
    for raw_t in cand.raw_texts:
        cand_tokens |= _informative_tokens(raw_t)
    token_overlap = bool(item_tokens & cand_tokens)
    best_ratio = max(
        (SequenceMatcher(None, item_norm, c_norm).ratio() for c_norm in cand.normalized_texts),
        default=0.0,
    )
    best_vendor_ratio = max(
        (SequenceMatcher(None, vendor_norm, c_norm).ratio() for c_norm in cand.normalized_texts),
        default=0.0,
    )
    strong_text = max(best_ratio, best_vendor_ratio) >= 0.72 or (
        token_overlap and min(len(item_tokens), len(cand_tokens)) >= 1 and max(best_ratio, best_vendor_ratio) >= 0.58
    )

    context_set = {normalize_merchant_text(h) for h in item.context_hints} - {""}
    # Category equality alone never justifies merging two distinct merchants (P0-03)
    context_hit = bool(context_set & cand_tokens) and not cand.brand_keys

    strong_identity = bool(exact_desc or brand_hit or strong_text)
    if not strong_identity and not (context_hit and day_gap <= 1):
        return None

    if not same_target and not unassigned:
        if cand.source == "ledger" and mode != "matched_principal":
            return None
        if not strong_identity:
            return None

    if mode == "matched_principal" and not (strong_identity or context_hit):
        return None

    score = 0
    reasons: list[str] = []

    if exact_desc:
        score += 130
        reasons.append("mesma descrição/estabelecimento")
    elif brand_hit:
        score += 115
        reasons.append(f"nome popular reconhecido ('{cand.display_label}' ⇔ '{item.popular_vendor}')")
    elif strong_text:
        score += 75
        reasons.append(f"descrição semelhante ('{cand.display_label}')")
    elif context_hit and day_gap <= 1:
        score += 25
        reasons.append("contexto compatível e valor exato")

    if day_gap == 0:
        score += 45
        reasons.append("mesma data" if cand.date == item_dt else f"data da compra ({hint_dt.isoformat()})")
    elif day_gap == 1:
        score += 30
        reasons.append("diferença de 1 dia")
    elif day_gap == 2:
        score += 15
        reasons.append("diferença de 2 dias")
    else:
        score += 5
        reasons.append(f"diferença de {day_gap} dias")

    if same_target:
        score += 35
    elif unassigned:
        score += 12
        reasons.append("captura sem conta/cartão vinculado")
    else:
        score -= 10
        reasons.append("lançado em outro instrumento pelo usuário")

    if mode == "matched_principal":
        score -= 10
        reasons.append(f"valor principal sem encargo do cartão ({item.principal_amount} vs {item.amount})")

    if score < 55:
        return None

    return score, mode, "; ".join(reasons), strong_identity


def _matches_reconciled_link(item: OfficialStatementItem, cand: ExistingCandidate) -> bool:
    item_amt = Decimal(item.amount).quantize(Decimal("0.01"))
    princ_amt = Decimal(item.principal_amount).quantize(Decimal("0.01")) if item.principal_amount else None
    if cand.amount != item_amt and (princ_amt is None or cand.amount != princ_amt):
        return False
    item_dt = date.fromisoformat(item.date)
    hint_dt = date.fromisoformat(item.purchase_date_hint)
    gap = min(abs((cand.date - item_dt).days), abs((cand.date - hint_dt).days))
    if gap > 7 and not item.prior_cycle_installment:
        return False
    _, det_prov_id = deterministic_ids(item.item_key)
    _, det_princ_prov_id = deterministic_ids(f"{item.item_key}:principal")
    _, leg_prov_id = deterministic_ids(item.legacy_item_key)
    _, leg_princ_prov_id = deterministic_ids(f"{item.legacy_item_key}:principal")
    if cand.identity in {det_prov_id, det_princ_prov_id, leg_prov_id, leg_princ_prov_id}:
        return True
    if item.item_key in cand.reconciled_keys or f"{item.item_key}:principal" in cand.reconciled_keys:
        return True
    if item.legacy_item_key in cand.reconciled_keys or f"{item.legacy_item_key}:principal" in cand.reconciled_keys:
        # Legacy positional key requires brand/text non-contradiction (P0-07)
        item_brands = brand_keys_for_text(item.description) | brand_keys_for_text(item.popular_vendor)
        if item_brands and cand.brand_keys and not (item_brands & cand.brand_keys):
            return False
        return True
    return False


def build_reconciliation_plan(
    db: Any,
    docs: list[OfficialStatementDocument],
) -> tuple[list[ItemMatchPlan], dict[str, TargetResolution]]:
    deduplicate_overlapping_bank_statements(docs)
    targets = resolve_targets(db, docs)
    candidates, deleted_candidates = _load_candidates_and_tombstones(db, docs, targets)

    used_candidate_ids: set[str] = set()
    used_deleted_ids: set[str] = set()
    plans_by_slot: dict[tuple[str, int], ItemMatchPlan] = {}

    active_entries: list[tuple[tuple[str, int], OfficialStatementDocument, OfficialStatementItem]] = []
    seen_active_item_keys: set[str] = set()
    for doc in docs:
        for item in doc.items:
            slot = (doc.doc_key, item.row_index)
            if item.skip_insert:
                is_prior = item.kind == "bill_payment_credit"
                plans_by_slot[slot] = ItemMatchPlan(
                    item=item,
                    doc=doc,
                    status="skipped_prior_payment" if is_prior else "skipped_overlap_duplicate",
                    reason=(
                        "Crédito de pagamento da fatura anterior (informativo na fatura)."
                        if is_prior
                        else "Lançamento já coberto por extrato mais amplo da mesma conta no lote."
                    ),
                )
            elif item.item_key in seen_active_item_keys:
                plans_by_slot[slot] = ItemMatchPlan(
                    item=item,
                    doc=doc,
                    status="skipped_overlap_duplicate",
                    reason="Lançamento idêntico já presente em outro extrato da mesma conta no lote.",
                )
            else:
                seen_active_item_keys.add(item.item_key)
                active_entries.append((slot, doc, item))

    # Pass 1: Tombstone check + Already reconciled idempotency check
    for slot, doc, item in active_entries:
        deleted_hit = next(
            (
                d
                for d in deleted_candidates
                if d.identity not in used_deleted_ids and _matches_reconciled_link(item, d)
            ),
            None,
        )
        if deleted_hit is not None:
            used_deleted_ids.add(deleted_hit.identity)
            plans_by_slot[slot] = ItemMatchPlan(
                item=item,
                doc=doc,
                status="skipped_deleted_by_user",
                candidate=deleted_hit,
                reason="Movimento excluído/cancelado pelo usuário anteriormente; não reinserido.",
            )
            continue

        existing = next(
            (
                c
                for c in candidates
                if c.identity not in used_candidate_ids and _matches_reconciled_link(item, c)
            ),
            None,
        )
        if existing is not None:
            used_candidate_ids.add(existing.identity)
            fee_cand = None
            insert_fee = False
            if item.fee_amount and existing.amount != Decimal(item.amount):
                _, det_fee_prov_id = deterministic_ids(f"{item.item_key}:fee")
                _, leg_fee_prov_id = deterministic_ids(f"{item.legacy_item_key}:fee")
                fee_cand = next(
                    (
                        c
                        for c in candidates
                        if c.identity not in used_candidate_ids
                        and (
                            f"{item.item_key}:fee" in c.reconciled_keys
                            or f"{item.legacy_item_key}:fee" in c.reconciled_keys
                            or c.identity in {det_fee_prov_id, leg_fee_prov_id}
                        )
                    ),
                    None,
                )
                if fee_cand is not None:
                    used_candidate_ids.add(fee_cand.identity)
                else:
                    insert_fee = True
            plans_by_slot[slot] = ItemMatchPlan(
                item=item,
                doc=doc,
                status="already_reconciled",
                candidate=existing,
                fee_candidate=fee_cand,
                score=1000,
                reason="Já conciliado anteriormente com comprovante oficial.",
                insert_missing_fee=insert_fee,
                strong_identity=True,
            )

    # Pass 2: Optimal greedy 1-to-1 matching with ambiguity margin protection
    remaining = [(slot, doc, it) for (slot, doc, it) in active_entries if slot not in plans_by_slot]
    scored_pairs: list[tuple[int, int, str, int, int, str, str, bool]] = []
    by_item: dict[int, list[tuple[int, int]]] = {}
    by_cand: dict[int, list[tuple[int, int]]] = {}

    for item_idx, (_slot, doc, item) in enumerate(remaining):
        item_dt = date.fromisoformat(item.purchase_date_hint)
        for cand_idx, cand in enumerate(candidates):
            if cand.identity in used_candidate_ids:
                continue
            scored = _score_candidate_pair(item, doc, cand)
            if scored is None:
                continue
            sc, mode, reason, strong_id = scored
            gap = abs((cand.date - item_dt).days)
            scored_pairs.append((sc, -gap, cand.identity, item_idx, cand_idx, mode, reason, strong_id))
            by_item.setdefault(item_idx, []).append((sc, cand_idx))
            by_cand.setdefault(cand_idx, []).append((sc, item_idx))

    scored_pairs.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
    matched_item_indices: set[int] = set()

    def _same_candidate_signature(a: ExistingCandidate, b: ExistingCandidate) -> bool:
        return (
            a.instrument_target == b.instrument_target
            and a.date == b.date
            and a.amount == b.amount
            and normalize_merchant_text(a.display_label) == normalize_merchant_text(b.display_label)
        )

    def _same_item_signature(a: OfficialStatementItem, b: OfficialStatementItem) -> bool:
        return (
            a.target == b.target
            and a.date == b.date
            and Decimal(a.amount) == Decimal(b.amount)
            and normalize_merchant_text(a.description) == normalize_merchant_text(b.description)
        )

    for sc, _neg_gap, _cid, item_idx, cand_idx, mode, reason, strong_id in scored_pairs:
        if item_idx in matched_item_indices:
            continue
        cand = candidates[cand_idx]
        if cand.identity in used_candidate_ids:
            continue
        slot, doc, item = remaining[item_idx]

        # Check non-twin competing candidates for this item
        rival_cands = [
            (r_sc, candidates[r_cidx])
            for r_sc, r_cidx in by_item.get(item_idx, ())
            if r_cidx != cand_idx and candidates[r_cidx].identity not in used_candidate_ids
        ]
        ambiguous_cand = any(
            not _same_candidate_signature(cand, r_cand) and (sc - r_sc) < _MIN_COMPETITION_MARGIN
            for r_sc, r_cand in rival_cands
        )
        # Check non-twin competing items for this candidate
        rival_items = [
            (r_sc, remaining[r_iidx][2])
            for r_sc, r_iidx in by_cand.get(cand_idx, ())
            if r_iidx != item_idx and r_iidx not in matched_item_indices
        ]
        ambiguous_item = any(
            not _same_item_signature(item, r_item) and (sc - r_sc) < _MIN_COMPETITION_MARGIN
            for r_sc, r_item in rival_items
        )
        if ambiguous_cand or ambiguous_item:
            matched_item_indices.add(item_idx)
            plans_by_slot[slot] = ItemMatchPlan(
                item=item,
                doc=doc,
                status="ambiguous_skip",
                score=sc,
                reason="Múltiplos candidatos com pontuação próxima; preservado para revisão manual sem fusão automática.",
            )
            continue

        matched_item_indices.add(item_idx)
        used_candidate_ids.add(cand.identity)

        fee_cand = None
        insert_fee = False
        if mode == "matched_principal" and item.fee_amount:
            fee_amt = Decimal(item.fee_amount).quantize(Decimal("0.01"))
            item_dt = date.fromisoformat(item.date)
            fee_cand = next(
                (
                    c
                    for c in candidates
                    if c.identity not in used_candidate_ids
                    and c.amount == fee_amt
                    and abs((c.date - item_dt).days) <= 3
                    and _is_fee_like_candidate(c, item.target)
                ),
                None,
            )
            if fee_cand is not None:
                used_candidate_ids.add(fee_cand.identity)
            else:
                insert_fee = True

        plans_by_slot[slot] = ItemMatchPlan(
            item=item,
            doc=doc,
            status=mode,
            candidate=cand,
            fee_candidate=fee_cand,
            score=sc,
            reason=reason,
            insert_missing_fee=insert_fee,
            strong_identity=strong_id,
        )

    # Pass 3: Check tombstones (`deleted_movements` / `cancelled`) before marking `missing` (P0-05)
    for slot, doc, item in remaining:
        if slot in plans_by_slot:
            continue
        best_del: tuple[int, ExistingCandidate] | None = None
        for d_cand in deleted_candidates:
            if d_cand.identity in used_deleted_ids:
                continue
            d_scored = _score_candidate_pair(item, doc, d_cand)
            if d_scored is None:
                continue
            d_sc, _d_mode, _d_reason, d_strong = d_scored
            if d_strong and d_sc >= 95 and (best_del is None or d_sc > best_del[0]):
                best_del = (d_sc, d_cand)
        if best_del is not None:
            used_deleted_ids.add(best_del[1].identity)
            plans_by_slot[slot] = ItemMatchPlan(
                item=item,
                doc=doc,
                status="skipped_deleted_by_user",
                candidate=best_del[1],
                score=best_del[0],
                reason="Excluído ou cancelado deliberadamente pelo usuário; não reinserido.",
            )
        else:
            plans_by_slot[slot] = ItemMatchPlan(
                item=item,
                doc=doc,
                status="missing",
                reason="Ausente no banco; será inserido com nome popular e categoria canônica.",
            )

    ordered_plans = [plans_by_slot[(doc.doc_key, it.row_index)] for doc in docs for it in doc.items]
    return ordered_plans, targets


class _MerchantCache:
    """In-memory cache of `db.merchants` to avoid N+1 round-trips over WAN."""

    def __init__(self, db: Any):
        self.db = db
        self.by_id: dict[Any, dict[str, Any]] = {}
        self.by_ext: dict[str, dict[str, Any]] = {}
        self.by_exact: dict[str, dict[str, Any]] = {}
        self.by_norm: dict[str, dict[str, Any]] = {}
        for mdoc in db.merchants.find({}):
            self._register(mdoc)

    def _register(self, mdoc: dict[str, Any]) -> None:
        self.by_id[mdoc["_id"]] = mdoc
        if mdoc.get("external_key"):
            self.by_ext[str(mdoc["external_key"])] = mdoc
        nome = str(mdoc.get("nome_padrao") or "").strip()
        if nome:
            self.by_exact[nome] = mdoc
            norm = normalize_merchant_text(nome)
            if norm and norm not in self.by_norm:
                self.by_norm[norm] = mdoc

    def ensure_merchant(
        self,
        name: str,
        raw_descriptor: str | None,
        now: datetime,
        *,
        db: Any | None = None,
    ) -> ObjectId | None:
        target_db = db or self.db
        clean = (name or "").strip()
        if not clean:
            return None
        norm_key = normalize_merchant_text(clean)
        ext_key = f"merchant-popular-{uuid5(NAMESPACE_URL, norm_key)}"
        existing = self.by_exact.get(clean) or self.by_norm.get(norm_key) or self.by_ext.get(ext_key)
        if not existing:
            new_doc: dict[str, Any] = {
                "_id": ObjectId(),
                "external_key": ext_key,
                "schema_version": 2,
                "created_at": now,
                "updated_at": now,
                "tipo_entidade": "marca",
                "nome_padrao": clean,
                "aliases": [],
            }
            raw_val = (raw_descriptor or clean).strip()
            raw_norm = normalize_merchant_text(raw_val)
            if raw_norm:
                new_doc["aliases"].append(
                    {"valor": raw_val, "normalizado": raw_norm, "status": "confirmado", "source_record_ids": []}
                )
            target_db.merchants.insert_one(new_doc)
            self._register(new_doc)
            return new_doc["_id"]

        if raw_descriptor:
            raw_norm = normalize_merchant_text(raw_descriptor)
            aliases = [
                dict(a)
                for a in (existing.get("aliases") or [])
                if isinstance(a, dict) and "valor" in a and "normalizado" in a and "status" in a
            ]
            if raw_norm and not any(a.get("normalizado") == raw_norm for a in aliases):
                aliases.append(
                    {
                        "valor": raw_descriptor.strip(),
                        "normalizado": raw_norm,
                        "status": "confirmado",
                        "source_record_ids": [],
                    }
                )
                existing["aliases"] = aliases
                existing["updated_at"] = now
                target_db.merchants.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"aliases": aliases, "updated_at": now}},
                )
        return existing["_id"]

    def ensure_platform(self, platform_name: str | None, now: datetime, *, db: Any | None = None) -> ObjectId | None:
        target_db = db or self.db
        if not platform_name:
            return None
        ident = platform_identity(platform_name)
        canonical = ident[1] if ident else platform_name.strip()
        ext_key = ident[0] if ident else f"platform-user-{uuid4()}"
        found = (self.by_ext.get(ext_key) if ident else None) or self.by_exact.get(canonical) or self.by_norm.get(
            normalize_merchant_text(canonical)
        )
        if found:
            return found["_id"]
        doc: dict[str, Any] = {
            "_id": ObjectId(),
            "external_key": ext_key,
            "schema_version": 2,
            "created_at": now,
            "updated_at": now,
            "tipo_entidade": "marca",
            "nome_padrao": canonical,
            "aliases": [
                {
                    "valor": canonical,
                    "normalizado": normalize_merchant_text(canonical) or canonical.upper(),
                    "status": "confirmado",
                    "source_record_ids": [],
                }
            ],
        }
        target_db.merchants.insert_one(doc)
        self._register(doc)
        return doc["_id"]


def _payment_method_for_item(item: OfficialStatementItem, target: TargetResolution) -> str | None:
    if target.kind == "card":
        return "cartao_credito"
    upper = item.description.upper()
    if "PIX" in upper:
        return "pix"
    if "CARTAO DEB" in upper:
        return "cartao_debito"
    if "BOLETO" in upper:
        return "boleto"
    if item.kind in {"expense", "bill_payment"}:
        return "debito_conta"
    return None


def _record_reconciliation_link(
    db: Any,
    *,
    seed_key: str,
    movement_source: str,
    movement_identity: str,
    movement_doc_id: Any,
    item: OfficialStatementItem,
    doc: OfficialStatementDocument,
    match_status: str,
    now: datetime,
) -> None:
    db.official_statement_reconciliations.update_one(
        {"_id": seed_key},
        {
            "$set": {
                "movement_source": movement_source,
                "movement_identity": movement_identity,
                "movement_doc_id": movement_doc_id,
                "doc_key": doc.doc_key,
                "filename": doc.filename,
                "sha256": doc.sha256,
                "official_date": item.date,
                "purchase_date_hint": item.purchase_date_hint,
                "official_description": item.description,
                "official_amount": item.amount,
                "principal_amount": item.principal_amount,
                "fee_amount": item.fee_amount,
                "match_status": match_status,
                "reconciled_at": now,
            }
        },
        upsert=True,
    )


def _raw_event_payload_for_item(
    *,
    event_id: str,
    linked_id: Any,
    seed_key: str,
    item: OfficialStatementItem,
    doc: OfficialStatementDocument,
    amount_dec: Decimal,
    vendor_name: str,
    stamp: datetime,
    now: datetime,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "source_type": "manual",
        "source_provider": "official_statement_reconciliation",
        "captured_at": now,
        "received_at": now,
        "processing_status": "confirmed",
        "linked_transaction_id": linked_id,
        "raw_payload": {
            "doc_key": doc.doc_key,
            "filename": doc.filename,
            "sha256": doc.sha256,
            "row_index": item.row_index,
            "seed_key": seed_key,
        },
        "parsed_payload": {
            "amount": f"{abs(amount_dec):.2f}",
            "currency": "BRL",
            "merchant_raw": item.description,
            "popular_vendor": vendor_name,
            "event_timestamp": stamp.isoformat(),
            "direction": "expense" if amount_dec < 0 else "income",
        },
        "parser_version": "official-statement-reconciler-v2",
        "media_status": "none",
    }


def _insert_official_provisional(
    db: Any,
    *,
    merchant_cache: _MerchantCache,
    existing_prov_by_id: dict[str, dict[str, Any]],
    seed_key: str,
    item: OfficialStatementItem,
    doc: OfficialStatementDocument,
    target: TargetResolution,
    amount_dec: Decimal,
    vendor_name: str,
    categoria: str | None,
    subcategoria: str | None,
    natureza: str | None,
    tipo: str,
    now: datetime,
) -> tuple[str, bool]:
    event_id, provisional_id = deterministic_ids(seed_key)
    evt_date = date.fromisoformat(item.date)
    stamp = datetime.combine(evt_date, time(12, 0), LOCAL_ZONE).astimezone(timezone.utc)

    existing_prov = existing_prov_by_id.get(provisional_id)
    if existing_prov is None:
        existing_prov = db.provisional_transactions.find_one({"provisional_id": provisional_id})
        if existing_prov is not None:
            existing_prov_by_id[provisional_id] = existing_prov

    if existing_prov is not None:
        # Never resurrect or link onto a user-cancelled capture (P0-05)
        if existing_prov.get("status") == "cancelled":
            return provisional_id, False
        existing_oid = existing_prov["_id"]
        # Heal missing raw_events on idempotent retry (P0-06)
        raw_evt = _raw_event_payload_for_item(
            event_id=event_id,
            linked_id=existing_oid,
            seed_key=seed_key,
            item=item,
            doc=doc,
            amount_dec=amount_dec,
            vendor_name=vendor_name,
            stamp=stamp,
            now=now,
        )
        db.raw_events.update_one(
            {"event_id": event_id},
            {"$setOnInsert": {k: v for k, v in raw_evt.items() if k != "event_id"}},
            upsert=True,
        )
        _record_reconciliation_link(
            db,
            seed_key=seed_key,
            movement_source="capture",
            movement_identity=provisional_id,
            movement_doc_id=existing_oid,
            item=item,
            doc=doc,
            match_status="inserted_official",
            now=now,
        )
        return provisional_id, False

    merchant_oid = None
    platform_oid = None
    if not item.technical_transit and not item.own_transfer and item.kind not in {"bill_payment", "bill_payment_credit"}:
        merchant_oid = merchant_cache.ensure_merchant(vendor_name, item.description, now, db=db)
        platform_oid = merchant_cache.ensure_platform(item.platform, now, db=db)

    metodo = _payment_method_for_item(item, target)
    prov_doc: dict[str, Any] = {
        "provisional_id": provisional_id,
        "event_id": event_id,
        "status": "confirmed",
        "revision": 1,
        "status_revisao": "validado",
        "revisado_em": now,
        "data_evento": stamp,
        "valor": Decimal128(f"{amount_dec:.2f}"),
        "fluxo": "saida" if amount_dec < 0 else "entrada",
        "moeda": "BRL",
        "merchant_raw": item.description,
        "vendedor": vendor_name,
        "tipo": tipo,
        "natureza": natureza,
        "account_profile_id": target.profile_key if target.kind == "checking" else None,
        "account_id": target.account_id if target.kind == "card" else None,
        "bank_transaction_ref": item.bank_ref,
        "created_at": now,
        "confirmed_at": now,
        "date_precision": "day",
    }
    if doc.kind == "card_statement" and doc.competencia:
        prov_doc["competencia"] = doc.competencia
    if merchant_oid is not None:
        prov_doc["merchant_id"] = merchant_oid
    if platform_oid is not None:
        prov_doc["platform_id"] = platform_oid
    if categoria:
        prov_doc["categoria_sugerida"] = categoria
    if subcategoria:
        prov_doc["subcategoria"] = subcategoria
    if metodo:
        prov_doc["metodo_pagamento"] = metodo
    if item.installment_number and item.installment_total:
        prov_doc["parcela_numero"] = item.installment_number
        prov_doc["parcela_total"] = item.installment_total

    inserted_id = db.provisional_transactions.insert_one(prov_doc).inserted_id
    prov_doc["_id"] = inserted_id
    existing_prov_by_id[provisional_id] = prov_doc
    raw_evt = _raw_event_payload_for_item(
        event_id=event_id,
        linked_id=inserted_id,
        seed_key=seed_key,
        item=item,
        doc=doc,
        amount_dec=amount_dec,
        vendor_name=vendor_name,
        stamp=stamp,
        now=now,
    )
    db.raw_events.update_one(
        {"event_id": event_id},
        {"$setOnInsert": {k: v for k, v in raw_evt.items() if k != "event_id"}},
        upsert=True,
    )
    _record_reconciliation_link(
        db,
        seed_key=seed_key,
        movement_source="capture",
        movement_identity=provisional_id,
        movement_doc_id=inserted_id,
        item=item,
        doc=doc,
        match_status="inserted_official",
        now=now,
    )
    return provisional_id, True


def apply_reconciliation_plan(
    db: Any,
    docs: list[OfficialStatementDocument],
    plans: list[ItemMatchPlan],
    targets: dict[str, TargetResolution],
    *,
    force_balances: bool = False,
    allow_standalone: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today_local = local_date(now)
    created_cards: list[str] = []

    # 1. Ensure missing credit cards exist in db.accounts (strictly obeying accounts-validator.json)
    for res in targets.values():
        if res.kind == "card" and res.account_id is None:
            with _atomic_unit(db, allow_standalone=allow_standalone) as writing:
                card_oid = ObjectId()
                apelido = f"{res.institution} final {res.card_final}"
                card_doc: dict[str, Any] = {
                    "_id": card_oid,
                    "external_key": f"card-official-{res.institution.lower()}-{res.card_final or card_oid}",
                    "schema_version": 2,
                    "tipo": "cartao_credito",
                    "instituicao": res.institution,
                    "titular": "Titular",
                    "moeda": "BRL",
                    "status": "ativo",
                    "created_at": now,
                    "updated_at": now,
                }
                if res.card_final and len(res.card_final) == 4 and res.card_final.isdigit():
                    card_doc["cartao_final"] = res.card_final
                writing.accounts.insert_one(card_doc)
                profile_key = f"card-profile:{card_oid}"
                writing.account_profiles.update_one(
                    {"_id": profile_key},
                    {
                        "$set": {
                            "key": profile_key,
                            "entity_type": "card",
                            "deleted": False,
                            "revision": 1,
                            "updated_at": now,
                            "values": {
                                "instituicao": res.institution,
                                "apelido": apelido,
                                "cartao_final": res.card_final or "",
                                "dia_vencimento": res.due_day,
                            },
                        }
                    },
                    upsert=True,
                )
                res.account_id = card_oid
                res.will_create_card = False
                created_cards.append(str(card_oid))

    merchant_cache = _MerchantCache(db)
    det_prov_ids: list[str] = []
    for plan in plans:
        for base_k in {plan.item.item_key, plan.item.legacy_item_key}:
            for sk in (base_k, f"{base_k}:principal", f"{base_k}:fee"):
                _, pid = deterministic_ids(sk)
                det_prov_ids.append(pid)
    existing_prov_by_id: dict[str, dict[str, Any]] = {
        str(r["provisional_id"]): r
        for r in db.provisional_transactions.find({"provisional_id": {"$in": det_prov_ids}})
        if r.get("provisional_id")
    }

    updated_existing = 0
    inserted_new = 0
    inserted_fees = 0
    skipped_deleted = 0
    skipped_ambiguous = 0
    skipped_overlap = 0

    tx_schema = _get_collection_schema(db, "transactions")
    allowed_tx_props = (
        set(tx_schema.get("properties") or []) & _CANONICAL_TX_PROPS
        if (tx_schema and tx_schema.get("additionalProperties") is False)
        else set(_CANONICAL_TX_PROPS)
    )

    # 2. Apply item reconciliations & missing insertions in bounded atomic chunks
    CHUNK_SIZE = 20
    for chunk_start in range(0, len(plans), CHUNK_SIZE):
        chunk = plans[chunk_start : chunk_start + CHUNK_SIZE]
        with _atomic_unit(db, allow_standalone=allow_standalone) as writing:
            for plan in chunk:
                item = plan.item
                doc = plan.doc
                if plan.status == "skipped_prior_payment":
                    continue
                if plan.status == "skipped_deleted_by_user":
                    skipped_deleted += 1
                    continue
                if plan.status == "skipped_overlap_duplicate":
                    skipped_overlap += 1
                    continue
                if plan.status == "ambiguous_skip":
                    skipped_ambiguous += 1
                    continue
                target = targets[item.target]

                if plan.status in {"already_reconciled", "matched_exact", "matched_principal"} and plan.candidate is not None:
                    cand = plan.candidate
                    raw_doc = cand.document
                    collection = writing.transactions if cand.source == "ledger" else writing.provisional_transactions

                    if plan.status != "already_reconciled":
                        set_fields: dict[str, Any] = {"updated_at": now}
                        cleared = set(raw_doc.get("user_cleared_fields") or [])
                        if cand.source == "ledger":
                            ov_col = getattr(writing, "transaction_field_overrides", None)
                            if ov_col is not None and hasattr(ov_col, "find_one"):
                                ov_doc = ov_col.find_one({"_id": cand.doc_id})
                                if isinstance(ov_doc, dict) and isinstance(
                                    ov_doc.get("user_cleared_fields"), (list, tuple, set)
                                ):
                                    cleared |= {str(f) for f in ov_doc["user_cleared_fields"] if f}
                        learn_alias = item.description if plan.strong_identity else None

                        if raw_doc.get("merchant_id"):
                            mdoc = merchant_cache.by_id.get(raw_doc["merchant_id"])
                            if mdoc and mdoc.get("nome_padrao") and learn_alias:
                                merchant_cache.ensure_merchant(mdoc["nome_padrao"], learn_alias, now, db=writing)
                        elif not item.technical_transit and not item.own_transfer and item.kind not in {"bill_payment", "bill_payment_credit"}:
                            preferred_vendor = cand.popular_name or item.popular_vendor
                            m_oid = merchant_cache.ensure_merchant(preferred_vendor, learn_alias, now, db=writing)
                            if m_oid is not None and "merchant_id" not in cleared and "vendedor" not in cleared:
                                set_fields["merchant_id"] = m_oid

                        if (
                            not raw_doc.get("platform_id")
                            and item.platform
                            and "platform_id" not in cleared
                            and "plataforma" not in cleared
                        ):
                            p_oid = merchant_cache.ensure_platform(item.platform, now, db=writing)
                            if p_oid is not None:
                                set_fields["platform_id"] = p_oid

                        if cand.source == "capture":
                            set_fields["revision"] = int(raw_doc.get("revision") or 1) + 1
                            was_promoted_to_confirmed = False
                            if raw_doc.get("status") in {"provisional", "enriched"}:
                                set_fields["status"] = "confirmed"
                                set_fields["confirmed_at"] = now
                                set_fields["status_revisao"] = "validado"
                                set_fields["revisado_em"] = now
                                was_promoted_to_confirmed = True
                            if (
                                doc.kind == "card_statement"
                                and doc.competencia
                                and not raw_doc.get("competencia")
                                and "competencia" not in cleared
                            ):
                                set_fields["competencia"] = doc.competencia
                            if (
                                not raw_doc.get("account_profile_id")
                                and not raw_doc.get("account_id")
                                and "account_profile_id" not in cleared
                                and "account_id" not in cleared
                            ):
                                if target.kind == "checking" and target.profile_key:
                                    set_fields["account_profile_id"] = target.profile_key
                                elif target.kind == "card" and target.account_id:
                                    set_fields["account_id"] = target.account_id
                            if not raw_doc.get("categoria_sugerida") and item.categoria and "categoria_sugerida" not in cleared and "categoria" not in cleared:
                                set_fields["categoria_sugerida"] = item.categoria
                            if not raw_doc.get("subcategoria") and item.subcategoria and "subcategoria" not in cleared:
                                set_fields["subcategoria"] = item.subcategoria
                        else:
                            # Strictly schema-compliant fields for db.transactions
                            if raw_doc.get("status_conciliacao") != "confirmado":
                                set_fields["status_conciliacao"] = "confirmado"
                            if not raw_doc.get("categoria") and item.categoria and "categoria" not in cleared:
                                set_fields["categoria"] = item.categoria
                            if not raw_doc.get("subcategoria") and item.subcategoria and "subcategoria" not in cleared:
                                set_fields["subcategoria"] = item.subcategoria
                            set_fields = {
                                k: v
                                for k, v in set_fields.items()
                                if k in allowed_tx_props and v is not None
                            }

                        collection.update_one({"_id": cand.doc_id}, {"$set": set_fields})

                        if cand.source == "capture" and was_promoted_to_confirmed:
                            if raw_doc.get("event_id"):
                                writing.raw_events.update_one(
                                    {"event_id": raw_doc["event_id"]},
                                    {"$set": {"processing_status": "confirmed"}},
                                )
                            writing.review_queue.update_many(
                                {"status": "pending", "provisional_id": cand.identity},
                                {
                                    "$set": {
                                        "status": "resolved",
                                        "decision": "confirmed",
                                        "resolution_reason": "official_statement_reconciled",
                                        "resolved_at": now,
                                    }
                                },
                            )

                        _record_reconciliation_link(
                            writing,
                            seed_key=item.item_key,
                            movement_source=cand.source,
                            movement_identity=cand.identity,
                            movement_doc_id=cand.doc_id,
                            item=item,
                            doc=doc,
                            match_status=plan.status,
                            now=now,
                        )
                        updated_existing += 1

                    if plan.fee_candidate is not None and f"{item.item_key}:fee" not in plan.fee_candidate.reconciled_keys:
                        _record_reconciliation_link(
                            writing,
                            seed_key=f"{item.item_key}:fee",
                            movement_source=plan.fee_candidate.source,
                            movement_identity=plan.fee_candidate.identity,
                            movement_doc_id=plan.fee_candidate.doc_id,
                            item=item,
                            doc=doc,
                            match_status="matched_fee",
                            now=now,
                        )
                    elif plan.insert_missing_fee and item.fee_amount:
                        _, fee_created = _insert_official_provisional(
                            writing,
                            merchant_cache=merchant_cache,
                            existing_prov_by_id=existing_prov_by_id,
                            seed_key=f"{item.item_key}:fee",
                            item=item,
                            doc=doc,
                            target=target,
                            amount_dec=Decimal(item.fee_amount),
                            vendor_name=f"Encargos Cartão — {item.popular_vendor}",
                            categoria="Finanças/Impostos",
                            subcategoria="Juros/Multas/IOF",
                            natureza="TARIFA_ENCARGO",
                            tipo="compra",
                            now=now,
                        )
                        if fee_created:
                            inserted_fees += 1

                elif plan.status == "missing":
                    if item.principal_amount and item.fee_amount:
                        _, princ_created = _insert_official_provisional(
                            writing,
                            merchant_cache=merchant_cache,
                            existing_prov_by_id=existing_prov_by_id,
                            seed_key=f"{item.item_key}:principal",
                            item=item,
                            doc=doc,
                            target=target,
                            amount_dec=Decimal(item.principal_amount),
                            vendor_name=item.popular_vendor,
                            categoria=item.categoria,
                            subcategoria=item.subcategoria,
                            natureza=item.natureza,
                            tipo=item.tipo,
                            now=now,
                        )
                        _, fee_created = _insert_official_provisional(
                            writing,
                            merchant_cache=merchant_cache,
                            existing_prov_by_id=existing_prov_by_id,
                            seed_key=f"{item.item_key}:fee",
                            item=item,
                            doc=doc,
                            target=target,
                            amount_dec=Decimal(item.fee_amount),
                            vendor_name=f"Encargos Cartão — {item.popular_vendor}",
                            categoria="Finanças/Impostos",
                            subcategoria="Juros/Multas/IOF",
                            natureza="TARIFA_ENCARGO",
                            tipo="compra",
                            now=now,
                        )
                        if princ_created:
                            inserted_new += 1
                        if fee_created:
                            inserted_fees += 1
                    else:
                        _, main_created = _insert_official_provisional(
                            writing,
                            merchant_cache=merchant_cache,
                            existing_prov_by_id=existing_prov_by_id,
                            seed_key=item.item_key,
                            item=item,
                            doc=doc,
                            target=target,
                            amount_dec=Decimal(item.amount),
                            vendor_name=item.popular_vendor,
                            categoria=item.categoria,
                            subcategoria=item.subcategoria,
                            natureza=item.natureza,
                            tipo=item.tipo,
                            now=now,
                        )
                        if main_created:
                            inserted_new += 1

    # 3. Sync checking account balance snapshots (pick widest/most recent per account, civil_day convention)
    updated_balances: list[dict[str, Any]] = []
    authoritative_bank_docs: dict[str, OfficialStatementDocument] = {}
    for doc in docs:
        if doc.kind != "bank_statement" or not doc.account_profile_key or not doc.closing_balance or not doc.closing_as_of:
            continue
        prev = authoritative_bank_docs.get(doc.account_profile_key)
        if prev is None:
            authoritative_bank_docs[doc.account_profile_key] = doc
        else:
            prev_span = (
                (date.fromisoformat(prev.period_end) - date.fromisoformat(prev.period_start)).days
                if prev.period_start and prev.period_end
                else 0
            )
            cur_span = (
                (date.fromisoformat(doc.period_end) - date.fromisoformat(doc.period_start)).days
                if doc.period_start and doc.period_end
                else 0
            )
            if (doc.closing_as_of, cur_span) >= (prev.closing_as_of or "", prev_span):
                authoritative_bank_docs[doc.account_profile_key] = doc

    for acct_key, doc in authoritative_bank_docs.items():
        close_dt = date.fromisoformat(doc.closing_as_of)  # type: ignore[arg-type]
        as_of_utc = datetime.combine(close_dt + timedelta(days=1), time.min, LOCAL_ZONE).astimezone(timezone.utc)
        close_amt = Decimal(doc.closing_balance).quantize(Decimal("0.01"))  # type: ignore[arg-type]
        with _atomic_unit(db, allow_standalone=allow_standalone) as writing:
            existing_snap = writing.account_balance_snapshots.find_one({"account_key": acct_key})
            if existing_snap and not force_balances and existing_snap.get("as_of"):
                prev_as_of = _aware_utc(existing_snap["as_of"])
                if prev_as_of > as_of_utc:
                    continue
            writing.account_balance_snapshots.update_one(
                {"account_key": acct_key},
                {
                    "$set": {
                        "amount": Decimal128(f"{close_amt:.2f}"),
                        "as_of": as_of_utc,
                        "reference_kind": "civil_day",
                        "as_of_date": close_dt.isoformat(),
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
            updated_balances.append(
                {
                    "account_key": acct_key,
                    "closing_balance": f"{close_amt:.2f}",
                    "as_of_date": close_dt.isoformat(),
                }
            )

    # 4. Sync credit card bills & linked obligations atomically (preserving 1:1 invariant)
    synced_bills: list[dict[str, Any]] = []
    bill_schema = _get_collection_schema(db, "credit_card_bills")
    obl_schema = _get_collection_schema(db, "obligations")
    bill_props = set((bill_schema or {}).get("properties") or [])
    obl_props = set((obl_schema or {}).get("properties") or [])
    bill_strict = bool(bill_schema and bill_schema.get("additionalProperties") is False)
    obl_strict = bool(obl_schema and obl_schema.get("additionalProperties") is False)
    bill_enums = (((bill_schema or {}).get("properties") or {}).get("status") or {}).get("enum") or []
    obl_enums = (((obl_schema or {}).get("properties") or {}).get("status") or {}).get("enum") or []

    for doc in docs:
        if doc.kind != "card_statement" or not doc.total_amount or not doc.due_date or not doc.competencia:
            continue
        target_key = doc.items[0].target if doc.items else f"card:{doc.institution.lower()}:{doc.card_final}"
        card_oid = targets[target_key].account_id
        if card_oid is None:
            continue
        tot = Decimal(doc.total_amount).quantize(Decimal("0.01"))
        paid = tot if doc.status == "paga" else Decimal("0.00")
        due_dt = date.fromisoformat(doc.due_date)
        due_utc = day_start(due_dt)
        paid_utc = day_start(date.fromisoformat(doc.paid_date)) if doc.paid_date else None

        with _atomic_unit(db, allow_standalone=allow_standalone) as writing:
            existing_bill = writing.credit_card_bills.find_one({"card_account_id": card_oid, "competencia": doc.competencia})
            if existing_bill is None:
                open_bal = max(Decimal("0.00"), tot - paid)
                base_stat = bill_status(open_bal, paid, due_dt, today_local)
                dias_atraso = max(0, (today_local - due_dt).days) if open_bal > 0 else 0
                bill_oid = ObjectId()
                raw_bill: dict[str, Any] = {
                    "_id": bill_oid,
                    "external_key": f"bill-{doc.doc_key}",
                    "schema_version": 2,
                    "card_account_id": card_oid,
                    "competencia": doc.competencia,
                    "data_vencimento": due_utc,
                    "data_pagamento": paid_utc if open_bal == 0 else None,
                    "saldo_anterior": Decimal128("0.00"),
                    "novos_lancamentos": Decimal128(f"{tot:.2f}"),
                    "encargos": Decimal128("0.00"),
                    "pagamentos": Decimal128(f"{paid:.2f}"),
                    "valor_pago": Decimal128(f"{paid:.2f}"),
                    "saldo_aberto": Decimal128(f"{open_bal:.2f}"),
                    "valor_total": Decimal128(f"{tot:.2f}"),
                    "status": base_stat,
                    "items": [],
                    "created_at": now,
                    "updated_at": now,
                }
                raw_obl: dict[str, Any] = {
                    "_id": ObjectId(),
                    "external_key": f"obl-{doc.doc_key}",
                    "schema_version": 2,
                    "tipo": "fatura_cartao",
                    "origem_id": bill_oid,
                    "account_id": card_oid,
                    "credor": doc.institution,
                    "descricao": f"Fatura {doc.institution} final {doc.card_final} ({doc.competencia})",
                    "competencia": doc.competencia,
                    "data_vencimento": due_utc,
                    "data_liquidacao": paid_utc,
                    "valor_original": Decimal128(f"{tot:.2f}"),
                    "valor_pago": Decimal128(f"{paid:.2f}"),
                    "saldo_aberto": Decimal128(f"{open_bal:.2f}"),
                    "principal": Decimal128(f"{tot:.2f}"),
                    "juros": Decimal128("0.00"),
                    "multa": Decimal128("0.00"),
                    "mora": Decimal128("0.00"),
                    "tarifas": Decimal128("0.00"),
                    "seguros": Decimal128("0.00"),
                    "outros_encargos": Decimal128("0.00"),
                    "encargos": Decimal128("0.00"),
                    "descontos": Decimal128("0.00"),
                    "dias_atraso": dias_atraso,
                    "moeda": "BRL",
                    "status": base_stat,
                    "source_record_ids": [],
                    "created_at": now,
                    "updated_at": now,
                }
                new_bill = _conform_document_to_schema(db, "credit_card_bills", raw_bill)
                new_obl = _conform_document_to_schema(db, "obligations", raw_obl)
                writing.credit_card_bills.insert_one(new_bill)
                try:
                    writing.obligations.insert_one(new_obl)
                except Exception:
                    if not isinstance(writing, _SessionDatabase):
                        writing.credit_card_bills.delete_one({"_id": bill_oid})
                    raise
                synced_bills.append(
                    {
                        "doc_key": doc.doc_key,
                        "competencia": doc.competencia,
                        "total_amount": f"{tot:.2f}",
                        "status": str(new_bill.get("status") or base_stat),
                    }
                )
            else:
                bill_oid = existing_bill["_id"]
                cur_paid = _dec(existing_bill.get("valor_pago", existing_bill.get("pagamentos", 0)))
                eff_paid = max(cur_paid, paid)
                eff_open = max(Decimal("0.00"), tot - eff_paid)
                base_stat = bill_status(eff_open, eff_paid, due_dt, today_local)
                eff_bill_stat = _translate_status_for_schema(base_stat, bill_enums, label="credit_card_bills.status")
                dias_atraso = max(0, (today_local - due_dt).days) if eff_open > 0 else 0

                bill_set: dict[str, Any] = {
                    "data_vencimento": due_utc,
                    "valor_total": Decimal128(f"{tot:.2f}"),
                    "saldo_aberto": Decimal128(f"{eff_open:.2f}"),
                    "novos_lancamentos": Decimal128(f"{tot:.2f}"),
                    "status": eff_bill_stat,
                    "updated_at": now,
                }
                if "pagamentos" in existing_bill or (not bill_strict or "pagamentos" in bill_props):
                    bill_set["pagamentos"] = Decimal128(f"{eff_paid:.2f}")
                if "valor_pago" in existing_bill or (not bill_strict or "valor_pago" in bill_props):
                    bill_set["valor_pago"] = Decimal128(f"{eff_paid:.2f}")
                if eff_open == 0 and paid_utc is not None:
                    if "data_pagamento" in existing_bill or (not bill_strict or "data_pagamento" in bill_props):
                        bill_set["data_pagamento"] = paid_utc
                if bill_strict:
                    bill_set = {k: v for k, v in bill_set.items() if k in bill_props}

                linked_obls = list(writing.obligations.find({"tipo": "fatura_cartao", "origem_id": bill_oid}))
                obl_update_doc: dict[str, Any] | None = None
                if len(linked_obls) == 1:
                    existing_obl = linked_obls[0]
                    obl_stat = _translate_status_for_schema(base_stat, obl_enums, label="obligations.status")
                    obl_set: dict[str, Any] = {
                        "data_vencimento": due_utc,
                        "valor_original": Decimal128(f"{tot:.2f}"),
                        "valor_pago": Decimal128(f"{eff_paid:.2f}"),
                        "saldo_aberto": Decimal128(f"{eff_open:.2f}"),
                        "principal": Decimal128(f"{tot:.2f}"),
                        "status": obl_stat,
                        "updated_at": now,
                    }
                    if "dias_atraso" in existing_obl or (not obl_strict or "dias_atraso" in obl_props):
                        obl_set["dias_atraso"] = dias_atraso
                    if eff_open == 0 and (paid_utc is not None or existing_obl.get("data_liquidacao") is not None):
                        if "data_liquidacao" in existing_obl or (not obl_strict or "data_liquidacao" in obl_props):
                            obl_set["data_liquidacao"] = paid_utc or existing_obl.get("data_liquidacao") or now
                    if obl_strict:
                        obl_set = {k: v for k, v in obl_set.items() if k in obl_props}
                    obl_update_doc = {"$set": obl_set}
                    if eff_open > 0 and "data_liquidacao" in existing_obl:
                        obl_update_doc["$unset"] = {"data_liquidacao": ""}

                prev_bill_snapshot = dict(existing_bill)
                bill_update_doc: dict[str, Any] = {"$set": bill_set}
                if eff_open > 0 and "data_pagamento" in existing_bill:
                    bill_update_doc["$unset"] = {"data_pagamento": ""}
                writing.credit_card_bills.update_one({"_id": bill_oid}, bill_update_doc)
                if obl_update_doc is not None:
                    try:
                        writing.obligations.update_one({"_id": linked_obls[0]["_id"]}, obl_update_doc)
                    except Exception:
                        if not isinstance(writing, _SessionDatabase):
                            writing.credit_card_bills.update_one({"_id": bill_oid}, {"$set": prev_bill_snapshot})
                        raise

                synced_bills.append(
                    {
                        "doc_key": doc.doc_key,
                        "competencia": doc.competencia,
                        "total_amount": f"{tot:.2f}",
                        "status": eff_bill_stat,
                    }
                )

    summary = {
        "updated_existing": updated_existing,
        "inserted_new": inserted_new,
        "inserted_fees": inserted_fees,
        "skipped_deleted_by_user": skipped_deleted,
        "skipped_ambiguous": skipped_ambiguous,
        "skipped_overlap_duplicate": skipped_overlap,
        "created_cards": created_cards,
        "updated_balances": updated_balances,
        "synced_bills": synced_bills,
    }
    with _atomic_unit(db, allow_standalone=allow_standalone) as writing:
        writing.audit_log.insert_one(
            {
                "event": "official_statements_reconciled",
                "source": "reconcile_official_statements_cli",
                "documents": [{"doc_key": d.doc_key, "filename": d.filename, "sha256": d.sha256} for d in docs],
                "summary": summary,
                "created_at": now,
            }
        )
    return summary


def format_markdown_report(
    docs: list[OfficialStatementDocument],
    plans: list[ItemMatchPlan],
    targets: dict[str, TargetResolution],
) -> str:
    lines: list[str] = ["# Relatório de Conciliação de Comprovantes Oficiais\n"]
    lines.append("## 1. Documentos Oficiais Verificados\n")
    for doc in docs:
        if doc.kind == "card_statement":
            lines.append(
                f"- **{doc.institution} Cartão final `{doc.card_final}`** (`{doc.filename}`): "
                f"competência `{doc.competencia}`, vencimento `{doc.due_date}`, "
                f"total **`R$ {doc.total_amount}`**, status **`{doc.status}`**"
                + (f" (paga em `{doc.paid_date}`)" if doc.paid_date else "")
            )
        else:
            lines.append(
                f"- **{doc.institution} Conta Corrente `{doc.account_profile_key}`** (`{doc.filename}`): "
                f"período `{doc.period_start}` a `{doc.period_end}`, "
                f"saldo inicial `R$ {doc.opening_balance}` ➔ saldo final **`R$ {doc.closing_balance}`** em `{doc.closing_as_of}`"
            )

    counts: dict[str, int] = {}
    for p in plans:
        counts[p.status] = counts.get(p.status, 0) + 1

    lines.append("\n## 2. Resumo da Conciliação\n")
    lines.append(f"- Já conciliados anteriormente: **{counts.get('already_reconciled', 0)}**")
    lines.append(f"- Conciliados por valor exato (incluindo nomes populares): **{counts.get('matched_exact', 0)}**")
    lines.append(f"- Conciliados pelo principal (`PgConta` / PIX no cartão sem juros): **{counts.get('matched_principal', 0)}**")
    lines.append(f"- Ausentes no banco (a inserir com nome popular): **{counts.get('missing', 0)}**")
    lines.append(f"- Excluídos/cancelados pelo usuário preservados: **{counts.get('skipped_deleted_by_user', 0)}**")
    lines.append(f"- Duplicatas de extratos sobrepostos ignoradas: **{counts.get('skipped_overlap_duplicate', 0)}**")
    lines.append(f"- Ambíguos preservados para revisão manual: **{counts.get('ambiguous_skip', 0)}**")
    lines.append(f"- Créditos informativos de pagamento de fatura anterior ignorados: **{counts.get('skipped_prior_payment', 0)}**\n")

    matched = [p for p in plans if p.status in {"matched_exact", "matched_principal", "already_reconciled"}]
    if matched:
        lines.append("## 3. Itens Conciliados com Registros Existentes\n")
        lines.append("| Data Oficial | Descritor Oficial | Nome Popular | Valor Oficial | Registro Existente | Valor Banco | Motivo |")
        lines.append("|---|---|---|---:|---|---:|---|")
        for p in matched:
            cand = p.candidate
            lines.append(
                f"| `{p.item.date}` | `{p.item.description}` | {p.item.popular_vendor} | `{p.item.amount}` | "
                f"{cand.display_label if cand else '-'} (`{cand.date.isoformat() if cand else '-'}`) | "
                f"`{cand.amount if cand else '-'}` | {p.reason} |"
            )
        lines.append("")

    missing = [p for p in plans if p.status == "missing"]
    if missing:
        lines.append("## 4. Itens Oficiais Ausentes (Serão Inseridos com `--apply`)\n")
        lines.append("| Data | Instrumento | Descritor Oficial | Nome Popular Curado | Valor | Categoria Canônica |")
        lines.append("|---|---|---|---|---:|---|")
        for p in missing:
            cat_str = f"{p.item.categoria} > {p.item.subcategoria}" if p.item.categoria else (p.item.natureza or p.item.tipo)
            lines.append(
                f"| `{p.item.date}` | `{p.item.target}` | `{p.item.description}` | **{p.item.popular_vendor}** | "
                f"`{p.item.amount}` | {cat_str} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"
