"""Canonical intermediary platforms and frequency-based historical capture pre-fill."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
import re
from typing import Any
import unicodedata

from bson.decimal128 import Decimal128

from app.account_profiles import account_profiles
from app.taxonomy import CURATED_SUBCATEGORIES, NATURES, category_subcategories


PLATFORMS = {
    "ifood": ("merchant-ifood", "iFood"),
    "99food": ("merchant-99food", "99Food"),
    "rappi": ("merchant-rappi", "Rappi"),
    "sem_parar": ("merchant-sem-parar", "Sem Parar"),
    "cappta": ("merchant-cappta", "Cappta"),
}

GENERIC_PLATFORM_TOKENS = {
    "ifd",
    "ifood",
    "99food",
    "99foods",
    "rappi",
    "sem",
    "parar",
    "cappta",
    "pag",
    "pagseguro",
    "mp",
    "mercadopago",
    "picpay",
    "stone",
    "cielo",
    "rede",
    "getnet",
    "sumup",
    "ton",
}


def platform_identity(value: str | None) -> tuple[str, str] | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if re.match(r"^(?:ifd\s*\*|ifood\b)", cleaned, flags=re.IGNORECASE):
        return PLATFORMS["ifood"]
    if re.match(r"^99\s*foods?\b", cleaned, flags=re.IGNORECASE):
        return PLATFORMS["99food"]
    if re.match(r"^rappi(?:\s*\*|\b)", cleaned, flags=re.IGNORECASE):
        return PLATFORMS["rappi"]
    if re.match(r"^sem\s*parar(?:\s*\*|\b)", cleaned, flags=re.IGNORECASE):
        return PLATFORMS["sem_parar"]
    if re.match(r"^cappta(?:\s*\*|\b)", cleaned, flags=re.IGNORECASE):
        return PLATFORMS["cappta"]
    return None


def has_vendor_tokens_beyond_platform(value: str | None, platform_name: str | None = None) -> bool:
    plain = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").casefold()
    tokens = [tok for tok in re.sub(r"[^a-z0-9 ]", " ", plain).split() if tok]
    if not tokens:
        return False
    platform_plain = unicodedata.normalize("NFKD", str(platform_name or "")).encode("ascii", "ignore").decode("ascii").casefold()
    extra_tokens = set(re.sub(r"[^a-z0-9 ]", " ", platform_plain).split())
    excluded = GENERIC_PLATFORM_TOKENS | extra_tokens
    remaining = [tok for tok in tokens if tok not in excluded]
    return any(len(tok) >= 2 for tok in remaining)


CHANNEL_STOPWORDS = GENERIC_PLATFORM_TOKENS | {
    "banco",
    "bank",
    "santander",
    "itau",
    "bradesco",
    "nubank",
    "inter",
    "caixa",
    "brasil",
    "pagamento",
    "transferencia",
    "compra",
    "debito",
    "credito",
    "pix",
    "ted",
    "doc",
}


def _norm(value: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", plain)).strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _stamp_key(row: dict[str, Any]) -> float:
    for field in ("data_evento", "confirmed_at", "updated_at", "created_at"):
        val = row.get(field)
        if isinstance(val, datetime):
            aware = val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val.astimezone(timezone.utc)
            return aware.timestamp()
    return 0.0


def _safe_find(collection, query: dict[str, Any], limit: int = 800) -> list[dict[str, Any]]:
    if collection is None or not hasattr(collection, "find"):
        return []
    try:
        cursor = collection.find(query)
        if hasattr(cursor, "sort"):
            try:
                cursor = cursor.sort([("data_evento", -1)])
            except Exception:
                pass
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(limit)
        rows = [row for row in cursor if isinstance(row, dict)]
    except Exception:
        return []
    rows.sort(key=_stamp_key, reverse=True)
    return rows[:limit]


def _safe_category_subcategories(db) -> dict[str, list[str]]:
    try:
        return category_subcategories(db)
    except Exception:
        return {k: list(v) for k, v in CURATED_SUBCATEGORIES.items()}


def _selectable_account_keys(db) -> set[str]:
    try:
        return {
            str(profile["key"])
            for profile in account_profiles(db)
            if profile.get("key") and not profile.get("archived") and profile.get("status") != "encerrada"
        }
    except Exception:
        return set()


def _merchant_catalog(db):
    merchants = _safe_find(getattr(db, "merchants", None), {}, limit=1500)
    by_id: dict[str, dict[str, Any]] = {}
    platform_ids: set[str] = set()
    platforms_by_key: dict[str, dict[str, Any]] = {}
    builtin_ext = {ext for ext, _ in PLATFORMS.values()}
    builtin_names = {_norm(name): (ext, name) for ext, name in PLATFORMS.values()}

    for item in merchants:
        mid = str(item.get("_id") or "")
        name = str(item.get("nome_padrao") or "").strip()
        if not mid or not name:
            continue
        by_id[mid] = item
        ext = str(item.get("external_key") or "")
        nkey = _norm(name)
        if ext in builtin_ext or ext.startswith("platform-user-") or nkey in builtin_names:
            platform_ids.add(mid)
            platforms_by_key[nkey] = item

    return by_id, platform_ids, platforms_by_key


def _detect_platform(raw: str, platforms_by_key: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None, set[str]]:
    detected = platform_identity(raw)
    if detected:
        ext, canonical_name = detected
        nkey = _norm(canonical_name)
        doc = platforms_by_key.get(nkey)
        p_tokens = set(nkey.split()) | {tok for tok in GENERIC_PLATFORM_TOKENS if tok.startswith(nkey[:3])}
        return doc, canonical_name, p_tokens

    source_tokens = _norm(raw).split()
    if not source_tokens:
        return None, None, set()
    for length in (2, 1):
        if len(source_tokens) >= length:
            prefix = " ".join(source_tokens[:length])
            if prefix in platforms_by_key:
                doc = platforms_by_key[prefix]
                name = str(doc.get("nome_padrao") or "").strip()
                return doc, name, set(prefix.split())
    return None, None, set()


def _store_tokens(raw_key: str, extra_platform_tokens: set[str] = frozenset()) -> list[str]:
    excluded = CHANNEL_STOPWORDS | extra_platform_tokens
    return [tok for tok in raw_key.split() if tok and tok not in excluded]


def _pick_winner(votes: dict[str, float], minimum_weight: float = 0.5) -> str | None:
    if not votes:
        return None
    best_val, best_score = max(votes.items(), key=lambda item: item[1])
    return best_val if best_score >= minimum_weight else None


def _row_flow(row: dict[str, Any]) -> str:
    flow = str(row.get("fluxo") or "").strip()
    if flow in {"entrada", "saida"}:
        return flow
    val = _decimal(row.get("valor"))
    return "saida" if val is None or val < 0 else "entrada"


def _row_has_user_review(row: dict[str, Any]) -> bool:
    if row.get("merchant_id") or row.get("platform_id") or row.get("editado_em"):
        return True
    if row.get("status_revisao") == "validado":
        return True
    if row.get("provisional_id") and int(row.get("revision") or 1) > 1:
        return True
    if row.get("subcategoria") or row.get("localidade") or row.get("metodo_pagamento"):
        return True
    return False


def historical_capture_profile(
    db,
    document: dict[str, Any],
    *,
    require_reviewed: bool = False,
    catalog_cache: tuple | None = None,
) -> dict[str, Any]:
    """Infer pre-fillable fields for a capture from confirmed/reviewed history."""
    if not document or document.get("natureza") == "TRANSFERENCIA":
        return {}
    if (document.get("transfer") or {}).get("from_account_key"):
        return {}
    raw = str(
        document.get("merchant_raw")
        or document.get("estabelecimento_original")
        or document.get("descricao_original")
        or ""
    ).strip()
    source_key = _norm(raw)
    if not source_key:
        return {}

    by_id, platform_ids, platforms_by_key = catalog_cache or _merchant_catalog(db)
    detected_platform_doc, detected_platform_name, plat_tokens = _detect_platform(raw, platforms_by_key)
    store_tokens = _store_tokens(source_key, plat_tokens)
    has_specific_store = any(len(tok) >= 2 for tok in store_tokens)

    flow = _row_flow(document)
    current_prov_id = document.get("provisional_id")
    current_id = document.get("_id")
    source_provider = str(document.get("source_provider") or "").strip()
    instrument_hint = str(document.get("instrument_hint") or "").strip()

    confirmed_captures = _safe_find(getattr(db, "provisional_transactions", None), {"status": "confirmed"}, limit=600)
    active_edited = [
        row
        for row in _safe_find(getattr(db, "provisional_transactions", None), {"status": "provisional"}, limit=200)
        + _safe_find(getattr(db, "provisional_transactions", None), {"status": "enriched"}, limit=200)
        if _row_has_user_review(row)
    ]
    ledger_rows = _safe_find(getattr(db, "transactions", None), {}, limit=800)

    candidates: list[tuple[dict[str, Any], bool]] = []
    for row in confirmed_captures + active_edited:
        if current_prov_id and row.get("provisional_id") == current_prov_id:
            continue
        if require_reviewed and not _row_has_user_review(row):
            continue
        candidates.append((row, True))
    for row in ledger_rows:
        if current_id is not None and row.get("_id") == current_id:
            continue
        if require_reviewed and not _row_has_user_review(row):
            continue
        candidates.append((row, False))

    for row, _ in candidates:
        pid = str(row.get("platform_id") or "")
        if pid and pid in by_id:
            platform_ids.add(pid)
            platforms_by_key[_norm(by_id[pid].get("nome_padrao"))] = by_id[pid]

    if not detected_platform_name:
        detected_platform_doc, detected_platform_name, plat_tokens = _detect_platform(raw, platforms_by_key)
        store_tokens = _store_tokens(source_key, plat_tokens)
        has_specific_store = any(len(tok) >= 2 for tok in store_tokens)

    matched_rows: list[tuple[dict[str, Any], float, bool]] = []
    for idx, (row, is_capture) in enumerate(candidates):
        if _row_flow(row) != flow:
            continue
        cand_raw = str(
            row.get("merchant_raw")
            or row.get("estabelecimento_original")
            or row.get("descricao_original")
            or ""
        ).strip()
        cand_key = _norm(cand_raw)
        if not cand_key:
            continue

        weight = 0.0
        if cand_key == source_key:
            weight = 4.0
        elif has_specific_store and len(source_key) >= 6 and len(cand_key) >= 6:
            cand_store = _store_tokens(cand_key, plat_tokens)
            if cand_store and store_tokens:
                first_s, first_c = store_tokens[0], cand_store[0]
                prefix_ok = (
                    min(len(first_s), len(first_c)) >= 3
                    and (first_c.startswith(first_s) or first_s.startswith(first_c))
                )
                if prefix_ok and (cand_key.startswith(source_key) or source_key.startswith(cand_key)):
                    weight = 2.4
                elif prefix_ok:
                    ratio = SequenceMatcher(None, source_key, cand_key).ratio()
                    if ratio >= 0.88:
                        weight = 2.0 * ratio

        if weight > 0:
            if _row_has_user_review(row):
                weight += 1.5
            if is_capture:
                weight += 0.4
            if source_provider and str(row.get("source_provider") or "").strip() == source_provider:
                weight += 0.4
            weight += 0.25 / (1.0 + idx * 0.08)
            matched_rows.append((row, weight, is_capture))

    # Match Path 3: Platform prefix + store token prefix matching a known vendor in history
    if has_specific_store and len(store_tokens[0]) >= 3:
        prefix_query = " ".join(store_tokens)
        matching_vendor_ids: set[str] = set()
        for mid, mdoc in by_id.items():
            if mid in platform_ids:
                continue
            vname_key = _norm(mdoc.get("nome_padrao"))
            vtokens = vname_key.split()
            if not vtokens:
                continue
            if (
                vname_key.startswith(prefix_query)
                or prefix_query.startswith(vname_key)
                or (
                    len(store_tokens) == 1
                    and len(store_tokens[0]) >= 3
                    and vtokens[0].startswith(store_tokens[0])
                )
            ):
                matching_vendor_ids.add(mid)

        if matching_vendor_ids:
            vendor_history_rows: list[tuple[dict[str, Any], float, bool]] = []
            vendor_Platform_hits: set[str] = set()
            vendor_counts: dict[str, int] = defaultdict(int)
            for idx, (row, is_capture) in enumerate(candidates):
                if _row_flow(row) != flow:
                    continue
                rmid = str(row.get("merchant_id") or "")
                if rmid not in matching_vendor_ids:
                    continue
                vendor_counts[rmid] += 1
                row_plat_id = str(row.get("platform_id") or "")
                row_plat_name = (
                    str(by_id[row_plat_id].get("nome_padrao") or "")
                    if row_plat_id in by_id
                    else str(row.get("plataforma") or "")
                )
                same_platform = bool(
                    detected_platform_name and _norm(row_plat_name) == _norm(detected_platform_name)
                )
                if same_platform:
                    vendor_Platform_hits.add(rmid)
                w = 2.6 if same_platform else 1.4
                w += 0.2 / (1.0 + idx * 0.1)
                vendor_history_rows.append((row, w, is_capture))

            # Accept vendor prefix history when either:
            # (a) the vendor was used with the same platform, or
            # (b) only one vendor in history matches the store prefix, or
            # (c) one vendor dominates (>= 75% of historical occurrences for that prefix)
            total_prefix_occurrences = sum(vendor_counts.values())
            dominant_vendor_id = None
            if len(vendor_Platform_hits) == 1:
                dominant_vendor_id = next(iter(vendor_Platform_hits))
            elif len(vendor_counts) == 1:
                dominant_vendor_id = next(iter(vendor_counts))
            elif total_prefix_occurrences > 0:
                top_vid, top_count = max(vendor_counts.items(), key=lambda item: item[1])
                if top_count / total_prefix_occurrences >= 0.75:
                    dominant_vendor_id = top_vid

            if dominant_vendor_id:
                seen_ids = {id(r) for r, _, _ in matched_rows}
                for row, w, is_capture in vendor_history_rows:
                    if str(row.get("merchant_id") or "") == dominant_vendor_id and id(row) not in seen_ids:
                        matched_rows.append((row, w, is_capture))

    if not matched_rows:
        return {}

    vendor_votes: dict[str, float] = defaultdict(float)
    vendor_id_by_name: dict[str, Any] = {}
    platform_votes: dict[str, float] = defaultdict(float)
    platform_id_by_name: dict[str, Any] = {}

    for row, weight, _ in matched_rows:
        if has_specific_store:
            mid = row.get("merchant_id")
            mid_str = str(mid or "")
            vname = ""
            if mid_str and mid_str not in platform_ids and mid_str in by_id:
                vname = str(by_id[mid_str].get("nome_padrao") or "").strip()
            elif not mid_str:
                vname = str(row.get("vendedor") or row.get("merchant_name") or row.get("fornecedor") or "").strip()
                if vname and _norm(vname) in platforms_by_key:
                    vname = ""
            if vname:
                vendor_votes[vname] += weight
                if mid and mid_str not in platform_ids:
                    vendor_id_by_name.setdefault(vname, mid)

        pid = row.get("platform_id")
        pid_str = str(pid or "")
        pname = ""
        if pid_str and pid_str in by_id:
            pname = str(by_id[pid_str].get("nome_padrao") or "").strip()
        elif row.get("plataforma"):
            pname = str(row.get("plataforma") or "").strip()
        if pname:
            platform_votes[pname] += weight
            if pid:
                platform_id_by_name.setdefault(pname, pid)

    winning_vendor = _pick_winner(vendor_votes) if has_specific_store else None
    winning_merchant_id = vendor_id_by_name.get(winning_vendor) if winning_vendor else None

    # If we identified a winning vendor, also include secondary historical records of that vendor
    # to enrich missing classification fields (category, subcategory, locality, payment method).
    all_enrichment_rows = list(matched_rows)
    if winning_merchant_id is not None:
        seen_ids = {id(r) for r, _, _ in all_enrichment_rows}
        for idx, (row, is_capture) in enumerate(candidates):
            if id(row) in seen_ids or _row_flow(row) != flow:
                continue
            if str(row.get("merchant_id") or "") == str(winning_merchant_id):
                all_enrichment_rows.append((row, 0.85 / (1.0 + idx * 0.05), is_capture))

    category_votes: dict[str, float] = defaultdict(float)
    subcategory_by_cat: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    nature_votes: dict[str, float] = defaultdict(float)
    type_votes: dict[str, float] = defaultdict(float)
    payment_votes: dict[str, float] = defaultdict(float)
    locality_votes: dict[str, float] = defaultdict(float)
    account_votes: dict[str, float] = defaultdict(float)

    allowed_natures = {"ESTORNO", "RENDIMENTO", "COMPRA"} if flow == "entrada" else {"COMPRA", "IMPOSTO_TRIBUTO", "TARIFA_ENCARGO"}
    selectable_accounts = _selectable_account_keys(db)
    allowed_subcats = _safe_category_subcategories(db)

    for row, weight, is_capture in all_enrichment_rows:
        cat = str((row.get("categoria_sugerida") if is_capture else row.get("categoria")) or row.get("categoria") or row.get("categoria_sugerida") or "").strip()
        if cat:
            category_votes[cat] += weight
            sub = str(row.get("subcategoria") or "").strip()
            if sub:
                if cat not in CURATED_SUBCATEGORIES or sub in set(allowed_subcats.get(cat, [])):
                    subcategory_by_cat[cat][sub] += weight

        nat = str(row.get("natureza") or "").strip()
        if nat in NATURES and nat in allowed_natures:
            nature_votes[nat] += weight

        tipo = str(row.get("tipo") or "").strip()
        if tipo and tipo != "transferencia" and not (flow == "saida" and tipo == "estorno"):
            type_votes[tipo] += weight

        pm = str(row.get("metodo_pagamento") or "").strip()
        if pm:
            pm_bonus = 1.3 if (source_provider and str(row.get("source_provider") or "").strip() == source_provider) else 1.0
            payment_votes[pm] += weight * pm_bonus

        loc = str(row.get("localidade") or "").strip()
        if loc:
            locality_votes[loc] += weight

        acc = str(row.get("account_profile_id") or "").strip()
        if acc and (not selectable_accounts or acc in selectable_accounts):
            row_provider = str(row.get("source_provider") or "").strip()
            row_hint = str(row.get("instrument_hint") or "").strip()
            provider_compatible = (not source_provider) or (row_provider == source_provider)
            hint_compatible = (not instrument_hint) or (not row_hint) or (row_hint == instrument_hint)
            if provider_compatible and hint_compatible:
                acc_weight = weight * (1.4 if source_provider and row_provider == source_provider else 0.9)
                account_votes[acc] += acc_weight

    winning_platform = _pick_winner(platform_votes) or detected_platform_name
    winning_platform_id = (
        platform_id_by_name.get(winning_platform)
        or (detected_platform_doc.get("_id") if detected_platform_doc else None)
    )
    winning_category = _pick_winner(category_votes)
    target_cat_for_sub = str(document.get("categoria_sugerida") or document.get("categoria") or winning_category or "").strip()
    winning_subcategory = _pick_winner(subcategory_by_cat.get(target_cat_for_sub, {})) if target_cat_for_sub else None
    winning_nature = _pick_winner(nature_votes)
    winning_type = _pick_winner(type_votes)
    winning_payment = _pick_winner(payment_votes)
    winning_locality = _pick_winner(locality_votes)
    winning_account = _pick_winner(account_votes, minimum_weight=1.5)

    result: dict[str, Any] = {"match_count": len(matched_rows)}
    if winning_vendor:
        result["vendedor"] = winning_vendor
        if winning_merchant_id is not None:
            result["merchant_id"] = winning_merchant_id
    if winning_platform:
        result["plataforma"] = winning_platform
        if winning_platform_id is not None:
            result["platform_id"] = winning_platform_id
    if winning_category:
        result["categoria_sugerida"] = winning_category
    if winning_subcategory:
        result["subcategoria"] = winning_subcategory
    if winning_nature:
        result["natureza"] = winning_nature
    if winning_type:
        result["tipo"] = winning_type
    if winning_payment:
        result["metodo_pagamento"] = winning_payment
    if winning_locality:
        result["localidade"] = winning_locality
    if winning_account:
        result["account_profile_id"] = winning_account
    return result


def apply_historical_capture_defaults(
    db,
    draft: dict[str, Any],
    *,
    base_item: dict[str, Any] | None = None,
    include_account_profile: bool = False,
) -> list[str]:
    """Populate missing capture fields on `draft` from reviewed historical records."""
    source = {**(base_item or {}), **draft}
    profile = historical_capture_profile(db, source, require_reviewed=True)
    if not profile:
        return []
    prefilled: list[str] = []
    if not source.get("merchant_id") and profile.get("merchant_id") and profile.get("vendedor"):
        if not source.get("platform_id") or profile["merchant_id"] != source.get("platform_id"):
            draft["merchant_id"] = profile["merchant_id"]
            draft["merchant_name"] = profile["vendedor"]
            prefilled.append("vendedor")
    if not source.get("platform_id") and profile.get("platform_id"):
        if not draft.get("merchant_id") or draft.get("merchant_id") != profile["platform_id"]:
            draft["platform_id"] = profile["platform_id"]
            prefilled.append("plataforma")
    if not source.get("categoria_sugerida") and profile.get("categoria_sugerida"):
        draft["categoria_sugerida"] = profile["categoria_sugerida"]
        prefilled.append("categoria_sugerida")
    effective_cat = draft.get("categoria_sugerida") or source.get("categoria_sugerida")
    if not source.get("subcategoria") and profile.get("subcategoria") and effective_cat == profile.get("categoria_sugerida"):
        draft["subcategoria"] = profile["subcategoria"]
        prefilled.append("subcategoria")
    if not source.get("natureza") and profile.get("natureza"):
        draft["natureza"] = profile["natureza"]
        prefilled.append("natureza")
    if not source.get("tipo") and profile.get("tipo"):
        draft["tipo"] = profile["tipo"]
        prefilled.append("tipo")
    if not source.get("metodo_pagamento") and profile.get("metodo_pagamento"):
        draft["metodo_pagamento"] = profile["metodo_pagamento"]
        prefilled.append("metodo_pagamento")
    if not source.get("localidade") and profile.get("localidade"):
        draft["localidade"] = profile["localidade"]
        prefilled.append("localidade")
    if include_account_profile and not source.get("account_profile_id") and profile.get("account_profile_id"):
        draft["account_profile_id"] = profile["account_profile_id"]
        prefilled.append("account_profile_id")
    if prefilled:
        draft["historical_prefill_fields"] = prefilled
    return prefilled


def build_vendor_defaults(db) -> dict[str, dict[str, str]]:
    """Build per-vendor most frequent classification defaults for interactive form pre-fill."""
    by_id, platform_ids, _ = _merchant_catalog(db)
    confirmed_captures = _safe_find(getattr(db, "provisional_transactions", None), {"status": "confirmed"}, limit=600)
    ledger_rows = _safe_find(getattr(db, "transactions", None), {}, limit=800)
    selectable_accounts = _selectable_account_keys(db)
    allowed_subcats = _safe_category_subcategories(db)

    buckets: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    subcat_buckets: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

    for idx, (row, is_capture) in enumerate(
        [(r, True) for r in confirmed_captures] + [(r, False) for r in ledger_rows]
    ):
        if row.get("natureza") == "TRANSFERENCIA":
            continue
        mid = str(row.get("merchant_id") or "")
        vname = ""
        if mid and mid not in platform_ids and mid in by_id:
            vname = str(by_id[mid].get("nome_padrao") or "").strip()
        elif not mid:
            vname = str(row.get("vendedor") or row.get("merchant_name") or row.get("fornecedor") or "").strip()
        if not vname:
            continue

        weight = 1.5 if _row_has_user_review(row) else 1.0
        weight += 0.2 / (1.0 + idx * 0.05)

        pid = str(row.get("platform_id") or "")
        pname = str(by_id[pid].get("nome_padrao") or "").strip() if pid in by_id else str(row.get("plataforma") or "").strip()
        if pname:
            buckets[vname]["plataforma"][pname] += weight

        cat = str((row.get("categoria_sugerida") if is_capture else row.get("categoria")) or row.get("categoria") or row.get("categoria_sugerida") or "").strip()
        if cat:
            buckets[vname]["categoria"][cat] += weight
            sub = str(row.get("subcategoria") or "").strip()
            if sub and (cat not in CURATED_SUBCATEGORIES or sub in set(allowed_subcats.get(cat, []))):
                subcat_buckets[vname][cat][sub] += weight

        nat = str(row.get("natureza") or "").strip()
        if nat in NATURES and nat != "TRANSFERENCIA":
            buckets[vname]["natureza"][nat] += weight

        tipo = str(row.get("tipo") or "").strip()
        if tipo and tipo != "transferencia":
            buckets[vname]["tipo"][tipo] += weight

        pm = str(row.get("metodo_pagamento") or "").strip()
        if pm:
            buckets[vname]["metodo_pagamento"][pm] += weight

        loc = str(row.get("localidade") or "").strip()
        if loc:
            buckets[vname]["localidade"][loc] += weight

        acc = str(row.get("account_profile_id") or "").strip()
        if acc and (not selectable_accounts or acc in selectable_accounts):
            buckets[vname]["account_profile_id"][acc] += weight

    defaults: dict[str, dict[str, str]] = {}
    for vname, field_map in buckets.items():
        entry: dict[str, str] = {}
        for field in ("plataforma", "categoria", "natureza", "tipo", "metodo_pagamento", "localidade", "account_profile_id"):
            winner = _pick_winner(field_map.get(field, {}), minimum_weight=1.0)
            if winner:
                entry[field] = winner
        if "categoria" in entry:
            sub_winner = _pick_winner(subcat_buckets[vname].get(entry["categoria"], {}), minimum_weight=1.0)
            if sub_winner:
                entry["subcategoria"] = sub_winner
        if entry:
            defaults[vname] = entry
    return defaults
