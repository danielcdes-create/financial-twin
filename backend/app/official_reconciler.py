"""Reconciliation engine for official bank statements and credit card invoices.

Matches official statement rows against existing MongoDB records (`transactions`
and `provisional_transactions`), accounting for popular establishment names,
acquirer prefixes, weekend settlement lags, and PgConta/PIX card financing fees.
Preserves user-chosen popular names and validated categories, obeys strict
collection `$jsonSchema` validators (`transactions`, `accounts`), and records
idempotent links in `official_statement_reconciliations`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from bson import ObjectId
from bson.decimal128 import Decimal128

from app.account_profiles import legacy_accounts_by_profile, profile_for_legacy
from app.economic_policy import duplicate_of_ledger
from app.financial_time import LOCAL_ZONE, day_start, local_date
from app.official_statement_parsers import OfficialStatementDocument, OfficialStatementItem
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


def _schema_allows_document(db: Any, collection_name: str, document: dict[str, Any]) -> bool:
    """Verify required keys and additionalProperties against MongoDB $jsonSchema before write."""
    raw_db = getattr(db, "db", db)
    if not hasattr(raw_db, "list_collections"):
        return True
    try:
        info = next(raw_db.list_collections(filter={"name": collection_name}), None)
    except Exception:
        return True
    schema = (((info or {}).get("options") or {}).get("validator") or {}).get("$jsonSchema")
    if not isinstance(schema, dict):
        return True
    required = set(schema.get("required") or [])
    if not required.issubset(document.keys()):
        return False
    if schema.get("additionalProperties") is False:
        allowed = set((schema.get("properties") or {}).keys())
        if not set(document.keys()).issubset(allowed):
            return False
    return True


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
    status: str  # "already_reconciled" | "matched_exact" | "matched_principal" | "missing" | "skipped_prior_payment"
    candidate: ExistingCandidate | None = None
    fee_candidate: ExistingCandidate | None = None
    score: int = 0
    reason: str = ""
    insert_missing_fee: bool = False


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


def load_existing_candidates(
    db: Any,
    docs: list[OfficialStatementDocument],
    targets: dict[str, TargetResolution],
) -> list[ExistingCandidate]:
    all_dates: list[date] = []
    all_rec_keys: list[str] = []
    for doc in docs:
        for item in doc.items:
            if item.skip_insert:
                continue
            all_dates.append(date.fromisoformat(item.date))
            all_dates.append(date.fromisoformat(item.purchase_date_hint))
            all_rec_keys.append(item.item_key)
            all_rec_keys.append(f"{item.item_key}:principal")
            if item.fee_amount:
                all_rec_keys.append(f"{item.item_key}:fee")
    if not all_dates:
        return []

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

    tx_query = (
        {"$or": [{"data_evento": window}, {"_id": {"$in": linked_tx_oids}}]}
        if linked_tx_oids
        else {"data_evento": window}
    )
    tx_rows = list(db.transactions.find(tx_query))
    tx_id_strs = {str(r["_id"]) for r in tx_rows}

    prov_query: dict[str, Any] = {"status": {"$in": ["provisional", "enriched", "confirmed"]}}
    if linked_prov_ids:
        prov_query["$or"] = [{"data_evento": window}, {"provisional_id": {"$in": linked_prov_ids}}]
    else:
        prov_query["data_evento"] = window

    prov_rows = [
        r
        for r in db.provisional_transactions.find(prov_query)
        if not duplicate_of_ledger(r, tx_id_strs)
    ]

    merchant_ids = {
        r.get(field_name)
        for r in (*tx_rows, *prov_rows)
        for field_name in ("merchant_id", "platform_id")
        if r.get(field_name)
    }
    merchants_by_id: dict[Any, dict[str, Any]] = {}
    if merchant_ids:
        for mdoc in db.merchants.find({"_id": {"$in": list(merchant_ids)}}):
            merchants_by_id[mdoc["_id"]] = mdoc

    account_lookup = _build_account_lookup(db, targets)
    profile_inst = {
        "itau": "Itaú",
        "santander": "Santander",
        "inter": "Inter",
        "btg": "BTG",
        "mercado_pago": "Mercado Pago",
    }

    candidates: list[ExistingCandidate] = []
    for source, rows in (("ledger", tx_rows), ("capture", prov_rows)):
        for row in rows:
            if not row.get("data_evento") or row.get("valor") is None:
                continue
            identity = str(row.get("provisional_id") or row["_id"])
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
                    doc_id=row["_id"],
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


def _score_candidate_pair(
    item: OfficialStatementItem,
    doc: OfficialStatementDocument,
    cand: ExistingCandidate,
) -> tuple[int, str, str] | None:
    item_amt = Decimal(item.amount).quantize(Decimal("0.01"))
    princ_amt = Decimal(item.principal_amount).quantize(Decimal("0.01")) if item.principal_amount else None

    if cand.amount == item_amt:
        mode = "matched_exact"
    elif princ_amt is not None and cand.amount == princ_amt:
        mode = "matched_principal"
    else:
        return None

    item_dt = date.fromisoformat(item.date)
    hint_dt = date.fromisoformat(item.purchase_date_hint)
    day_gap = min(abs((cand.date - item_dt).days), abs((cand.date - hint_dt).days))
    max_gap = 6 if item.prior_cycle_installment else 4
    if day_gap > max_gap:
        if item.prior_cycle_installment and cand.date.strftime("%Y-%m") == item.statement_month:
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
    context_hit = bool(context_set & cand_tokens) or (
        item.categoria is not None and cand.categoria == item.categoria
    )

    if not same_target and not unassigned:
        if cand.source == "ledger" and mode != "matched_principal":
            return None
        if not (exact_desc or brand_hit or strong_text):
            return None

    if mode == "matched_principal" and not (exact_desc or brand_hit or strong_text or context_hit):
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
        reasons.append("mesma categoria/contexto e valor exato")
    elif item_brands and cand.brand_keys and not (item_brands & cand.brand_keys):
        if day_gap >= 1 or not same_target:
            return None
        score -= 35
        reasons.append("estabelecimento divergente (mesmo valor/data no mesmo instrumento)")

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

    return score, mode, "; ".join(reasons)


def build_reconciliation_plan(
    db: Any,
    docs: list[OfficialStatementDocument],
) -> tuple[list[ItemMatchPlan], dict[str, TargetResolution]]:
    targets = resolve_targets(db, docs)
    candidates = load_existing_candidates(db, docs, targets)

    used_candidate_ids: set[str] = set()
    plans_by_key: dict[str, ItemMatchPlan] = {}
    doc_by_key = {doc.doc_key: doc for doc in docs}

    active_items: list[OfficialStatementItem] = []
    for doc in docs:
        for item in doc.items:
            if item.skip_insert:
                plans_by_key[item.item_key] = ItemMatchPlan(
                    item=item,
                    doc=doc,
                    status="skipped_prior_payment",
                    reason="Crédito de pagamento da fatura anterior (informativo na fatura).",
                )
            else:
                active_items.append(item)

    # Pass 1: Already reconciled idempotency check
    for item in active_items:
        _, det_prov_id = deterministic_ids(item.item_key)
        _, det_princ_prov_id = deterministic_ids(f"{item.item_key}:principal")
        existing = next(
            (
                c
                for c in candidates
                if c.identity not in used_candidate_ids
                and (
                    item.item_key in c.reconciled_keys
                    or f"{item.item_key}:principal" in c.reconciled_keys
                    or c.identity in {det_prov_id, det_princ_prov_id}
                )
            ),
            None,
        )
        if existing is not None:
            used_candidate_ids.add(existing.identity)
            fee_cand = None
            insert_fee = False
            if item.fee_amount and existing.amount != Decimal(item.amount):
                _, det_fee_prov_id = deterministic_ids(f"{item.item_key}:fee")
                fee_cand = next(
                    (
                        c
                        for c in candidates
                        if c.identity not in used_candidate_ids
                        and (f"{item.item_key}:fee" in c.reconciled_keys or c.identity == det_fee_prov_id)
                    ),
                    None,
                )
                if fee_cand is not None:
                    used_candidate_ids.add(fee_cand.identity)
                else:
                    insert_fee = True
            plans_by_key[item.item_key] = ItemMatchPlan(
                item=item,
                doc=doc_by_key[item.doc_key],
                status="already_reconciled",
                candidate=existing,
                fee_candidate=fee_cand,
                score=1000,
                reason="Já conciliado anteriormente com comprovante oficial.",
                insert_missing_fee=insert_fee,
            )

    # Pass 2: Optimal greedy 1-to-1 matching
    remaining = [it for it in active_items if it.item_key not in plans_by_key]
    scored_pairs: list[tuple[int, int, str, int, int, str, str]] = []
    for item_idx, item in enumerate(remaining):
        doc = doc_by_key[item.doc_key]
        item_dt = date.fromisoformat(item.purchase_date_hint)
        for cand_idx, cand in enumerate(candidates):
            if cand.identity in used_candidate_ids:
                continue
            scored = _score_candidate_pair(item, doc, cand)
            if scored is None:
                continue
            sc, mode, reason = scored
            gap = abs((cand.date - item_dt).days)
            scored_pairs.append((sc, -gap, cand.identity, item_idx, cand_idx, mode, reason))

    scored_pairs.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
    matched_item_indices: set[int] = set()

    for sc, _neg_gap, _cid, item_idx, cand_idx, mode, reason in scored_pairs:
        if item_idx in matched_item_indices:
            continue
        cand = candidates[cand_idx]
        if cand.identity in used_candidate_ids:
            continue
        item = remaining[item_idx]
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
                ),
                None,
            )
            if fee_cand is not None:
                used_candidate_ids.add(fee_cand.identity)
            else:
                insert_fee = True

        plans_by_key[item.item_key] = ItemMatchPlan(
            item=item,
            doc=doc_by_key[item.doc_key],
            status=mode,
            candidate=cand,
            fee_candidate=fee_cand,
            score=sc,
            reason=reason,
            insert_missing_fee=insert_fee,
        )

    for item in remaining:
        if item.item_key not in plans_by_key:
            plans_by_key[item.item_key] = ItemMatchPlan(
                item=item,
                doc=doc_by_key[item.doc_key],
                status="missing",
                reason="Ausente no banco; será inserido com nome popular e categoria canônica.",
            )

    ordered_plans = [plans_by_key[it.item_key] for doc in docs for it in doc.items]
    return ordered_plans, targets


def _ensure_merchant(db: Any, name: str, raw_descriptor: str | None, now: datetime) -> ObjectId | None:
    clean = (name or "").strip()
    if not clean:
        return None
    norm_key = normalize_merchant_text(clean)
    existing = db.merchants.find_one({"nome_padrao": clean})
    if not existing:
        for mdoc in db.merchants.find({"nome_padrao": {"$nin": [None, ""]}}):
            if normalize_merchant_text(mdoc.get("nome_padrao")) == norm_key:
                existing = mdoc
                break
    if not existing:
        new_doc = {
            "_id": ObjectId(),
            "external_key": f"merchant-popular-{uuid5(NAMESPACE_URL, norm_key)}",
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
        db.merchants.insert_one(new_doc)
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
            db.merchants.update_one({"_id": existing["_id"]}, {"$set": {"aliases": aliases, "updated_at": now}})
    return existing["_id"]


def _ensure_platform(db: Any, platform_name: str | None, now: datetime) -> ObjectId | None:
    if not platform_name:
        return None
    ident = platform_identity(platform_name)
    canonical = ident[1] if ident else platform_name.strip()
    ext_key = ident[0] if ident else f"platform-user-{uuid4()}"
    found = db.merchants.find_one({"external_key": ext_key}) if ident else None
    if not found:
        found = db.merchants.find_one({"nome_padrao": canonical})
    if found:
        return found["_id"]
    doc = {
        "_id": ObjectId(),
        "external_key": ext_key,
        "schema_version": 2,
        "created_at": now,
        "updated_at": now,
        "tipo_entidade": "marca",
        "nome_padrao": canonical,
        "aliases": [],
    }
    db.merchants.insert_one(doc)
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


def _insert_official_provisional(
    db: Any,
    *,
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
) -> str:
    event_id, provisional_id = deterministic_ids(seed_key)
    existing_prov = db.provisional_transactions.find_one({"provisional_id": provisional_id})
    if existing_prov:
        _record_reconciliation_link(
            db,
            seed_key=seed_key,
            movement_source="capture",
            movement_identity=provisional_id,
            movement_doc_id=existing_prov["_id"],
            item=item,
            doc=doc,
            match_status="inserted_official",
            now=now,
        )
        return provisional_id

    evt_date = date.fromisoformat(item.date)
    stamp = datetime.combine(evt_date, time(12, 0), LOCAL_ZONE).astimezone(timezone.utc)

    merchant_oid = None
    platform_oid = None
    if not item.technical_transit and not item.own_transfer and item.kind not in {"bill_payment", "bill_payment_credit"}:
        merchant_oid = _ensure_merchant(db, vendor_name, item.description, now)
        platform_oid = _ensure_platform(db, item.platform, now)

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
    db.raw_events.insert_one(
        {
            "event_id": event_id,
            "source_type": "manual",
            "source_provider": "official_statement_reconciliation",
            "captured_at": now,
            "received_at": now,
            "processing_status": "confirmed",
            "linked_transaction_id": inserted_id,
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
            "parser_version": "official-statement-reconciler-v1",
            "media_status": "none",
        }
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
    return provisional_id


def apply_reconciliation_plan(
    db: Any,
    docs: list[OfficialStatementDocument],
    plans: list[ItemMatchPlan],
    targets: dict[str, TargetResolution],
    *,
    force_balances: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    created_cards: list[str] = []

    # 1. Ensure missing credit cards exist in db.accounts (strictly obeying accounts-validator.json)
    for res in targets.values():
        if res.kind == "card" and res.account_id is None:
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
            db.accounts.insert_one(card_doc)
            profile_key = f"card-profile:{card_oid}"
            db.account_profiles.update_one(
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

    updated_existing = 0
    inserted_new = 0
    inserted_fees = 0

    # 2. Apply item reconciliations & missing insertions
    for plan in plans:
        item = plan.item
        doc = plan.doc
        if plan.status == "skipped_prior_payment":
            continue
        target = targets[item.target]

        if plan.status in {"already_reconciled", "matched_exact", "matched_principal"} and plan.candidate is not None:
            cand = plan.candidate
            raw_doc = cand.document
            collection = db.transactions if cand.source == "ledger" else db.provisional_transactions

            if plan.status != "already_reconciled":
                set_fields: dict[str, Any] = {"updated_at": now}

                # Preserve user's popular name & teach db.merchants the official bank alias
                if raw_doc.get("merchant_id"):
                    mdoc = db.merchants.find_one({"_id": raw_doc["merchant_id"]})
                    if mdoc and mdoc.get("nome_padrao"):
                        _ensure_merchant(db, mdoc["nome_padrao"], item.description, now)
                elif not item.technical_transit and not item.own_transfer and item.kind not in {"bill_payment", "bill_payment_credit"}:
                    preferred_vendor = cand.popular_name or item.popular_vendor
                    m_oid = _ensure_merchant(db, preferred_vendor, item.description, now)
                    if m_oid is not None:
                        set_fields["merchant_id"] = m_oid

                if not raw_doc.get("platform_id") and item.platform:
                    p_oid = _ensure_platform(db, item.platform, now)
                    if p_oid is not None:
                        set_fields["platform_id"] = p_oid

                if cand.source == "capture":
                    if raw_doc.get("status") in {"provisional", "enriched"}:
                        set_fields["status"] = "confirmed"
                        set_fields["confirmed_at"] = now
                    if not raw_doc.get("account_profile_id") and not raw_doc.get("account_id"):
                        if target.kind == "checking" and target.profile_key:
                            set_fields["account_profile_id"] = target.profile_key
                        elif target.kind == "card" and target.account_id:
                            set_fields["account_id"] = target.account_id
                    if not raw_doc.get("categoria_sugerida") and item.categoria:
                        set_fields["categoria_sugerida"] = item.categoria
                    if not raw_doc.get("subcategoria") and item.subcategoria:
                        set_fields["subcategoria"] = item.subcategoria
                else:
                    # Strictly schema-compliant fields for db.transactions
                    if raw_doc.get("status_conciliacao") != "confirmado":
                        set_fields["status_conciliacao"] = "confirmado"
                    if not raw_doc.get("categoria") and item.categoria:
                        set_fields["categoria"] = item.categoria
                    if not raw_doc.get("subcategoria") and item.subcategoria:
                        set_fields["subcategoria"] = item.subcategoria

                collection.update_one({"_id": cand.doc_id}, {"$set": set_fields})
                _record_reconciliation_link(
                    db,
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
                    db,
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
                _insert_official_provisional(
                    db,
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
                inserted_fees += 1

        elif plan.status == "missing":
            if item.principal_amount and item.fee_amount:
                _insert_official_provisional(
                    db,
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
                _insert_official_provisional(
                    db,
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
                inserted_new += 1
                inserted_fees += 1
            else:
                _insert_official_provisional(
                    db,
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
                inserted_new += 1

    # 3. Sync checking account balance snapshots (civil_day convention)
    updated_balances: list[dict[str, Any]] = []
    for doc in docs:
        if doc.kind != "bank_statement" or not doc.account_profile_key or not doc.closing_balance or not doc.closing_as_of:
            continue
        close_dt = date.fromisoformat(doc.closing_as_of)
        as_of_utc = datetime.combine(close_dt + timedelta(days=1), time.min, LOCAL_ZONE).astimezone(timezone.utc)
        close_amt = Decimal(doc.closing_balance).quantize(Decimal("0.01"))
        existing_snap = db.account_balance_snapshots.find_one({"account_key": doc.account_profile_key})
        if existing_snap and not force_balances and existing_snap.get("as_of"):
            prev_as_of = _aware_utc(existing_snap["as_of"])
            if prev_as_of > as_of_utc:
                continue
        db.account_balance_snapshots.update_one(
            {"account_key": doc.account_profile_key},
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
                "account_key": doc.account_profile_key,
                "closing_balance": f"{close_amt:.2f}",
                "as_of_date": close_dt.isoformat(),
            }
        )

    # 4. Sync credit card bills & linked obligations
    synced_bills: list[dict[str, Any]] = []
    for doc in docs:
        if doc.kind != "card_statement" or not doc.total_amount or not doc.due_date or not doc.competencia:
            continue
        target_key = doc.items[0].target if doc.items else f"card:{doc.institution.lower()}:{doc.card_final}"
        card_oid = targets[target_key].account_id
        if card_oid is None:
            continue
        tot = Decimal(doc.total_amount).quantize(Decimal("0.01"))
        paid = tot if doc.status == "paga" else Decimal("0.00")
        open_bal = tot - paid
        bill_stat = "paga" if open_bal == 0 else "aberta"
        due_utc = day_start(date.fromisoformat(doc.due_date))

        existing_bill = db.credit_card_bills.find_one({"card_account_id": card_oid, "competencia": doc.competencia})
        if existing_bill is None:
            bill_oid = ObjectId()
            new_bill = {
                "_id": bill_oid,
                "external_key": f"bill-{doc.doc_key}",
                "schema_version": 2,
                "card_account_id": card_oid,
                "competencia": doc.competencia,
                "data_vencimento": due_utc,
                "valor_total": Decimal128(f"{tot:.2f}"),
                "valor_pago": Decimal128(f"{paid:.2f}"),
                "saldo_aberto": Decimal128(f"{open_bal:.2f}"),
                "novos_lancamentos": Decimal128(f"{tot:.2f}"),
                "encargos": Decimal128("0.00"),
                "moeda": "BRL",
                "status": bill_stat,
                "items": [],
                "created_at": now,
                "updated_at": now,
            }
            new_obl = {
                "_id": ObjectId(),
                "external_key": f"obl-{doc.doc_key}",
                "schema_version": 2,
                "tipo": "fatura_cartao",
                "origem_id": bill_oid,
                "competencia": doc.competencia,
                "data_vencimento": due_utc,
                "valor_original": Decimal128(f"{tot:.2f}"),
                "valor_pago": Decimal128(f"{paid:.2f}"),
                "saldo_aberto": Decimal128(f"{open_bal:.2f}"),
                "principal": Decimal128(f"{tot:.2f}"),
                "moeda": "BRL",
                "status": bill_stat,
                "created_at": now,
                "updated_at": now,
            }
            if _schema_allows_document(db, "credit_card_bills", new_bill) and _schema_allows_document(
                db, "obligations", new_obl
            ):
                db.credit_card_bills.insert_one(new_bill)
                db.obligations.insert_one(new_obl)
                synced_bills.append(
                    {"doc_key": doc.doc_key, "competencia": doc.competencia, "total_amount": f"{tot:.2f}", "status": bill_stat}
                )
        else:
            bill_oid = existing_bill["_id"]
            cur_paid = _dec(existing_bill.get("valor_pago", existing_bill.get("pagamentos", 0)))
            eff_paid = max(cur_paid, paid)
            eff_open = max(Decimal("0.00"), tot - eff_paid)
            eff_stat = "paga" if eff_open == 0 else ("parcialmente_paga" if eff_paid > 0 else "aberta")
            bill_set: dict[str, Any] = {
                "data_vencimento": due_utc,
                "valor_total": Decimal128(f"{tot:.2f}"),
                "saldo_aberto": Decimal128(f"{eff_open:.2f}"),
                "novos_lancamentos": Decimal128(f"{tot:.2f}"),
                "status": eff_stat,
                "updated_at": now,
            }
            if "valor_pago" in existing_bill or "pagamentos" not in existing_bill:
                bill_set["valor_pago"] = Decimal128(f"{eff_paid:.2f}")
            db.credit_card_bills.update_one({"_id": bill_oid}, {"$set": bill_set})
            linked_obls = list(db.obligations.find({"tipo": "fatura_cartao", "origem_id": bill_oid}))
            if len(linked_obls) == 1:
                db.obligations.update_one(
                    {"_id": linked_obls[0]["_id"]},
                    {
                        "$set": {
                            "data_vencimento": due_utc,
                            "valor_original": Decimal128(f"{tot:.2f}"),
                            "valor_pago": Decimal128(f"{eff_paid:.2f}"),
                            "saldo_aberto": Decimal128(f"{eff_open:.2f}"),
                            "principal": Decimal128(f"{tot:.2f}"),
                            "status": eff_stat,
                            "updated_at": now,
                        }
                    },
                )
            synced_bills.append(
                {"doc_key": doc.doc_key, "competencia": doc.competencia, "total_amount": f"{tot:.2f}", "status": eff_stat}
            )

    summary = {
        "updated_existing": updated_existing,
        "inserted_new": inserted_new,
        "inserted_fees": inserted_fees,
        "created_cards": created_cards,
        "updated_balances": updated_balances,
        "synced_bills": synced_bills,
    }
    db.audit_log.insert_one(
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
