from datetime import date, datetime, timezone, timedelta
from copy import deepcopy
from zoneinfo import ZoneInfo
import calendar
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import DuplicateKeyError, WriteError, OperationFailure
from pydantic import BaseModel, Field, model_validator
from fastapi import UploadFile, File, Form
import hmac
import asyncio
import logging
from contextlib import asynccontextmanager
from app.config import get_settings
from app.schemas import EventAck, RawEventIn
from app.transaction_service import EventBusy, EventConflict, TransactionService, enrich_notification_event, stored_instant
from app.services.ai.xai.provider import XAITextProvider, XAIVisionProvider, XAIError
from app.schemas_document import DocumentExtraction
from app.parsers import parse_brl, parse_datetime
from app.auth import require_session
from app.economic_policy import eligible_payment, effects, captures_not_in_ledger
from app.economic_period import economic_month, economic_instant, economic_in_period
from app.financial_time import LOCAL_ZONE, day_start, local_date, today as local_today, utc_instant
from app.analytics import EXPENSE_REASONS, calculate_financial_totals, decimal_amount
from app.category_filter import ACTIVE_CAPTURES, catalog_category_names, category_query
from app.movement_filters import period_query, intersect, search_query, filter_key as movement_filter_key
from app.document_validation import validate_document_extraction
from app.pdf_input import PDFPasswordError, read_pdf
from app.reference_cache import classification_cache
from app.upload_worker import persisted_event, run_upload
from app.operation_identity import bind_upload, complete_upload
from app.mutation_identity import durable_mutation
from app.commitment_estimates import effective_commitments, estimate_account, same_estimate_account, PATTERNS
from app.commitments import commitment_cutoff, CommitmentIn, advance, as_date, bson_date, future_pending_total, schedule, schedule_from, stored_document, payment_occurrences, occurrence_already_linked, next_occurrence_date, historical_occurrence_conflicts, automatic_suggestion
from app.parcelamento_link import decorate_purchase_links, ensure_parcelamento_purchase, sync_parcelamento_purchase
from app.future_commitments import effective_documents, overlay_link_names
from app.providers.import_provider import ImportFinancialProvider
from app.platforms import PLATFORMS, _merchant_catalog, apply_historical_capture_defaults, build_vendor_defaults, has_vendor_tokens_beyond_platform, historical_capture_profile, platform_identity
from app.taxonomy import NATURES, NATURE_LABELS, CURATED_SUBCATEGORIES, category_subcategories, infer_classification, taxonomy_overrides, normalized as taxonomy_key
from app.account_positions import account_key_for_legacy, account_positions, validate_account_key, reconcile_capture_transfer
from app.account_profiles import (AccountProfileCreate, AccountProfileUpdate, AccountProfileDelete,
    account_usage, create_profile, update_profile, delete_profile, movement_account_context, capture_account_query, legacy_accounts_by_profile)
from app.safe_errors import safe_error
from app.movement_insert_lock import lock_movement_insert
from app.pagination import page as workflow_page
from app.source_tokens import source_token, require_source_token
from app.record_review import (historical_statement_reviews, capture_review_query, display_capture_review,
    review_filter_key, encode_review_cursor, decode_review_cursor)
from app.movement_deletion import DeleteMovement, Source, blockers as movement_blockers, preview_deletion, delete_movement, initialize_deletion_storage
from app.dashboard_window import snapshot_for, PROJECTION
from pymongo.database import Database
from app.atomic import _SessionDatabase
from app.atomic import AtomicUnavailable, mongo_snapshot, mongo_transaction, session_args, session_database
from app.media_store import reconcile_media, store_media
import base64, hashlib, csv, io, json, re, os, unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode
from uuid import uuid4, uuid5, NAMESPACE_URL

def configured_mongodb_uri(settings):
    uri=settings.mongodb_uri
    hosts=settings.mongodb_direct_hosts.strip()
    if not hosts or not uri.startswith("mongodb+srv://"):
        return uri
    credentials_and_target=uri.removeprefix("mongodb+srv://")
    credentials,target=credentials_and_target.rsplit("@",1)
    _,path_and_query=target.split("/",1)
    database_name,_,query=path_and_query.partition("?")
    options=dict(parse_qsl(query,keep_blank_values=True))
    options.update({"tls":"true","authSource":options.get("authSource","admin"),"replicaSet":settings.mongodb_replica_set,"readPreference":"primary"})
    options={key:value for key,value in options.items() if value}
    return f"mongodb://{credentials}@{hosts}/{database_name}?{urlencode(options)}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.environment.lower()=="production" and len(settings.jwt_secret)<32:
        raise RuntimeError("Production requires JWT_SECRET of at least 32 characters")
    app.state.mongo_client = None
    app.state.service = None
    app.state.db = None
    recovery_task = None
    if settings.mongodb_uri:
        from pymongo import MongoClient
        app.state.mongo_client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=2500)
        app.state.db = app.state.mongo_client[settings.mongodb_database]
        initialize_deletion_storage(app.state.db)
        app.state.service = TransactionService(app.state.db)
        app.state.db.categories.create_index("key",unique=True)
        app.state.db.account_balance_snapshots.create_index("account_key",unique=True)
        try:
            app.state.db.account_profiles.create_index("key",unique=True,name="account_profile_identity")
        except OperationFailure as exc:
            if exc.code != 8000: raise
            logging.warning("Account profile storage unavailable; existing ledger remains readable.")
        app.state.db.commitments.create_index("commitment_id",unique=True)
        app.state.db.commitments.create_index("linked_transactions.period_key",name="commitment_payment_period")
        app.state.db.media_assets.create_index("sha256",unique=True)
        app.state.db.merchants.create_index("nome_padrao",name="merchant_standard_name")
        app.state.db.batch_commands.create_index([("status",1),("created_at",1)],name="pending_batch_commands")
        app.state.db.capture_batches.create_index([("status",1),("created_at",-1)],name="capture_batch_drafts")
        app.state.db.capture_batch_locks.update_one({"_id":"movement-insert"},{"$setOnInsert":{"revision":0}},upsert=True)
        app.state.db.commitments.create_index("linked_transactions.transaction_id",unique=True,sparse=True)
        app.state.db.commitments.create_index("linked_transactions.provisional_id",unique=True,sparse=True)
        app.state.db.commitments.create_index("purchase_provisional_id",unique=True,sparse=True,name="commitment_purchase_provisional")
        # Stable pagination and the dashboard date windows use these exact prefixes.
        app.state.db.transactions.create_index([("data_evento",-1),("_id",-1)],name="movement_page")
        app.state.db.transaction_descriptor_index.create_index([("key",1),("_id",1)],name="exact_descriptor_batch")
        app.state.db.transactions.create_index([("descricao_original","text"),("estabelecimento_original","text"),("categoria","text"),("subcategoria","text"),("tipo","text"),("merchant_name","text")],name="transaction_text_search",default_language="portuguese")
        app.state.db.transactions.create_index([("categoria",1),("data_evento",-1),("_id",-1)],name="movement_category_page")
        app.state.db.transactions.create_index([("fluxo",1),("classification.migration_reason",1),("valor",1),("data_evento",-1)],name="commitment_payment_candidates")
        app.state.db.provisional_transactions.create_index([("status",1),("data_evento",-1),("_id",-1)],name="provisional_status_page")
        app.state.db.provisional_transactions.create_index([("status",1),("fluxo",1),("valor",1),("data_evento",-1)],name="capture_payment_candidates")
        app.state.db.provisional_transactions.create_index([("merchant_raw","text"),("merchant_name","text"),("categoria_sugerida","text"),("subcategoria","text"),("tipo","text")],name="provisional_text_search",default_language="portuguese")
        app.state.db.review_queue.create_index([("status",1),("created_at",-1),("_id",-1)],name="review_status_page")
        for collection in ('financial_cases','saving_experiments'):
            app.state.db[collection].create_index([('created_at',-1),('_id',-1)],name='workflow_page')
        account_index=[('account_id',1),('data_evento',-1)]
        if not any(list(spec['key'])==account_index for spec in app.state.db.transactions.index_information().values()):
            app.state.db.transactions.create_index(account_index,name='account_posted_movements')
        async def recover_events():
            cycles=0
            while True:
                try:
                    await asyncio.to_thread(app.state.service.recover_pending)
                    await asyncio.to_thread(recover_classification_commands,app.state.db)
                    if cycles%10==0: await asyncio.to_thread(reconcile_media,app.state.db)
                except Exception as exc:
                    logging.error("Pending event recovery failed (%s)", safe_error(exc))
                cycles+=1
                await asyncio.sleep(60)
        recovery_task=asyncio.create_task(recover_events())
    yield
    if recovery_task:
        recovery_task.cancel()
        try: await recovery_task
        except asyncio.CancelledError: pass
    if app.state.mongo_client:
        app.state.mongo_client.close()

app = FastAPI(title="Financial Digital Twin API", version="0.4.0", lifespan=lifespan)
from app.account_auth import router as account_auth_router
app.include_router(account_auth_router)
@app.exception_handler(RequestValidationError)
async def private_validation_error(request: Request, exc):
    if request.url.path.startswith('/v1/auth/'):
        # FastAPI's default validation response echoes input, including passwords.
        return JSONResponse(status_code=422, content={'detail': 'Confira os campos. O usuário deve ter 3 a 64 caracteres; a nova senha, pelo menos 12.'})
    from fastapi.exception_handlers import request_validation_exception_handler
    return await request_validation_exception_handler(request, exc)
from app.insights import router as insights_router
app.include_router(insights_router)
from app.voice_capture import router as voice_capture_router
app.include_router(voice_capture_router)
from app.capture_batches import router as capture_batches_router
app.include_router(capture_batches_router)
from app.future_commitments import router as future_commitments_router
app.include_router(future_commitments_router)
from app.commitment_profile import router as commitment_profile_router
app.include_router(commitment_profile_router)
from app.subcategories import router as subcategories_router
app.include_router(subcategories_router)
from app.cards import router as cards_router, metadata as card_metadata
from app.installment_cards import attach_cards as attach_installment_cards
from app.installment_labels import card_labels, linked_statement_snapshots, plan_card_labels, reviewed_plan_labels
app.include_router(cards_router)
from app.commitment_projection import router as commitment_projection_router
app.include_router(commitment_projection_router)
from app.month_close import router as month_close_router
app.include_router(month_close_router)
@app.exception_handler(AtomicUnavailable)
def atomic_unavailable(_request, exc):
    return JSONResponse(status_code=503,content={"detail":str(exc)})
@app.exception_handler(OperationFailure)
def mongo_write_conflict(_request, exc):
    if exc.code in {112,244,251} or exc.has_error_label('TransientTransactionError'):
        return JSONResponse(status_code=409,content={"detail":"Conflito concorrente. Recarregue e repita com o mesmo identificador."})
    logging.error("Mongo operation failed, code=%s",exc.code)
    message = str((exc.details or {}).get('errmsg', exc)).lower()
    if exc.code == 8000 and any(reason in message for reason in ('too many collections', 'maximum number of collections', 'collection limit')):
        return JSONResponse(status_code=503,content={"detail":"O banco atingiu o limite de coleções. A operação foi abortada; o histórico permanece preservado. É necessário liberar capacidade no servidor antes de tentar novamente."})
    return JSONResponse(status_code=503,content={"detail":"Operação indisponível; nenhuma gravação parcial deve ser considerada confirmada."})

app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:19006","http://127.0.0.1:19006"],allow_methods=["GET","POST","PUT","PATCH","DELETE"],allow_headers=["Authorization","Content-Type"],expose_headers=['X-Operation-Pending','Retry-After','X-Session-Token','X-Session-Expires-In'])

@app.middleware("http")
async def private_response_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/v1/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.middleware("http")
async def invalidate_reference_data(request: Request, call_next):
    mutates = request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path != "/v1/capture-batches/file-status" and request.url.path.startswith(("/v1/future-commitments", "/v1/commitment-profile", "/v1/commitments", "/v1/capture-batches", "/v1/movements", "/v1/transactions", "/v1/categories", "/v1/provisional", "/v1/merchants", "/v1/reviews", "/v1/events", "/v1/import", "/v1/documents"))
    if mutates: classification_cache.invalidate()
    try:
        return await call_next(request)
    finally:
        if mutates: classification_cache.invalidate()
from app.release_manifest import verified_release
# Compatibility symbols: the API uses the verified APK manifest, never these values.
ANDROID_VERSION_NAME=None
ANDROID_VERSION_CODE=None

def android_apk_path():
    configured=os.getenv("ANDROID_APK_PATH")
    return Path(configured) if configured else Path(__file__).resolve().parents[2]/"mobile"/"artifacts"/"financial-twin-arm64.apk"

def json_safe(value):
    if isinstance(value, ObjectId): return str(value)
    if isinstance(value, Decimal128): return float(value.to_decimal())
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, datetime):
        aware=value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return aware.isoformat().replace("+00:00","Z")
    if isinstance(value, list): return [json_safe(item) for item in value]
    if isinstance(value, dict): return {k:json_safe(v) for k,v in value.items() if k != "_id"} | ({"id":str(value["_id"])} if "_id" in value else {})
    return value

def _attach_historical_capture_defaults(document:dict[str,Any], result:dict[str,Any], db, catalog_cache=None):
    hist=historical_capture_profile(db,document,catalog_cache=catalog_cache)
    if not hist: return
    defaults:dict[str,Any]={}
    if not result.get("vendedor") and hist.get("vendedor"):
        defaults["vendedor"]=hist["vendedor"]
        result.setdefault("vendedor_sugerido",hist["vendedor"])
    if not result.get("plataforma") and hist.get("plataforma"):
        defaults["plataforma"]=hist["plataforma"]
        result.setdefault("plataforma_sugerida",hist["plataforma"])
    for field in ("categoria_sugerida","subcategoria","natureza","tipo","metodo_pagamento","localidade","account_profile_id"):
        if not result.get(field) and hist.get(field):
            defaults[field]=hist[field]
    if defaults.get("categoria_sugerida") and not result.get("categoria_inferida"):
        result["categoria_inferida"]=defaults["categoria_sugerida"]
    if defaults:
        result["historical_defaults"]=defaults
        result["historical_match_count"]=hist.get("match_count",0)

def _sync_merchant_alias(db, merchant:dict[str,Any], raw_source:str|None, supplier_name:str, platform_active:bool, platform_name:str|None, previous_merchant_id, now:datetime):
    use_raw=bool(raw_source) and (not platform_active or has_vendor_tokens_beyond_platform(raw_source,platform_name))
    raw=(raw_source if use_raw else supplier_name) or supplier_name
    normalized=re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9 ]"," ",str(raw).upper())).strip()
    if not normalized: return
    if previous_merchant_id and previous_merchant_id!=merchant.get("_id"):
        prev_doc=db.merchants.find_one({"_id":previous_merchant_id})
        if prev_doc and isinstance(prev_doc.get("aliases"),list):
            filtered=[a for a in prev_doc["aliases"] if a.get("normalizado")!=normalized]
            if len(filtered)!=len(prev_doc["aliases"]):
                db.merchants.update_one({"_id":previous_merchant_id},{"$set":{"aliases":filtered,"updated_at":now}})
    existing_doc=db.merchants.find_one({"_id":merchant["_id"]}) or merchant
    aliases=list(existing_doc.get("aliases") or [])
    if not any(a.get("normalizado")==normalized for a in aliases):
        aliases.append({"valor":raw,"normalizado":normalized,"status":"confirmado","source_record_ids":[]})
        merchant["aliases"]=aliases
        db.merchants.update_one({"_id":merchant["_id"]},{"$set":{"aliases":aliases,"updated_at":now}})

def with_merchant_name(document:dict[str,Any], db):
    result=json_safe(document)
    if document.get('provisional_id'): result['categoria']=document.get('categoria_sugerida')
    result['source_token']=source_token(document)
    if not document.get("provisional_id"):
        result.setdefault("status_revisao","pendente")
    merchant_id=document.get("merchant_id")
    if merchant_id:
        merchant=db.merchants.find_one({"_id":merchant_id},{"nome_padrao":1})
        if merchant and merchant.get("nome_padrao"):
            result["vendedor"]=merchant["nome_padrao"]
            result["fornecedor"]=merchant["nome_padrao"]
    platform_id=document.get("platform_id")
    if platform_id:
        platform=db.merchants.find_one({"_id":platform_id},{"nome_padrao":1})
        if platform and platform.get("nome_padrao"): result["plataforma"]=platform["nome_padrao"]
    elif detected:=platform_identity(document.get("merchant_raw") or document.get("estabelecimento_original") or document.get("descricao_original")):
        result["plataforma_sugerida"]=detected[1]
    suggestion=infer_classification(document,taxonomy_overrides(db))
    for field,value in suggestion.items():
        if not result.get(field): result['categoria_inferida' if field=='categoria' and document.get('provisional_id') else f"{field}_sugerida"]=value
    if document.get("provisional_id") and document.get("status") in {"provisional","enriched"}:
        _attach_historical_capture_defaults(document,result,db)
    return result

def with_merchant_names(documents:list[dict[str,Any]], db):
    """Enrich a page with merchant/platform names using one bounded lookup."""
    merchant_ids={item.get(key) for item in documents for key in ("merchant_id","platform_id") if item.get(key)}
    merchants={str(item["_id"]):item.get("nome_padrao") for item in db.merchants.find({"_id":{"$in":list(merchant_ids)}},{"nome_padrao":1})} if merchant_ids else {}
    results=[]
    overrides=taxonomy_overrides(db)
    catalog_cache=_merchant_catalog(db) if any(item.get("provisional_id") and item.get("status") in {"provisional","enriched"} for item in documents) else None
    for document in documents:
        result=json_safe(document)
        if document.get('provisional_id'): result['categoria']=document.get('categoria_sugerida')
        result['source_token']=source_token(document)
        if not document.get("provisional_id"):
            result.setdefault("status_revisao","pendente")
        merchant_name=merchants.get(str(document.get("merchant_id")))
        if merchant_name:
            result["vendedor"]=merchant_name
            result["fornecedor"]=merchant_name
        platform_name=merchants.get(str(document.get("platform_id")))
        if platform_name:
            result["plataforma"]=platform_name
        elif not document.get("platform_id") and (detected:=platform_identity(document.get("merchant_raw") or document.get("estabelecimento_original") or document.get("descricao_original"))):
            result["plataforma_sugerida"]=detected[1]
        for field,value in infer_classification(document,overrides).items():
            if not result.get(field): result['categoria_inferida' if field=='categoria' and document.get('provisional_id') else f"{field}_sugerida"]=value
        if document.get("provisional_id") and document.get("status") in {"provisional","enriched"}:
            _attach_historical_capture_defaults(document,result,db,catalog_cache=catalog_cache)
        results.append(result)
    return results

def with_installment_references(documents:list[dict[str,Any]], items:list[dict[str,Any]], db):
    """Add read-only links from ledger rows to imported installment plans.

    Imported plans and ledger rows are intentionally kept in separate
    collections.  The link can be present on the row (installment.plan_id)
    or on the plan part (installments.transaction_id), so support both forms
    without inferring identity from merchant, date, or amount alone.
    """
    ledger = [row for row in documents if not row.get("provisional_id") and row.get("_id")]
    if not ledger:
        return items
    transaction_ids = [row["_id"] for row in ledger]
    plan_ids = [row.get("installment", {}).get("plan_id") for row in ledger
                if isinstance(row.get("installment"), dict)
                and ObjectId.is_valid(str(row.get("installment", {}).get("plan_id", "")))]
    clauses = []
    if plan_ids:
        clauses.append({"_id": {"$in": plan_ids}})
    clauses.append({"installments.transaction_id": {"$in": transaction_ids}})
    plans = list(db.installment_plans.find({"$or": clauses}))
    if not plans:
        return items
    effective = effective_documents(db, "installment", plans)
    reviewed_labels = reviewed_plan_labels(db, effective)
    snapshots = linked_statement_snapshots(db, {str(plan["_id"]): [part.get("transaction_id") for part in plan.get("installments") or [] if part.get("transaction_id")] for plan in effective})
    by_id = {str(plan["_id"]): plan for plan in effective}
    by_transaction = {}
    for plan in effective:
        for part in plan.get("installments", []):
            if part.get("transaction_id"):
                by_transaction[str(part["transaction_id"])] = (plan, part)

    for document, result in zip(documents, items):
        if document.get("provisional_id"):
            continue
        installment = document.get("installment") if isinstance(document.get("installment"), dict) else {}
        plan = by_id.get(str(installment.get("plan_id")))
        part = by_transaction.get(str(document["_id"]), (None, None))[1]
        if plan is None and part is not None:
            plan = by_transaction[str(document["_id"])][0]
        if plan is None:
            continue
        if part is None and installment.get("numero") is not None:
            part = next((candidate for candidate in plan.get("installments", [])
                         if candidate.get("numero") == installment.get("numero")), None)
        if part is None:
            continue
        future = [candidate for candidate in plan.get("installments", [])
                  if candidate.get("status") == "projetada"
                  and not candidate.get("transaction_id")
                  and not candidate.get("bill_id")
                  and not candidate.get("future_excluded")]
        next_part = min(future, key=lambda candidate: str(candidate.get("data_vencimento") or candidate.get("competencia") or "~"), default=None)
        saved_card = (plan.get("profile") or {}).get("card_label") or document.get("card_label") or (snapshots.get(str(plan["_id"])) or {}).get("card_label") or plan.get("card_label")
        linked_card = plan_card_labels(db, [plan["_id"]]).get(str(plan["_id"]))
        if not linked_card and document.get("card_id"):
            linked_card = card_labels(db, [document.get("card_id")]).get(str(document.get("card_id")))
        card_name = saved_card or linked_card
        if card_name and str(document.get("metodo_pagamento") or "") in {"cartao", "cartao_credito", "cartao_debito"}:
            result["card_label"] = card_name
        raw_name = plan.get("descricao_original") or plan.get("descricao") or ""
        reviewed = reviewed_labels.get(str(plan["_id"]))
        seller = (snapshots.get(str(plan["_id"])) or {}).get("vendedor")
        plan_name = reviewed if reviewed and reviewed != raw_name else (seller or reviewed or plan.get("display_name") or plan.get("descricao") or "Compra parcelada")
        result["installment_plan"] = json_safe({
            "id": str(plan["_id"]),
            "name": plan_name,
            "purchase_date": plan.get("data_compra"),
            "current_number": part.get("numero"),
            "total": plan.get("numero_parcelas") or len(plan.get("installments", [])),
            "current_amount": part.get("valor"),
            "future_count": len(future),
            "future_total": sum((decimal_amount(candidate.get("valor")) or Decimal("0") for candidate in future), Decimal("0")),
            "next_due_date": next_part.get("data_vencimento") if next_part else None,
            "source": "installment_plan",
            "card_label": card_name,
        })
    return items

def canonical_key(value:str)->str:
    plain=unicodedata.normalize("NFKD",value).encode("ascii","ignore").decode("ascii").casefold()
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9 ]"," ",plain)).strip()

def canonical_distinct(db, field:str, value:str)->str:
    key=canonical_key(value)
    return next((str(item) for item in db.transactions.distinct(field) if item and canonical_key(str(item))==key),value)

def find_canonical_merchant(db, value:str):
    exact=db.merchants.find_one({"nome_padrao":value.strip()},{"nome_padrao":1,"aliases.normalizado":1}) if value and value.strip() else None
    if exact: return exact
    key=canonical_key(value)
    for merchant in db.merchants.find({"nome_padrao":{"$nin":[None,""]}},{"nome_padrao":1,"aliases.normalizado":1}):
        if canonical_key(str(merchant.get("nome_padrao","")))==key: return merchant
        if any(canonical_key(str(alias.get("valor") or alias.get("normalizado") or ""))==key for alias in merchant.get("aliases",[])): return merchant
    return None

def find_or_create_platform(db, name:str, now:datetime):
    identity=platform_identity(name)
    canonical=identity[1] if identity else name.strip()
    platform=db.merchants.find_one({"external_key":identity[0]}) if identity else None
    if not platform:
        platform=db.merchants.find_one({"nome_padrao":canonical})
    if platform: return platform
    platform={"_id":ObjectId(),"external_key":identity[0] if identity else f"platform-user-{uuid4()}","schema_version":2,"created_at":now,"updated_at":now,"tipo_entidade":"marca","nome_padrao":canonical,"aliases":[]}
    db.merchants.insert_one(platform)
    return platform

def supplier_match_score(source:str,candidate:str,supplier:str)->float:
    source_key=canonical_key(source);candidate_key=canonical_key(candidate);supplier_key=canonical_key(supplier)
    if not source_key or not candidate_key: return 0.0
    if source_key==candidate_key: return 1.0
    score=SequenceMatcher(None,source_key,candidate_key).ratio()
    if len(supplier_key)>=4 and re.search(rf"(?:^| ){re.escape(supplier_key)}(?: |$)",candidate_key): score=max(score,0.97)
    return score

def similar_supplier_records(db,item:dict[str,Any],supplier:str,limit:int=500):
    from pymongo.database import Database
    source=str(item.get("estabelecimento_original") or item.get("descricao_original") or "")
    if not source: return [],[]
    transaction_matches=[]
    query={"_id":{"$ne":item.get("_id")},"$or":[{"merchant_id":{"$exists":False}},{"merchant_id":None}]}
    if isinstance(db,Database) or isinstance(getattr(db,"db",None),Database): query["$text"]={"$search":source}
    transaction_candidates=list(db.transactions.find(query,{"estabelecimento_original":1,"descricao_original":1}).sort("_id",1).limit(5001))
    if len(transaction_candidates)>5000: raise HTTPException(status_code=409,detail="A busca de fornecedores excede o limite seguro. Refine a descrição.")
    for candidate in transaction_candidates:
        raw=str(candidate.get("estabelecimento_original") or candidate.get("descricao_original") or "");score=supplier_match_score(source,raw,supplier)
        if score>=0.90: transaction_matches.append((candidate,score,raw))
    provisional_matches=[]
    provisional_query={"status":{"$in":["provisional","enriched"]},"$or":[{"merchant_id":{"$exists":False}},{"merchant_id":None}]}
    if isinstance(db,Database) or isinstance(getattr(db,"db",None),Database): provisional_query["$text"]={"$search":source}
    provisional_candidates=list(db.provisional_transactions.find(provisional_query,{"merchant_raw":1}).sort("_id",1).limit(1001))
    if len(provisional_candidates)>1000: raise HTTPException(status_code=409,detail="A busca de capturas excede o limite seguro. Refine a descrição.")
    for candidate in provisional_candidates:
        raw=str(candidate.get("merchant_raw") or "");score=supplier_match_score(source,raw,supplier)
        if score>=0.90: provisional_matches.append((candidate,score,raw))
    transaction_matches.sort(key=lambda value:value[1],reverse=True);provisional_matches.sort(key=lambda value:value[1],reverse=True)
    if len(transaction_matches)>limit or len(provisional_matches)>limit: raise HTTPException(status_code=409,detail="A propagação excede o limite seguro. Refine a seleção.")
    return transaction_matches,provisional_matches

def exact_descriptor_matches(db,item:dict[str,Any],limit:int=500):
    from pymongo.database import Database
    mongo_db=isinstance(db,Database) or isinstance(getattr(db,"db",None),Database)
    source_key=canonical_key(str(item.get("estabelecimento_original") or item.get("descricao_original") or ""))
    if not source_key: return []
    if mongo_db:
        marker=db.schema_migrations.find_one({"_id":"descriptor_index_v1","complete":True})
        if not marker or marker.get("scanned")!=db.transactions.count_documents({}):
            raise HTTPException(status_code=503,detail="A indexação dos descritores está incompleta; nenhum lote foi sugerido.")
        ids=[entry["_id"] for entry in db.transaction_descriptor_index.find({"key":source_key},{"_id":1}).sort("_id",1).limit(limit+2) if entry["_id"]!=item.get("_id")]
        if len(ids)>limit: raise HTTPException(status_code=409,detail="Há mais correspondências do que o limite seguro do lote. Refine a seleção.")
        query={"_id":{"$in":ids}}
    else:
        query={"_id":{"$ne":item.get("_id")}}
    matches=[]
    for candidate in db.transactions.find(query, {"estabelecimento_original":1,"descricao_original":1,"valor":1}).sort("_id",1).limit(limit+1):
        raw=str(candidate.get("estabelecimento_original") or candidate.get("descricao_original") or "")
        if canonical_key(raw)==source_key: matches.append((candidate,raw))
    if len(matches)>limit: raise HTTPException(status_code=409,detail="Há mais correspondências do que o limite seguro do lote. Refine a seleção.")
    return matches

def batch_classification_matches(db,item:dict[str,Any],limit:int=500):
    """Only suggest a batch when the descriptor is specific and flow direction agrees."""
    source=str(item.get("estabelecimento_original") or item.get("descricao_original") or "")
    source_key=canonical_key(source)
    # A bank or platform name alone identifies the channel, not the underlying movement.
    informative=[token for token in source_key.split() if token not in {"banco","bank","santander","itau","bradesco","nubank","inter","caixa","brasil","pagamento","transferencia"}]
    if not source_key or len(informative)<2: return []
    try:
        raw_amount=item.get("valor")
        source_signed=float(raw_amount.to_decimal() if hasattr(raw_amount,"to_decimal") else raw_amount)
    except (TypeError,ValueError): source_signed=0
    source_sign=1 if source_signed>0 else -1
    matches=[]
    for candidate,raw in exact_descriptor_matches(db,item,limit=5000):
        raw_amount=candidate.get("valor")
        try: signed=float(raw_amount.to_decimal() if hasattr(raw_amount,"to_decimal") else raw_amount or 0)
        except (TypeError,ValueError): signed=0
        sign=1 if signed>0 else -1
        if sign==source_sign:
            matches.append((candidate,raw))
    if len(matches)>limit: raise HTTPException(status_code=409,detail="O lote excede o limite seguro. Refine a seleção.")
    return matches

def database(request: Request):
    db = request.app.state.db
    if db is None: raise HTTPException(status_code=503, detail="MongoDB is not configured")
    return db

@app.get("/live")
async def live():
    return {"status":"alive"}

@app.get("/health")
def health(request: Request):
    client = request.app.state.mongo_client
    if client is None: return {"status":"degraded", "mongodb":"not_configured", "mode":"limited", "auth_required":True}
    try: client.admin.command("ping")
    except Exception: raise HTTPException(status_code=503, detail={"status":"degraded", "mongodb":"unavailable"})
    return {"status":"ok", "mongodb":"connected", "auth_required":True}

@app.get("/v1/app-update")
def app_update():
    return verified_release(android_apk_path())

@app.get("/v1/app-update/apk")
def download_app_update():
    apk=android_apk_path()
    release=verified_release(apk)
    if not release["available"]: raise HTTPException(status_code=404,detail="Android update is not verified")
    return FileResponse(apk,media_type="application/vnd.android.package-archive",filename=f"financial-twin-{release['version_name']}.apk")

@app.post("/v1/events", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_session)])
def ingest_event(event: RawEventIn, request: Request):
    service = request.app.state.service
    if service is None: raise HTTPException(status_code=503, detail="MongoDB is not configured")
    payload = event.model_dump(mode="python")
    payload["event_id"] = str(payload["event_id"])
    payload["captured_at"] = payload["captured_at"].astimezone(timezone.utc)
    try: return persisted_event(request.app.state.db, payload["event_id"], service.process(payload))
    except EventConflict: raise HTTPException(status_code=409, detail="event_id already exists with different content")
    except EventBusy: raise HTTPException(status_code=503,detail="Evento em processamento. Tente novamente com o mesmo identificador.")
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc))

def period_start(anchor:datetime,period:str,custom_from:date|None)->datetime:
    if custom_from: return day_start(custom_from)
    anchor=anchor.astimezone(LOCAL_ZONE)
    if period=="all": return datetime.min.replace(tzinfo=timezone.utc)
    if period=="year": return day_start(date(anchor.year,1,1))
    months={"month":0,"3m":2,"6m":5,"12m":11}.get(period,0)
    month_index=anchor.year*12+anchor.month-1-months
    return day_start(date(month_index//12,month_index%12+1,1))

def normalized_datetime(value:datetime)->datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def shift_months(value:datetime,months:int)->datetime:
    value=normalized_datetime(value).astimezone(LOCAL_ZONE)
    month_index=value.year*12+value.month-1+months
    year,month=month_index//12,month_index%12+1
    day=min(value.day,calendar.monthrange(year,month)[1])
    return value.replace(year=year,month=month,day=day).astimezone(timezone.utc)

def percentage_change(current:Decimal,previous:Decimal)->float|None:
    if previous==0: return None
    return float((current-previous)/abs(previous)*Decimal("100"))

def dashboard_forecast(db):
    """Period-independent home figures. Overview must not repeat these scans per card."""
    pending=db.review_queue.count_documents({"status":"pending"})
    provisional=db.provisional_transactions.count_documents({"status":{"$in":["provisional","enriched"]}})
    ingested=db.source_records.find_one({}, {"ingested_at":1}, sort=[("ingested_at",-1)])
    open_debt=sum((decimal_amount(x.get("saldo_aberto")) or Decimal("0") for x in db.obligations.find({"status":{"$in":["vencida","parcialmente_paga"]}},{"saldo_aberto":1})),Decimal("0"))
    future_financing=sum((decimal_amount(x.get("principal")) or Decimal("0") for x in effective_documents(db, 'obligation', db.obligations.find({"status":"futura","tipo":"financiamento"})) if x.get('status') == 'futura'),Decimal("0"))
    projected_installments=Decimal("0");projected_count=0
    for plan in effective_documents(db, 'installment', db.installment_plans.find({"status":"ativo"})):
        for part in plan.get("installments",[]):
            if part.get("status")=="projetada":
                value=part.get("valor",0);projected_installments+=value.to_decimal() if isinstance(value,Decimal128) else Decimal(str(value));projected_count+=1
    commitment_due_count=0;commitment_due_total=Decimal("0")
    today=local_today()
    next_30_days=today+timedelta(days=30)
    for commitment in effective_commitments(db, db.commitments.find({"status":"ativo"})):
        for occurrence in schedule_from(commitment,today,12):
            due=date.fromisoformat(occurrence["due_date"])
            if today<=due<=next_30_days:
                commitment_due_count+=1
                commitment_due_total+=Decimal(str(occurrence["amount"]))
    investment_count=db.investments.count_documents({})
    return {"pending":pending,"provisional":provisional,"ingested":ingested,"open_debt":open_debt,"future_financing":future_financing,"projected_installments":projected_installments,"projected_count":projected_count,"commitment_due_count":commitment_due_count,"commitment_due_total":commitment_due_total,"investment_count":investment_count}

def _dashboard(period, date_from, date_to, db, snapshot=None, forecast=None):
    if snapshot is None and isinstance(db,(Database,_SessionDatabase)):snapshot=snapshot_for(db,[(period,date_from,date_to)])
    txs=list(snapshot[0]) if snapshot is not None else list(db.transactions.find({}, PROJECTION).limit(0))
    dates=[tx["data_evento"] for tx in txs if tx.get("data_evento")]
    confirmed_all=list(snapshot[1]) if snapshot is not None else list(db.provisional_transactions.find({"status":"confirmed"}, PROJECTION))
    confirmed_dates=[item["data_evento"] for item in confirmed_all if item.get("data_evento")]
    if snapshot is not None and len(snapshot)>2:
        dates=[snapshot[2]["legacy"]] if snapshot[2]["legacy"] else []
        confirmed_dates=[snapshot[2]["captured"]] if snapshot[2]["captured"] else []
    all_dates=[*dates,*confirmed_dates]
    data_anchor=max((normalized_datetime(value) for value in all_dates),default=None)
    economic_dates=[economic_instant(row) for row in [*txs,*confirmed_all] if economic_instant(row)]
    if snapshot is not None and len(snapshot)>2 and snapshot[2].get("economic_anchor"):economic_dates.append(snapshot[2]["economic_anchor"])
    anchor=max((normalized_datetime(value) for value in [*all_dates,*economic_dates]),default=datetime.now(timezone.utc))
    range_start=period_start(anchor,period,date_from)
    range_end=(day_start(date_to+timedelta(days=1))-timedelta(microseconds=1)) if date_to else anchor
    if range_start>range_end: raise HTTPException(status_code=422,detail="A data inicial deve ser anterior à data final.")
    def economic_selected(row,start,end):
        if row.get("competencia"):return economic_in_period(row,date.min if start.year==1 else local_date(start),local_date(end))
        return bool(row.get("data_evento") and start<=normalized_datetime(row["data_evento"])<=end)
    filtered_txs=[tx for tx in txs if economic_selected(tx,range_start,range_end)]
    confirmed=captures_not_in_ledger(filtered_txs,[item for item in confirmed_all if economic_selected(item,range_start,range_end)])
    totals=calculate_financial_totals(filtered_txs,confirmed)
    income,expenses,categories=totals["income"],totals["expenses"],totals["categories"]
    nature_totals={nature:Decimal("0") for nature in NATURES}
    for captured, rows in ((False,filtered_txs),(True,confirmed)):
        for row in rows:
            effect=effects(row,captured)
            nature=row.get("natureza") or infer_classification(row).get("natureza")
            if nature in nature_totals:nature_totals[nature]+=effect["expenses"]
    previous_totals=None;previous_start=None;previous_end=None
    if period!="all":
        if date_from or date_to:
            duration=range_end-range_start;previous_end=range_start-timedelta(microseconds=1);previous_start=previous_end-duration
        else:
            shift=-12 if period=="year" else -{"month":1,"3m":3,"6m":6,"12m":12}.get(period,1)
            previous_start=shift_months(range_start,shift);previous_end=shift_months(range_end,shift)
        previous_txs=[tx for tx in txs if economic_selected(tx,previous_start,previous_end)]
        previous_confirmed=captures_not_in_ledger(previous_txs,[item for item in confirmed_all if economic_selected(item,previous_start,previous_end)])
        previous_totals=calculate_financial_totals(previous_txs,previous_confirmed)
    forecast=forecast or dashboard_forecast(db)
    pending=forecast["pending"]
    provisional=forecast["provisional"]
    latest=max(dates).isoformat() if dates else None
    latest_confirmed=max(confirmed_dates).isoformat() if confirmed_dates else None
    latest_any=data_anchor.isoformat() if data_anchor else None
    filtered_dates=[tx["data_evento"] for tx in filtered_txs if tx.get("data_evento")]
    latest_month=max((economic_month(row) for row in [*filtered_txs,*confirmed] if economic_month(row)),default=None)
    recurring_total=Decimal("0");recurring_count=0;recurring_categories={}
    non_expense_recurring_categories={"pagamento de fatura","pagamento de fatura cartao","investimentos","movimentacao de credito do cartao","transferencia pix","transferencia ted doc"}
    for tx in filtered_txs:
        recurrence=tx.get("recorrencia") or {};event_date=tx.get("data_evento");amount=decimal_amount(tx.get("valor"))
        if not event_date or not amount or economic_month(tx)!=latest_month: continue
        if recurrence.get("tipo") not in {"recorrente_fixa","recorrente_variavel"} or recurrence.get("periodicidade")!="mensal": continue
        if not eligible_payment(tx): continue
        category=tx.get("categoria") or "Sem categoria"
        if canonical_key(category) in non_expense_recurring_categories: continue
        value=abs(amount);recurring_total+=value;recurring_count+=1
        recurring_categories[category]=recurring_categories.get(category,Decimal("0"))+value
    ingested=forecast["ingested"]
    open_debt=forecast["open_debt"]
    future_financing=forecast["future_financing"]
    projected_installments=forecast["projected_installments"]
    projected_count=forecast["projected_count"]
    commitment_due_count=forecast["commitment_due_count"]
    commitment_due_total=forecast["commitment_due_total"]
    investment_count=forecast["investment_count"]
    monthly_series=[]
    month_keys=sorted({economic_month(item) for item in [*filtered_txs,*confirmed] if economic_month(item)})
    for month_key in month_keys:
        month_transactions=[item for item in filtered_txs if economic_month(item)==month_key]
        month_provisionals=[item for item in confirmed if economic_month(item)==month_key]
        month_totals=calculate_financial_totals(month_transactions,month_provisionals)
        monthly_series.append({"month":month_key,"income":float(month_totals["income"]),"expenses":float(month_totals["expenses"]),"net":float(month_totals["net_flow"])})
    latest_dt=datetime.fromisoformat(latest.replace("Z","+00:00")) if latest else None
    if latest_dt and latest_dt.tzinfo is None: latest_dt=latest_dt.replace(tzinfo=timezone.utc)
    latest_any_dt=data_anchor
    comparison=None if previous_totals is None else {"period_start":local_date(previous_start).isoformat(),"period_end":local_date(previous_end).isoformat(),"income":float(previous_totals["income"]),"expenses":float(previous_totals["expenses"]),"net_flow":float(previous_totals["net_flow"]),"income_change_pct":percentage_change(income,previous_totals["income"]),"expenses_change_pct":percentage_change(expenses,previous_totals["expenses"]),"net_change":float((income-expenses)-previous_totals["net_flow"])}
    return {"currency":"BRL","economic_period_basis":"competencia_declarada; sem competencia: data_evento_brasilia","cash_period_basis":"data_evento_brasilia","period":period,"period_start":local_date(range_start).isoformat() if range_start.year>1 else None,"period_end":local_date(range_end).isoformat(),"comparison":comparison,"monthly_series":monthly_series,"income":float(income),"expenses":float(expenses),"net_flow":float(income-expenses),"category_spending":[{"category":k,"amount":float(v)} for k,v in sorted(categories.items(),key=lambda x:x[1],reverse=True)],"nature_spending":[{"nature":nature,"label":NATURE_LABELS[nature],"amount":float(amount)} for nature,amount in nature_totals.items() if amount],"tax_total":float(nature_totals["IMPOSTO_TRIBUTO"]),"financial_cost_total":float(nature_totals["TARIFA_ENCARGO"]),"pending_reviews":pending,"provisional_count":provisional,"transaction_count":len(filtered_txs)+len(confirmed),"legacy_transaction_count":len(filtered_txs),"confirmed_capture_count":len(confirmed),"monthly_recurring_total":float(recurring_total),"monthly_recurring_count":recurring_count,"recurring_reference_month":latest_month,"monthly_recurring_by_category":[{"category":k,"amount":float(v)} for k,v in sorted(recurring_categories.items(),key=lambda x:x[1],reverse=True)],"overdue_or_partial_obligations":float(open_debt),"future_financing_principal":float(future_financing),"projected_installment_total":float(projected_installments),"projected_installment_count":projected_count,"commitment_due_count":commitment_due_count,"commitment_due_total":float(commitment_due_total),"investment_positions_available":investment_count>0,"investment_position_count":investment_count,"data_updated_until":latest_any,"legacy_data_updated_until":latest,"confirmed_capture_updated_until":latest_confirmed,"data_is_stale":bool(latest_any_dt and datetime.now(timezone.utc)-latest_any_dt>timedelta(days=7)),"legacy_data_is_stale":bool(latest_dt and datetime.now(timezone.utc)-latest_dt>timedelta(days=7)),"source_data_loaded_at":ingested.get("ingested_at").isoformat() if ingested and ingested.get("ingested_at") else None}

@app.get("/v1/dashboard", dependencies=[Depends(require_session)])
def dashboard(period:Literal["month","3m","6m","12m","year","all"]="month",date_from:date|None=None,date_to:date|None=None,db=Depends(database)):
    with mongo_snapshot(db) as (reading,consistency):
        result=_dashboard(period,date_from,date_to,reading)
    result["read_consistency"]=consistency
    result["read_at"]=datetime.now(timezone.utc).isoformat()
    return result

@app.get("/v1/dashboard/overview", dependencies=[Depends(require_session)])
def dashboard_overview(period:Literal["month","3m","6m","12m","year","all"]="month",year_start:date|None=None,month_start:date|None=None,month_end:date|None=None,db=Depends(database)):
    """Build all home summaries from one MongoDB snapshot."""
    with mongo_snapshot(db) as (reading,consistency):
        end=month_end or local_today()
        month_begin=month_start or end.replace(day=1)
        year_begin=year_start or end.replace(month=1,day=1)
        snapshot=snapshot_for(reading,[(period,None,None),("year",year_begin,end),("month",month_begin,end)]) if isinstance(reading,(Database,_SessionDatabase)) else (list(reading.transactions.find({},PROJECTION).limit(0)),list(reading.provisional_transactions.find({'status':'confirmed'},PROJECTION)))
        forecast=dashboard_forecast(reading)
        result={
            "selected":_dashboard(period,None,None,reading,snapshot,forecast),
            "year":_dashboard("year",year_begin,end,reading,snapshot,forecast),
            "month":_dashboard("month",month_begin,end,reading,snapshot,forecast),
        }
    result["read_consistency"]=consistency
    result["read_at"]=datetime.now(timezone.utc).isoformat()
    return result

def encode_movement_cursor(when, source, object_id, filter_key=None):
    value=[normalized_datetime(when).isoformat(),source,str(object_id)]
    if filter_key: value.append(filter_key)
    return base64.urlsafe_b64encode(json.dumps(value,separators=(",",":")).encode()).decode().rstrip("=")


def decode_movement_cursor(value, filter_key=None):
    try:
        payload=json.loads(base64.urlsafe_b64decode(value+"="*(-len(value)%4)))
        if len(payload) not in (3,4): raise ValueError()
        if len(payload)==4 and filter_key is not None and payload[3]!=filter_key: raise ValueError()
        when,source,object_id=payload[:3]
        if source not in {"ledger","capture"} or not ObjectId.is_valid(object_id): raise ValueError()
        return datetime.fromisoformat(when),source,ObjectId(object_id)
    except Exception:
        raise HTTPException(status_code=422,detail="Cursor de paginação inválido.")


@app.get("/v1/transactions", dependencies=[Depends(require_session)])
def transactions(
    limit:int=Query(100,ge=1,le=500),
    offset:int=Query(0,ge=0),
    cursor:str|None=None,
    search:str|None=Query(default=None,max_length=120),
    category:str|None=Query(default=None,max_length=120),
    account_id:str|None=Query(default=None,max_length=80),
    review_status:Literal["todos","pendente","validado"]="todos",
    date_from:date|None=None,
    date_to:date|None=None,
    period_basis:Literal['cash','economic']='cash',
    db=Depends(database),
):
    with mongo_snapshot(db) as (reading,consistency):
        result=_transaction_page(limit,offset,cursor,search,category,account_id,review_status,date_from,date_to,period_basis,reading)
    result['read_consistency']=consistency
    result['read_at']=datetime.now(timezone.utc).isoformat()
    return result


def _transaction_page(limit,offset,cursor,search,category,account_id,review_status,date_from,date_to,period_basis,db):
    query:dict[str,Any]={}
    account_filter=account_id if isinstance(account_id,str) and account_id.strip() else None
    if review_status=="pendente": query["status_revisao"]={"$ne":"validado"}
    elif review_status=="validado": query["status_revisao"]="validado"
    query.update(category_query("categoria",category))
    if account_filter:
        # Imported ledger rows point to the account document. Captures are
        # filtered separately by movements(), using the stable account key.
        identity,_ = movement_account_context(db,account_filter)
        query["account_id"] = identity if identity is not None else "__invalid_account_filter__"
    query=intersect(query,period_query(date_from,date_to,period_basis))
    query=intersect(query,search_query(db,db.transactions,'categoria',search))
    signature=movement_filter_key('transactions',search,category,account_filter,date_from,date_to,review_status,period_basis)
    total=db.transactions.count_documents(query)
    if cursor:
        after_date,_,after_id=decode_movement_cursor(cursor,signature)
        query={"$and":[query,{"$or":[{"data_evento":{"$lt":after_date}},{"data_evento":after_date,"_id":{"$lt":after_id}}]}]}
        offset=0
    if offset>10000: raise HTTPException(status_code=422,detail="Use paginação por cursor para consultar o histórico distante.")
    documents=list(db.transactions.find(query).sort([("data_evento",-1),("_id",-1)]).skip(offset).limit(limit+1))
    has_more=len(documents)>limit
    documents=documents[:limit]
    items=with_merchant_names(documents,db)
    with_installment_references(documents, items, db)
    linked_ids={str(x["_id"]) for x in documents}
    if linked_ids:
        for commitment in db.commitments.find({"linked_transactions.transaction_id":{"$in":list(linked_ids)}}):
            for link in commitment.get("linked_transactions",[]):
                if link.get("transaction_id") in linked_ids:
                    for result in items:
                        if result["id"]==link["transaction_id"]:
                            result["commitment"]={"id":commitment["commitment_id"],"name":commitment["name"],"kind":commitment["kind"]}
    return {
        "items":items,
        "limit":limit,
        "offset":offset,
        "total":total,
        "has_more":has_more,
        "next_cursor":encode_movement_cursor(documents[-1]["data_evento"],"ledger",documents[-1]["_id"],signature) if has_more and documents and ObjectId.is_valid(str(documents[-1]["_id"])) else None,
    }

class TransactionReviewUpdate(BaseModel):
    request_id:UUID|None=None
    status_revisao:Literal["pendente","validado"]
    expected_token:str|None=None

def resolve_pending_reviews_for_transaction(db, transaction_id:ObjectId, now:datetime, session=None) -> int:
    """Close stale match prompts when the user has explicitly validated the candidate ledger row.

    Validation does not create matching evidence; it only means this prompt no
    longer belongs in the active queue. Explicit match decisions remain the
    path that creates transaction evidence.
    """
    kwargs=session_args(session)
    pending=list(db.review_queue.find({
        "status":"pending",
        "candidate_type":"transaction",
        "candidate_transaction_id":transaction_id,
    },**kwargs))
    if not pending: return 0
    for review in pending:
        db.review_queue.update_one(
            {"_id":review["_id"],"status":"pending"},
            {"$set":{
                "status":"resolved",
                "decision":"candidate_validated",
                "resolution_reason":"candidate_transaction_validated",
                "resolved_at":now,
            }},**kwargs,
        )
    db.audit_log.insert_one({
        "event":"review_auto_resolved_after_transaction_validation",
        "transaction_id":transaction_id,
        "review_ids":[review.get("review_id") for review in pending],
        "created_at":now,
    },**kwargs)
    return len(pending)

def resolve_pending_reviews_for_provisional(db, provisional_id:str, now:datetime, reason:str) -> int:
    """Remove a provisional capture from the active match queue after its decision."""
    pending_by_id={}
    for field in ("provisional_id", "candidate_provisional_id"):
        for review in db.review_queue.find({"status":"pending",field:provisional_id}):
            pending_by_id[review["_id"]]=review
    pending=list(pending_by_id.values())
    if not pending: return 0
    for review in pending:
        db.review_queue.update_one(
            {"_id":review["_id"]},
            {"$set":{
                "status":"resolved",
                "decision":"provisional_decided",
                "resolution_reason":reason,
                "resolved_at":now,
            }},
        )
    db.audit_log.insert_one({
        "event":"review_auto_resolved_after_provisional_decision",
        "provisional_id":provisional_id,
        "review_ids":[review.get("review_id") for review in pending],
        "reason":reason,
        "created_at":now,
    })
    return len(pending)

@app.patch("/v1/transactions/{transaction_id}/review", dependencies=[Depends(require_session)])
@durable_mutation('transaction_review',('transaction_id',),'update')
def update_transaction_review(transaction_id:str, update:TransactionReviewUpdate, db=Depends(database)):
    try: object_id=ObjectId(transaction_id)
    except Exception: raise HTTPException(status_code=404,detail="Transaction not found")
    with mongo_transaction(db) as session:
        kwargs=session_args(session)
        lock_movement_insert(session_database(db,session))
        item=db.transactions.find_one({"_id":object_id},**kwargs)
        if item is None: raise HTTPException(status_code=404,detail="Transaction not found")
        require_source_token(item,update.expected_token)
        previous=item.get("status_revisao","pendente")
        now=datetime.now(timezone.utc)
        changed=previous!=update.status_revisao
        if changed:
            mutation={"$set":{"status_revisao":update.status_revisao,"updated_at":now}}
            if update.status_revisao=="validado": mutation["$set"]["revisado_em"]=now
            else: mutation["$unset"]={"revisado_em":""}
            filter_value=previous if "status_revisao" in item else {"$exists":False}
            try: result=db.transactions.update_one({"_id":object_id,"status_revisao":filter_value},mutation,**kwargs)
            except WriteError as exc:
                if exc.code==121: raise HTTPException(status_code=409,detail="O estado de revisão não passou pelas regras do dicionário financeiro.")
                raise
            if result is not None and result.matched_count==0:
                raise HTTPException(status_code=409,detail="A movimentação foi alterada. Recarregue antes de revisar.")
            db.audit_log.insert_one({"event":"transaction_review_status_updated","transaction_id":object_id,"source":"mobile_manual_review","before":{"status_revisao":previous},"after":{"status_revisao":update.status_revisao},"created_at":now},**kwargs)
        resolved_count=resolve_pending_reviews_for_transaction(db, object_id, now,session) if update.status_revisao=="validado" else 0
    if not changed:
        return {"item":with_merchant_name(item,db),"changed":False,"resolved_review_count":resolved_count}
    return {"item":with_merchant_name(db.transactions.find_one({"_id":object_id}),db),"changed":True,"resolved_review_count":resolved_count}

CLASSIFICATION_FIELDS=("tipo","natureza","categoria","subcategoria","metodo_pagamento","fluxo","competencia","localidade","localidade_contexto","status_conciliacao","detalhe")
RECURRENCE_TYPES=("recorrente_fixa","recorrente_variavel","eventual")
RECURRENCE_PERIODICITIES=("semanal","mensal","trimestral","semestral","anual","irregular","nao_aplicavel")
PAYMENT_METHOD_LABELS={"sem_parar":"Sem Parar"}

@app.get("/v1/transactions/classification-options", dependencies=[Depends(require_session)])
def transaction_classification_options(db=Depends(database)):
    return classification_cache.get(db, lambda: build_classification_options(db))

def build_classification_options(db):
    live_captures={"status":{"$in":ACTIVE_CAPTURES}}
    result={field:sorted({str(value) for value in db.transactions.distinct(field)
                         +db.provisional_transactions.distinct("categoria_sugerida" if field=="categoria" else field,live_captures)
                         if value not in (None,"")},key=str.casefold) for field in CLASSIFICATION_FIELDS}
    result["categoria"]=sorted(set(catalog_category_names(db)),key=str.casefold)
    known_platform_ids=[value for value in db.transactions.distinct("platform_id")+db.provisional_transactions.distinct("platform_id",live_captures) if value]
    platform_names={name for _,name in PLATFORMS.values()} | {str(value) for value in db.merchants.distinct("nome_padrao",{"_id":{"$in":known_platform_ids}}) if value} | {str(value) for value in db.merchants.distinct("nome_padrao",{"external_key":{"$regex":"^platform-user-"}}) if value}
    result["plataforma"]=sorted(platform_names,key=str.casefold)
    platform_keys={canonical_key(name) for name in platform_names}
    result["vendedor"]=sorted(str(value) for value in db.merchants.distinct("nome_padrao") if value not in (None,"") and canonical_key(str(value)) not in platform_keys)
    result["fornecedor"]=result["vendedor"]
    result["recorrencia_tipo"]=list(RECURRENCE_TYPES)
    result["recorrencia_periodicidade"]=list(RECURRENCE_PERIODICITIES)
    result["natureza"]=list(NATURES)
    result["natureza_labels"]=NATURE_LABELS
    result["metodo_pagamento_labels"]=PAYMENT_METHOD_LABELS
    result["categoria_subcategorias"]=category_subcategories(db)
    result["subcategoria"]=sorted({name for names in result["categoria_subcategorias"].values() for name in names},key=str.casefold)
    result["vendor_defaults"]=build_vendor_defaults(db)
    return result

class TransactionClassificationUpdate(BaseModel):
    request_id:UUID|None=None
    expected_token:str|None=None
    tipo:str|None=Field(default=None,min_length=1,max_length=100)
    natureza:Literal["COMPRA","IMPOSTO_TRIBUTO","TARIFA_ENCARGO","ESTORNO","RENDIMENTO","TRANSFERENCIA"]|None=None
    fornecedor:str|None=Field(default=None,min_length=1,max_length=160)
    vendedor:str|None=Field(default=None,max_length=160)
    plataforma:str|None=Field(default=None,max_length=160)
    categoria:str|None=Field(default=None,min_length=1,max_length=120)
    subcategoria:str|None=Field(default=None,max_length=120)
    metodo_pagamento:str|None=Field(default=None,max_length=100)
    fluxo:str|None=Field(default=None,pattern="^(entrada|saida)$")
    competencia:str|None=Field(default=None,min_length=1,max_length=20)
    localidade:str|None=Field(default=None,max_length=160)
    localidade_contexto:str|None=Field(default=None,min_length=1,max_length=100)
    status_conciliacao:str|None=Field(default=None,min_length=1,max_length=100)
    detalhe:str|None=Field(default=None,max_length=500)
    recorrencia_tipo:Literal["recorrente_fixa","recorrente_variavel","eventual"]|None=None
    recorrencia_periodicidade:Literal["semanal","mensal","trimestral","semestral","anual","irregular","nao_aplicavel"]|None=None
    supplier_preview_token:str|None=None
    classification_preview_token:str|None=None
    apply_similar_supplier:bool=False
    apply_similar_classification:bool=False
    validate_review:bool=False

def preview_signature(rows, context=""):
    payload={"context":context,"rows":sorted([json.dumps(json_safe(row),sort_keys=True,default=str) for row in rows])}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

@app.get("/v1/transactions/{transaction_id}/classification-match-preview", dependencies=[Depends(require_session)])
def classification_match_preview(transaction_id:str, db=Depends(database)):
    try: object_id=ObjectId(transaction_id)
    except Exception: raise HTTPException(status_code=404,detail="Transaction not found")
    item=db.transactions.find_one({"_id":object_id})
    if item is None: raise HTTPException(status_code=404,detail="Transaction not found")
    matches=batch_classification_matches(db,item)
    descriptor_matches=exact_descriptor_matches(db,item)
    return {"preview_token":preview_signature([c for c,_ in matches],str(object_id)),"total":len(matches),"same_description_count":len(descriptor_matches),"match_type":"specific_descriptor_and_flow","samples":[{"id":str(candidate["_id"]),"description":raw,"amount":json_safe(candidate.get("valor"))} for candidate,raw in matches[:8]]}

@app.get("/v1/transactions/{transaction_id}/supplier-match-preview", dependencies=[Depends(require_session)])
def supplier_match_preview(transaction_id:str, supplier:str=Query(min_length=1,max_length=160), db=Depends(database)):
    try: object_id=ObjectId(transaction_id)
    except Exception: raise HTTPException(status_code=404,detail="Transaction not found")
    item=db.transactions.find_one({"_id":object_id})
    if item is None: raise HTTPException(status_code=404,detail="Transaction not found")
    transaction_matches,provisional_matches=similar_supplier_records(db,item,supplier.strip())
    samples=[
        {"kind":kind,"id":str(candidate.get("_id") or candidate.get("provisional_id")),"description":raw,"score":round(score,3)}
        for kind,matches in (("transaction",transaction_matches),("provisional",provisional_matches))
        for candidate,score,raw in matches
    ]
    samples.sort(key=lambda value:value["score"],reverse=True)
    return {"preview_token":preview_signature([c for c,_,_ in transaction_matches+provisional_matches],supplier.strip()),"transaction_count":len(transaction_matches),"provisional_count":len(provisional_matches),"total":len(transaction_matches)+len(provisional_matches),"threshold":0.90,"samples":samples[:8]}

@app.patch("/v1/transactions/{transaction_id}/classification", dependencies=[Depends(require_session)])
@durable_mutation('transaction_classification',('transaction_id',),'update')
def update_transaction_classification(transaction_id:str, update:TransactionClassificationUpdate, db=Depends(database)):
    batch=update.apply_similar_supplier or update.apply_similar_classification
    if batch:
        command_id=str(uuid4())
        db.batch_commands.insert_one({"_id":command_id,"kind":"classification","transaction_id":transaction_id,"payload":update.model_dump(mode="json"),"status":"pending","attempts":0,"created_at":datetime.now(timezone.utc)})
        return attempt_classification_command(db,command_id)
    with mongo_transaction(db) as session:
        return _update_transaction_classification(transaction_id,update,session_database(db,session))


def attempt_classification_command(db,command_id):
    try: return run_classification_command(db,command_id)
    except HTTPException as exc:
        if 400<=exc.status_code<500:
            last_error=str(exc.detail)[:500]
            db.batch_commands.update_one({"_id":command_id,"status":"pending"},{"$set":{"status":"needs_review","last_error":last_error}})
            saved=db.batch_commands.find_one({"_id":command_id})
            if saved:
                exc._batch_command_needs_review={**saved,"status":"needs_review","last_error":last_error}
        raise


def run_classification_command(db,command_id):
    with mongo_transaction(db) as session:
        kwargs=session_args(session)
        command=db.batch_commands.find_one({"_id":command_id},**kwargs)
        if not command: raise HTTPException(status_code=404,detail="Comando de lote não encontrado.")
        if command["status"]=="completed": return command["result"]
        if command["status"]!="pending": raise HTTPException(status_code=409,detail="Comando de lote requer revisão.")
        claimed=db.batch_commands.update_one({"_id":command_id,"status":"pending"},{"$set":{"status":"running"}},**kwargs)
        if claimed is not None and claimed.matched_count==0: raise HTTPException(status_code=409,detail="Comando de lote já está em processamento.")
        result=_update_transaction_classification(command["transaction_id"],TransactionClassificationUpdate.model_validate(command["payload"]),session_database(db,session))
        db.batch_commands.update_one({"_id":command_id,"status":"running"},{"$set":{"status":"completed","result":json_safe(result),"completed_at":datetime.now(timezone.utc)}},**kwargs)
        return result


def recover_classification_commands(db,limit=20):
    cutoff=datetime.now(timezone.utc)-timedelta(seconds=30)
    recovered=0
    for command in db.batch_commands.find({"status":"pending","created_at":{"$lt":cutoff}}).sort("created_at",1).limit(limit):
        try:
            run_classification_command(db,command["_id"])
            recovered+=1
        except Exception as exc:
            attempts=command.get("attempts",0)+1
            db.batch_commands.update_one({"_id":command["_id"],"status":"pending"},{"$inc":{"attempts":1},"$set":{"last_error":safe_error(exc),"status":"needs_review" if attempts>=3 else "pending"}})
            logging.error("Could not recover classification command %s (%s)",command["_id"],safe_error(exc))
    return recovered


@app.get("/v1/batch-commands",dependencies=[Depends(require_session)])
def list_batch_commands(limit:int=Query(100,ge=1,le=500),cursor:str|None=None,db=Depends(database)):
    rows,info=workflow_page(db.batch_commands,{'status':{'$in':['pending','needs_review']}},'created_at',limit,cursor)
    return {'items':[json_safe({k:v for k,v in item.items() if k!='payload'}) for item in rows],**info}


@app.post("/v1/batch-commands/{command_id}/retry",dependencies=[Depends(require_session)])
def retry_batch_command(command_id:str,db=Depends(database)):
    changed=db.batch_commands.update_one({"_id":command_id,"status":"needs_review"},{"$set":{"status":"pending","attempts":0,"created_at":datetime.now(timezone.utc)}})
    if changed.matched_count==0: raise HTTPException(status_code=409,detail="O comando não está aguardando revisão.")
    return attempt_classification_command(db,command_id)


def _update_transaction_classification(transaction_id:str, update:TransactionClassificationUpdate, db):
    lock_movement_insert(db)
    try: object_id=ObjectId(transaction_id)
    except Exception: raise HTTPException(status_code=404,detail="Transaction not found")
    item=db.transactions.find_one({"_id":object_id})
    if item is None: raise HTTPException(status_code=404,detail="Transaction not found")
    require_source_token(item, update.expected_token)
    payload=update.model_dump(exclude_none=True)
    payload.pop('expected_token',None)
    payload.pop('request_id',None)
    apply_similar_supplier=bool(payload.pop("apply_similar_supplier",False))
    apply_similar_classification=bool(payload.pop("apply_similar_classification",False))
    validate_review=bool(payload.pop("validate_review",False))
    supplier_token=payload.pop("supplier_preview_token",None)
    classification_token=payload.pop("classification_preview_token",None)
    if apply_similar_supplier:
        name=payload.get("vendedor") or payload.get("fornecedor") or ""
        tx_matches,capture_matches=similar_supplier_records(db,item,name.strip())
        actual=preview_signature([c for c,_,_ in tx_matches+capture_matches],name.strip())
        if supplier_token!=actual:raise HTTPException(409,"A prévia de vendedores mudou. Confira o novo conjunto antes de aplicar.")
    if apply_similar_classification:
        actual=preview_signature([c for c,_ in batch_classification_matches(db,item)],str(object_id))
        if classification_token!=actual:raise HTTPException(409,"A prévia de classificação mudou. Confira o novo conjunto antes de aplicar.")
    recurrence_type=payload.pop("recorrencia_tipo",None)
    recurrence_periodicity=payload.pop("recorrencia_periodicidade",None)
    changes={key:value.strip() for key,value in payload.items()}
    if any(key in changes and not changes[key] for key in ('tipo','categoria','competencia','localidade_contexto','status_conciliacao')):
        raise HTTPException(422,'Os campos obrigatórios de classificação não podem ficar vazios.')
    if any(key in changes and changes[key]!=item.get(key) for key in ('natureza','fluxo','tipo')):
        if db.refund_claims.find_one({'_id':'ledger:'+transaction_id}) or db.commitments.find_one({'linked_transactions.transaction_id':transaction_id}):
            raise HTTPException(409,'Esta movimentação sustenta um vínculo financeiro. Revise o vínculo antes de mudar sua natureza ou direção.')
    if apply_similar_classification and any(key in changes for key in ('natureza','fluxo','tipo')):
        for peer,_ in batch_classification_matches(db,item):
            if any(key in changes and changes[key]!=peer.get(key) for key in ('natureza','fluxo','tipo')):
                identity=str(peer['_id'])
                if db.refund_claims.find_one({'_id':'ledger:'+identity}) or db.commitments.find_one({'linked_transactions.transaction_id':identity}):
                    raise HTTPException(409,'Um registro da prévia sustenta vínculo financeiro; revise o conjunto antes de aplicar.')
    effective_category=changes.get("categoria",item.get("categoria"))
    retired=taxonomy_overrides(db).get(taxonomy_key(effective_category),{}).get(taxonomy_key(changes.get("subcategoria")),{}).get("active") is False if "subcategoria" in changes else False
    if changes.get("subcategoria") and (effective_category in CURATED_SUBCATEGORIES or retired):
        allowed=set(category_subcategories(db).get(effective_category, []))
        if changes["subcategoria"] not in allowed:
            raise HTTPException(status_code=422,detail=f"Selecione uma subcategoria válida para {effective_category}.")
    existing_recurrence=item.get("recorrencia") or {}
    recurrence_changed=recurrence_type is not None or recurrence_periodicity is not None
    if recurrence_changed:
        effective_type=recurrence_type or existing_recurrence.get("tipo")
        if not effective_type:
            raise HTTPException(status_code=422,detail="Selecione primeiro o tipo de recorrência.")
        if effective_type=="eventual":
            effective_periodicity="nao_aplicavel"
        else:
            effective_periodicity=recurrence_periodicity or existing_recurrence.get("periodicidade") or "mensal"
        recurrence={**existing_recurrence,"tipo":effective_type,"periodicidade":effective_periodicity,"source":"mobile_manual_review"}
    else:
        recurrence=existing_recurrence
    if not changes and not recurrence_changed and not validate_review: raise HTTPException(status_code=422,detail="At least one classification field is required")
    if validate_review and not changes and not recurrence_changed and not apply_similar_classification and not apply_similar_supplier:
        return update_transaction_review(transaction_id,TransactionReviewUpdate(status_revisao="validado",expected_token=update.expected_token),db)
    supplier_name=changes.pop("vendedor",None)
    legacy_supplier=changes.pop("fornecedor",None)
    if supplier_name is None: supplier_name=legacy_supplier
    clear_supplier=supplier_name is not None and not supplier_name.strip()
    if clear_supplier: supplier_name=None
    platform_name=changes.pop("plataforma",None)
    clear_platform=platform_name=='' and any(key in item for key in ('platform_id','plataforma'))
    if changes.get("localidade"): changes["localidade"]=canonical_distinct(db,"localidade",changes["localidade"])
    validate_signed_flow({**item,**changes})
    if apply_similar_classification:
        for candidate,_ in batch_classification_matches(db,item):
            validate_signed_flow({**candidate,**changes})
    if "fluxo" in changes:
        if apply_similar_classification:
            # Validate every exact peer before the batch filters by sign, so a
            # mixed debit/credit descriptor cannot be partially rewritten.
            for candidate,_ in exact_descriptor_matches(db,item):
                validate_signed_flow({**candidate,"fluxo":changes["fluxo"]})
    current_merchant=db.merchants.find_one({"_id":item["merchant_id"]}) if item.get("merchant_id") else None
    merchant=find_canonical_merchant(db,supplier_name) if supplier_name else current_merchant
    merchant_changed=False
    if supplier_name and (not current_merchant or not merchant or current_merchant["_id"]!=merchant["_id"] or current_merchant.get("nome_padrao")!=merchant.get("nome_padrao")): merchant_changed=True
    before={key:item.get(key) for key in changes}
    changed={key:value for key,value in changes.items() if before.get(key)!=value}
    recurrence_changed=recurrence_changed and recurrence!=existing_recurrence
    if not changed and not merchant_changed and not (clear_supplier and item.get("merchant_id")) and not platform_name and not clear_platform and not recurrence_changed and not apply_similar_classification and not (apply_similar_supplier and supplier_name and merchant) and not validate_review: return {"item":with_merchant_name(item,db),"changed":False}
    now=datetime.now(timezone.utc)
    review_values={"status_revisao":"validado" if validate_review else "pendente"}
    if validate_review: review_values["revisado_em"]=now
    set_values={**changed,"updated_at":now,**review_values}
    if changed or merchant_changed or clear_supplier or platform_name or clear_platform or recurrence_changed:
        set_values["editado_em"]=now
    if recurrence_changed: set_values["recorrencia"]=recurrence
    created_merchant_id=None
    if merchant_changed:
        raw_source=item.get("estabelecimento_original") or item.get("descricao_original")
        platform_active=bool(item.get("platform_id") or platform_name or platform_identity(raw_source))
        if not merchant:
            merchant_id=ObjectId()
            use_raw=bool(raw_source) and (not platform_active or has_vendor_tokens_beyond_platform(raw_source,platform_name))
            raw=(raw_source if use_raw else supplier_name) or supplier_name
            normalized=re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9 ]"," ",str(raw).upper())).strip()
            merchant={"_id":merchant_id,"external_key":f"merchant-user-{uuid4()}","schema_version":2,"created_at":now,"updated_at":now,"tipo_entidade":"marca","nome_padrao":supplier_name,"aliases":[{"valor":raw,"normalizado":normalized,"status":"confirmado","source_record_ids":[]}]}
            db.merchants.insert_one(merchant)
            created_merchant_id=merchant_id
        _sync_merchant_alias(db,merchant,raw_source,supplier_name,platform_active,platform_name,item.get("merchant_id"),now)
        supplier_name=merchant["nome_padrao"]
        set_values["merchant_id"]=merchant["_id"]
        if "vendedor" in item: set_values["vendedor"]=supplier_name
        if "merchant_name" in item: set_values["merchant_name"]=supplier_name
    platform_changed=clear_platform
    platform_before=with_merchant_name(item,db).get('plataforma') if platform_name is not None else None
    unset_values={}
    if changes.get('metodo_pagamento')=='':
        set_values.pop('metodo_pagamento',None);unset_values['metodo_pagamento']=''
    if clear_platform:
        unset_values.update({key:'' for key in ('platform_id','plataforma') if key in item})
    if platform_name:
        platform=find_or_create_platform(db,platform_name,now)
        platform_changed=item.get("platform_id")!=platform["_id"]
        if platform_changed: set_values["platform_id"]=platform["_id"]
        if "plataforma" in item: set_values["plataforma"]=platform["nome_padrao"]
        if current_merchant and not supplier_name and canonical_key(current_merchant.get("nome_padrao",""))==canonical_key(platform["nome_padrao"]): clear_supplier=True
    if clear_supplier and item.get("merchant_id"):
        unset_values["merchant_id"]=""
    if clear_supplier:
        for key in ("vendedor","merchant_name"):
            if key in item: unset_values[key]=""
    if not validate_review and item.get("revisado_em"): unset_values["revisado_em"]=""
    try: db.transactions.update_one({"_id":object_id},{"$set":set_values,**({"$unset":unset_values} if unset_values else {})})
    except WriteError as exc:
        if created_merchant_id: db.merchants.delete_one({"_id":created_merchant_id})
        if exc.code==121: raise HTTPException(status_code=409,detail="O registro não passou pelas regras do dicionário financeiro. Revise os valores selecionados.")
        raise
    if changed or merchant_changed or clear_supplier or platform_changed or recurrence_changed:
        db.audit_log.insert_one({
            "event":"transaction_classification_updated",
            "transaction_id":object_id,
            "source":"mobile_manual_review",
            "before":{**{key:before.get(key) for key in changed},**({"vendedor":current_merchant.get("nome_padrao") if current_merchant else None} if merchant_changed or clear_supplier else {}),**({"plataforma":platform_before} if platform_changed else {}),**({"recorrencia":existing_recurrence or None} if recurrence_changed else {})},
            "after":{**changed,**({'metodo_pagamento':None} if 'metodo_pagamento' in unset_values else {}),**({"vendedor":None if clear_supplier else supplier_name} if merchant_changed or clear_supplier else {}),**({"plataforma":None if clear_platform else platform["nome_padrao"]} if platform_changed else {}),**({"recorrencia":recurrence} if recurrence_changed else {})},
            "change_reason":"user_review",
            "created_at":now,
        })
    propagated_transactions=0;propagated_provisionals=0
    validated_peer_ids:set[ObjectId]=set()
    if apply_similar_supplier and supplier_name and merchant:
        transaction_matches,provisional_matches=similar_supplier_records(db,item,supplier_name)
        transaction_ids=[candidate["_id"] for candidate,_,_ in transaction_matches]
        provisional_ids=[candidate["_id"] for candidate,_,_ in provisional_matches]
        if transaction_ids:
            propagated_transactions=db.transactions.update_many(
                {"_id":{"$in":transaction_ids},"$or":[{"merchant_id":{"$exists":False}},{"merchant_id":None}]},
                {"$set":{"merchant_id":merchant["_id"],"updated_at":now,**review_values},**({} if validate_review else {"$unset":{"revisado_em":""}})},
            ).modified_count
            if validate_review and propagated_transactions: validated_peer_ids.update(transaction_ids)
        if provisional_ids:
            propagated_provisionals=db.provisional_transactions.update_many(
                {"_id":{"$in":provisional_ids},"$or":[{"merchant_id":{"$exists":False}},{"merchant_id":None}]},
                {"$set":{"merchant_id":merchant["_id"],"merchant_name":supplier_name,"updated_at":now}},
            ).modified_count
        if propagated_transactions or propagated_provisionals:
            db.audit_log.insert_one({
                "event":"supplier_bulk_propagated","source_transaction_id":object_id,"source":"mobile_manual_review",
                "supplier_id":merchant["_id"],"supplier_name":supplier_name,"threshold":0.90,
                "transaction_count":propagated_transactions,"provisional_count":propagated_provisionals,
                "matched_records":[
                    {"kind":kind,"id":candidate["_id"],"score":round(score,3),"description":raw}
                    for kind,matches in (("transaction",transaction_matches),("provisional",provisional_matches))
                    for candidate,score,raw in matches
                ],
                "change_reason":"user_confirmed_supplier_with_high_confidence_propagation","created_at":now,
            })
    classification_propagated_count=0
    classification_matches=[]
    if apply_similar_classification:
        classification_matches=batch_classification_matches(db,item)
        safe_fields={"tipo","natureza","categoria","subcategoria","metodo_pagamento","fluxo","localidade","localidade_contexto","status_conciliacao"}
        classification_values={key:value for key,value in changes.items() if key in safe_fields}
        classification_unsets={}
        if classification_values.get('metodo_pagamento')=='':
            classification_values.pop('metodo_pagamento');classification_unsets['metodo_pagamento']=''
        if platform_name=='': classification_unsets.update({'platform_id':'','plataforma':''})
        if platform_name and platform: classification_values["platform_id"]=platform["_id"]
        if recurrence_type is not None or recurrence_periodicity is not None:
            classification_values["recorrencia"]={**recurrence,"source":"reviewed_exact_match"}
        if (classification_values or classification_unsets or validate_review) and classification_matches:
            classification_values.update({"updated_at":now,**review_values})
            classification_ids=[candidate["_id"] for candidate,_ in classification_matches]
            if not validate_review: classification_unsets['revisado_em']=''
            affected_fields=set(classification_values)|set(classification_unsets)
            # Matching uses a descriptor projection. Read the persisted peer
            # values before writing; projected candidates are not audit evidence.
            audit_fields=affected_fields-{'updated_at'}
            if platform_name is not None: audit_fields.update({'platform_id','plataforma'})
            projection={key:1 for key in affected_fields|audit_fields}
            classification_before={peer['_id']:deepcopy(peer) for peer in db.transactions.find({'_id':{'$in':classification_ids}},projection)}
            classification_propagated_count=db.transactions.update_many({"_id":{"$in":classification_ids}},{"$set":classification_values,**({'$unset':classification_unsets} if classification_unsets else {})}).modified_count
            if validate_review and classification_propagated_count: validated_peer_ids.update(classification_ids)
            if classification_propagated_count:
                classification_after={peer['_id']:peer for peer in db.transactions.find({'_id':{'$in':classification_ids}},projection)}
                matched_records=[]
                for candidate,raw in classification_matches:
                    peer_id=candidate['_id'];previous=classification_before.get(peer_id);final=classification_after.get(peer_id)
                    if previous is None or final is None:
                        raise HTTPException(409,'O conjunto de classificação mudou durante a gravação; nenhum lote foi confirmado.')
                    differs=lambda key:(key in previous)!=(key in final) or previous.get(key)!=final.get(key)
                    if not any(differs(key) for key in affected_fields):continue
                    peer_fields={key for key in audit_fields if differs(key)}
                    if platform_name is not None:peer_fields.update({'platform_id','plataforma'})
                    matched_records.append({'id':peer_id,'description':raw,
                        'before':{key:previous.get(key) for key in sorted(peer_fields)},
                        'after':{key:final.get(key) for key in sorted(peer_fields)},
                        'before_missing':sorted(key for key in peer_fields if key not in previous),
                        'after_missing':sorted(key for key in peer_fields if key not in final)})
                if len(matched_records)!=classification_propagated_count:
                    raise HTTPException(409,'A contagem do lote divergiu da auditoria; nenhuma alteração foi confirmada.')
                db.audit_log.insert_one({
                    "event":"transaction_classification_bulk_propagated","source_transaction_id":object_id,"source":"mobile_manual_review",
                    "match_type":"exact_normalized_descriptor","fields":sorted(key for key in {*classification_values,*classification_unsets} if key not in {"updated_at","status_revisao","revisado_em"}),
                    "transaction_count":classification_propagated_count,
                    "matched_records":matched_records,
                    "change_reason":"user_confirmed_exact_descriptor_propagation","created_at":now,
                })
    if validate_review:
        db.audit_log.insert_one({"event":"transaction_review_status_updated","transaction_id":object_id,"source":"mobile_manual_review","before":{"status_revisao":item.get("status_revisao","pendente")},"after":{"status_revisao":"validado"},"validated_peer_ids":sorted(validated_peer_ids,key=str),"validated_peer_count":len(validated_peer_ids),"created_at":now})
    resolved_review_count=0
    if validate_review:
        resolved_review_count+=resolve_pending_reviews_for_transaction(db, object_id, now)
        for peer_id in validated_peer_ids:
            if peer_id!=object_id:
                resolved_review_count+=resolve_pending_reviews_for_transaction(db, peer_id, now)
    updated=db.transactions.find_one({"_id":object_id})
    result_item=with_merchant_name(updated,db)
    with_installment_references([updated],[result_item],db)
    return {"item":result_item,"changed":True,"propagated_count":propagated_transactions+propagated_provisionals+classification_propagated_count,"propagated_transactions":propagated_transactions,"propagated_provisionals":propagated_provisionals,"classification_propagated_count":classification_propagated_count,"batch_validated_count":len(validated_peer_ids),"validated_count":1+len(validated_peer_ids) if validate_review else 0,"resolved_review_count":resolved_review_count}

@app.get("/v1/transactions/{transaction_id}", dependencies=[Depends(require_session)])
def transaction_detail(transaction_id:str, db=Depends(database)):
    try: doc=db.transactions.find_one({"_id":ObjectId(transaction_id)})
    except Exception: doc=None
    if doc is None: raise HTTPException(status_code=404,detail="Transaction not found")
    evidence=list(db.transaction_evidence.find({"transaction_id":doc["_id"]}))
    sources=[]
    source_id=(doc.get("source") or {}).get("source_record_id")
    if source_id: sources=list(db.source_records.find({"_id":source_id},{"checksum":1,"source_workbook":1,"source_sheet":1,"source_row":1,"batch_id":1}))
    result=with_merchant_name(doc,db); result["evidence"]=[json_safe(x) for x in evidence]; result["source_records"]=[json_safe(x) for x in sources]
    with_installment_references([doc], [result], db)
    commitment=db.commitments.find_one({"linked_transactions.transaction_id":transaction_id},{"commitment_id":1,"name":1,"kind":1})
    if commitment: result["commitment"]={"id":commitment["commitment_id"],"name":commitment["name"],"kind":commitment["kind"]}
    return result

@app.get("/v1/provisional", dependencies=[Depends(require_session)])
def provisional_transactions(status_filter:str|None=None,active_only:bool=False,limit:int=Query(100,ge=1,le=500),cursor:str|None=None,db=Depends(database)):
    query={"status":status_filter} if status_filter else {"status":{"$in":["provisional","enriched"] if active_only else ["provisional","enriched","confirmed"]}}
    documents,page_info=workflow_page(db.provisional_transactions,query,"data_evento",limit,cursor)
    items=with_merchant_names(documents,db)
    event_ids={item.get("event_id") for item in documents if item.get("event_id")}
    events={item["event_id"]:item.get("source_type") for item in db.raw_events.find({"event_id":{"$in":list(event_ids)}},{"event_id":1,"source_type":1})} if event_ids else {}
    for result in items:
        result["source_type"]=events.get(result.get("event_id"))
    linked_ids={item["provisional_id"] for item in items if item.get("provisional_id")}
    if linked_ids:
        links={}
        for commitment in db.commitments.find({"linked_transactions.provisional_id":{"$in":list(linked_ids)}}):
            for link in commitment.get("linked_transactions",[]):
                if link.get("provisional_id") in linked_ids:
                    links[link["provisional_id"]]={"id":commitment["commitment_id"],"name":commitment["name"],"kind":commitment["kind"]}
        for result in items:
            if result.get("provisional_id") in links: result["commitment"]=links[result["provisional_id"]]
    return {"items":items,**page_info}

@app.get('/v1/provisional/{provisional_id}',dependencies=[Depends(require_session)])
def get_provisional(provisional_id:str,db=Depends(database)):
    with mongo_snapshot(db) as (reading,consistency):
        item=reading.provisional_transactions.find_one({'provisional_id':provisional_id})
        if not item:raise HTTPException(404,'Captura não encontrada. Preserve o rascunho para conferência.')
        result=with_merchant_name(item,reading)
        event=reading.raw_events.find_one({'event_id':item.get('event_id')})
        result['source_type']=event.get('source_type') if event else None
        if item.get('status')=='confirmed':
            display_capture_review(result,historical_statement_reviews(reading,{'provisional_id':provisional_id,'status':'confirmed'}))
        return {'item':result,'read_consistency':consistency}

def cursor_movements(limit, cursor, search, category, account_id, date_from, date_to, db, review_status=None, offset=0, period_basis='cash', spending_only=False):
    legacy_query={}
    capture_query={"status":"confirmed"} if review_status is not None or spending_only else {"status":{"$in":["provisional","enriched","confirmed"]}}
    legacy_query.update(category_query("categoria",category))
    capture_query.update(category_query("categoria_sugerida",category))
    account_id=(account_id or '').strip()
    if account_id:
        identity,key=movement_account_context(db,account_id)
        legacy_query["account_id"]=identity if identity is not None else "__invalid__"
        capture_query.update(capture_account_query(identity,key))
    window=period_query(date_from,date_to,period_basis)
    legacy_query=intersect(legacy_query,window)
    capture_query=intersect(capture_query,window)
    legacy_query=intersect(legacy_query,search_query(db,db.transactions,'categoria',search))
    capture_query=intersect(capture_query,search_query(db,db.provisional_transactions,'categoria_sugerida',search))
    signature=movement_filter_key('movements',search,category,account_id,date_from,date_to,review_status,period_basis,spending_only)
    cursor_position=decode_movement_cursor(cursor,signature) if cursor else None
    historical={}
    if review_status is not None:
        if review_status=="pendente": legacy_query["status_revisao"]={"$ne":"validado"}
        elif review_status=="validado": legacy_query["status_revisao"]="validado"
        historical=historical_statement_reviews(db,capture_query)
        capture_query=capture_review_query(capture_query,review_status,historical)
    total=db.transactions.count_documents(legacy_query)+db.provisional_transactions.count_documents(capture_query)
    def bounded(query,source):
        if not cursor: return query
        when,last_source,last_id=cursor_position
        same_date=source==last_source
        if source=="ledger" and last_source=="capture":
            bound={"data_evento":{"$lte":when}}
        elif source=="capture" and last_source=="ledger":
            bound={"data_evento":{"$lt":when}}
        elif same_date:
            bound={"$or":[{"data_evento":{"$lt":when}},{"data_evento":when,"_id":{"$lt":last_id}}]}
        else:
            bound={"data_evento":{"$lt":when}}
        return {"$and":[query,bound]}
    if cursor: offset=0
    needed=offset+limit
    order=lambda pair:(normalized_datetime(pair[1]["data_evento"]),1 if pair[0]=="capture" else 0,str(pair[1]["_id"]))
    spending_total=None
    if spending_only:
        # Bound by category/account/economic window before applying the exact
        # shared policy. Filtering only the first page would corrupt counts.
        legacy=list(db.transactions.find(legacy_query))
        captures=captures_not_in_ledger(legacy,list(db.provisional_transactions.find(capture_query)))
        pairs=[(source,item) for source,rows in [('ledger',legacy),('capture',captures)] for item in rows
               if effects(item,source=='capture')['role'] in {'expense','refund'}]
        total=len(pairs)
        spending_total=sum((effects(item,source=='capture')['expenses'] for source,item in pairs),Decimal('0'))
        if cursor_position:
            when,source,identity=cursor_position
            boundary=(normalized_datetime(when),1 if source=='capture' else 0,str(identity))
            pairs=[pair for pair in pairs if order(pair)<boundary]
    else:
        legacy=list(db.transactions.find(bounded(legacy_query,"ledger")).sort([("data_evento",-1),("_id",-1)]).limit(needed+1))
        captures=list(db.provisional_transactions.find(bounded(capture_query,"capture")).sort([("data_evento",-1),("_id",-1)]).limit(needed+1))
        pairs=[("ledger",item) for item in legacy]+[("capture",item) for item in captures]
    pairs.sort(key=order,reverse=True)
    has_more=len(pairs)>needed
    selected=pairs[offset:needed]
    ledger_display={str(item["id"]):item for item in with_merchant_names([doc for source,doc in selected if source=="ledger"],db)}
    capture_display={str(item["provisional_id"]):item for item in with_merchant_names([doc for source,doc in selected if source=="capture"],db)}
    items=[]
    for source,doc in selected:
        item=ledger_display[str(doc["_id"])] if source=="ledger" else capture_display[str(doc["provisional_id"])]
        item["movement_source"]=source
        if source=="capture" and review_status is not None: display_capture_review(item,historical)
        items.append(item)
    event_ids=[doc.get("event_id") for source,doc in selected if source=="capture" and doc.get("event_id")]
    if event_ids:
        event_types={event["event_id"]:event.get("source_type") for event in db.raw_events.find({"event_id":{"$in":event_ids}},{"event_id":1,"source_type":1})}
        for item in items:
            if item["movement_source"]=="capture": item["source_type"]=event_types.get(item.get("event_id"))
    ledger_ids=[item["id"] for item in items if item["movement_source"]=="ledger"]
    capture_ids=[item["provisional_id"] for item in items if item["movement_source"]=="capture"]
    if ledger_ids or capture_ids:
        links={}
        for commitment in db.commitments.find({"$or":[{"linked_transactions.transaction_id":{"$in":ledger_ids}},{"linked_transactions.provisional_id":{"$in":capture_ids}}]}):
            for link in commitment.get("linked_transactions",[]):
                key=link.get("transaction_id") or link.get("provisional_id")
                if key: links[key]={"id":commitment["commitment_id"],"name":commitment["name"],"kind":commitment["kind"]}
        for item in items:
            linked=links.get(item.get("id") or item.get("provisional_id"))
            if linked: item["commitment"]=linked
    decorate_purchase_links(db, items)
    next_cursor=encode_movement_cursor(selected[-1][1]["data_evento"],selected[-1][0],selected[-1][1]["_id"],signature) if has_more and selected else None
    return {"items":items,"limit":limit,"offset":offset,"total":total,"has_more":has_more,"next_cursor":next_cursor,
            'period_basis':period_basis,'spending_only':spending_only,'spending_total':float(spending_total) if spending_total is not None else None}


@app.get("/v1/review-records", dependencies=[Depends(require_session)])
def review_records(
    limit:int=Query(50,ge=1,le=100),
    offset:int=Query(0,ge=0,le=10000),
    cursor:str|None=None,
    search:str|None=Query(default=None,max_length=120),
    category:str|None=Query(default=None,max_length=120),
    account_id:str|None=Query(default=None,max_length=80),
    review_status:Literal["todos","pendente","validado"]="todos",
    date_from:date|None=None,
    date_to:date|None=None,
    period_basis:Literal['cash','economic']='cash',
    db=Depends(database),
):
    """Review the imported ledger and confirmed captures in one stable page."""
    filter_key=review_filter_key(review_status,search,category,account_id,date_from,date_to)+':'+period_basis
    movement_cursor=decode_review_cursor(cursor,filter_key) if cursor else None
    with mongo_snapshot(db) as (reading,consistency):
        result=cursor_movements(limit,movement_cursor,search,category,account_id,date_from,date_to,reading,
            review_status=review_status,offset=offset,period_basis=period_basis)
        if result.get('next_cursor'): result['next_cursor']=encode_review_cursor(result['next_cursor'],filter_key)
        return {**result,"read_consistency":consistency}


@app.get("/v1/movements/{source}/{identity}/deletion", dependencies=[Depends(require_session)])
def movement_deletion_preview(source:Source, identity:str, db=Depends(database)):
    return preview_deletion(db, source, identity)

@app.delete("/v1/movements/{source}/{identity}", dependencies=[Depends(require_session)])
def remove_movement(source:Source, identity:str, body:DeleteMovement, db=Depends(database)):
    try:
        return delete_movement(db, source, identity, body)
    except DuplicateKeyError:
        raise HTTPException(409,"Outra exclusão foi confirmada ao mesmo tempo. Recarregue o extrato.")

@app.get("/v1/movements", dependencies=[Depends(require_session)])
def movements(
    limit:int=Query(50,ge=1,le=100),
    offset:int=Query(0,ge=0),
    cursor_mode:bool=False,
    cursor:str|None=None,
    search:str|None=Query(default=None,max_length=120),
    category:str|None=Query(default=None,max_length=120),
    account_id:str|None=Query(default=None,max_length=80),
    date_from:date|None=None,
    date_to:date|None=None,
    period_basis:Literal['cash','economic']='cash',
    spending_only:bool=False,
    db=Depends(database),
):
    """One stable page across the legacy ledger and captured movements."""
    if not (cursor_mode or cursor) and offset>10000:
        raise HTTPException(status_code=422,detail="Use paginação por cursor para consultar o histórico distante.")
    with mongo_snapshot(db) as (reading,consistency):
        result=cursor_movements(limit,cursor if cursor_mode or cursor else None,search,category,
            account_id if isinstance(account_id,str) else None,date_from,date_to,reading,
            offset=0 if cursor_mode or cursor else offset,period_basis=period_basis,spending_only=spending_only)
    result['read_consistency']=consistency
    result['read_at']=datetime.now(timezone.utc).isoformat()
    return result

class ReparseIn(BaseModel):
    request_id:UUID|None=None
    expected_revision:int=Field(ge=1)

@app.post("/v1/provisional/{provisional_id}/reparse",dependencies=[Depends(require_session)])
@durable_mutation('capture_reparse',('provisional_id',))
def reparse_provisional(provisional_id:str,body:ReparseIn,db=Depends(database)):
    item=db.provisional_transactions.find_one({"provisional_id":provisional_id})
    if not item: raise HTTPException(404,"Captura provisória não encontrada.")
    if item.get('revision',1)!=body.expected_revision: raise HTTPException(409,'A captura mudou. Recarregue.')
    if item.get('status') not in {'provisional','enriched'}: raise HTTPException(409,'Somente capturas ainda em revisão podem ser reprocessadas.')
    event=db.raw_events.find_one({"event_id":item.get("event_id")})
    if not event or event.get("source_type")!="android_notification": raise HTTPException(422,"Esta captura não veio de uma notificação Android.")
    enriched=enrich_notification_event(dict(event));parsed=enriched.get("parsed_payload") or {}
    merchant=str(parsed.get("merchant_raw") or "").strip();stamp=parsed.get("event_timestamp")
    if not merchant: raise HTTPException(422,"O texto original não contém um vendedor reconhecível.")
    when=parse_datetime(stamp).astimezone(timezone.utc) if isinstance(stamp,str) else item.get("data_evento")
    with mongo_transaction(db) as session:
        writing=session_database(db,session);lock_movement_insert(writing)
        current=writing.provisional_transactions.find_one({'_id':item['_id']})
        current_event=writing.raw_events.find_one({'_id':event['_id']})
        require_source_token(current,source_token(item));require_source_token(current_event,source_token(event))
        if current.get('status') not in {'provisional','enriched'} or writing.commitments.find_one({'linked_transactions.provisional_id':provisional_id}) or writing.refund_claims.find_one({'_id':'capture:'+provisional_id}) or writing.transaction_evidence.find_one({'event_id':item['event_id'],'status':{'$ne':'reversed'}}):
            raise HTTPException(409,'A captura já sustenta uma decisão ou vínculo. Revise os dados atuais.')
        now=datetime.now(timezone.utc);changes={"merchant_raw":merchant,"merchant_normalized":canonical_key(merchant).replace(" ",""),"data_evento":when,"updated_at":now,'revision':current.get('revision',1)+1}
        merchant_entity=find_canonical_merchant(writing,merchant);detected_platform=platform_identity(merchant)
        if detected_platform:
            platform=find_or_create_platform(writing,detected_platform[1],now);changes['platform_id']=platform['_id']
        if merchant_entity and (not detected_platform or merchant_entity['_id']!=changes.get('platform_id')):
            changes.update({'merchant_id':merchant_entity['_id'],'merchant_name':merchant_entity['nome_padrao']})
        apply_historical_capture_defaults(writing,changes,base_item=current)
        before={key:json_safe(item.get(key)) for key in changes if key!='updated_at'}
        writing.provisional_transactions.update_one({'_id':item['_id']},{'$set':changes})
        writing.raw_events.update_one({'_id':event['_id']},{'$set':{'parsed_payload':parsed,'parser_version':enriched.get('parser_version'),'updated_at':now}})
        writing.audit_log.insert_one({'event':'provisional_notification_reparsed','provisional_id':provisional_id,'before':before,'after':json_safe(changes),'parser_version':enriched.get('parser_version'),'created_at':now})
        return {'item':with_merchant_name(writing.provisional_transactions.find_one({'_id':item['_id']}),writing),'changed':True}

class ProvisionalUpdate(BaseModel):
    request_id:UUID|None=None
    action:Literal['confirm','cancel']|None=None
    expected_revision:int|None=Field(default=None,ge=1)
    validate_review:bool|None=None
    fornecedor:str|None=Field(default=None,min_length=1,max_length=160)
    vendedor:str|None=Field(default=None,max_length=160)
    plataforma:str|None=Field(default=None,max_length=160)
    valor:Decimal|None=None
    data_evento:datetime|None=None
    categoria_sugerida:str|None=Field(default=None,max_length=120)
    subcategoria:str|None=Field(default=None,max_length=120)
    tipo:str|None=Field(default=None,min_length=1,max_length=100)
    natureza:Literal["COMPRA","IMPOSTO_TRIBUTO","TARIFA_ENCARGO","ESTORNO","RENDIMENTO","TRANSFERENCIA"]|None=None
    metodo_pagamento:str|None=Field(default=None,max_length=100)
    fluxo:str|None=Field(default=None,pattern="^(entrada|saida)$")
    localidade:str|None=Field(default=None,max_length=160)
    detalhe:str|None=Field(default=None,max_length=500)
    account_profile_id:str|None=None
    from_account_key:str|None=None
    to_account_key:str|None=None

    @model_validator(mode='after')
    def financial_wall_clock(self):
        if self.data_evento is not None:
            self.data_evento=parse_datetime(self.data_evento).astimezone(timezone.utc)
        return self

def validate_signed_flow(item:dict[str,Any]) -> None:
    value=decimal_amount(item.get("valor"))
    flow=item.get("fluxo")
    if value is not None and flow in {"entrada","saida"} and (value<0)!=(flow=="saida"):
        raise HTTPException(status_code=422,detail="O fluxo deve corresponder ao sinal do valor.")
    if value is not None and value < 0 and (item.get('natureza')=='ESTORNO' or item.get('tipo')=='estorno'):
        raise HTTPException(422,'Estorno recebido exige valor positivo e fluxo de entrada. Registre uma reversão como saída explícita, sem a natureza Estorno.')

def validate_confirmed_provisional(item:dict[str,Any], db=None) -> None:
    value=decimal_amount(item.get("valor"))
    if value is None or not value.is_finite() or value == 0 or abs(value)>Decimal("100000000"):
        raise HTTPException(422,"Informe um valor financeiro válido e diferente de zero.")
    validate_signed_flow(item)
    if item.get("account_profile_id"):
        validate_account_key(item["account_profile_id"],db)
    if not str(item.get("tipo") or "").strip() or item.get("fluxo") not in {"entrada","saida"}:
        raise HTTPException(status_code=422,detail="Informe tipo e fluxo antes de confirmar a captura.")
    if item.get("natureza")=="TRANSFERENCIA":
        transfer=item.get("transfer") or {}
        origin=transfer.get("from_account_key");destination=transfer.get("to_account_key")
        validate_account_key(origin,db);validate_account_key(destination,db)
        if origin==destination: raise HTTPException(status_code=422,detail="Origem e destino devem ser contas diferentes.")

@app.patch("/v1/provisional/{provisional_id}", dependencies=[Depends(require_session)])
@durable_mutation('capture_update',('provisional_id',),'update')
def update_provisional(provisional_id:str, update:ProvisionalUpdate, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session)
        result=_atomic_update_provisional(provisional_id, update, db=writing)
        if update.action is not None:
            result['decision']=_atomic_decide_provisional(provisional_id,ProvisionalAction(action=update.action,expected_revision=result['item'].get('revision',1)),writing)
            result['item']=with_merchant_name(writing.provisional_transactions.find_one({'provisional_id':provisional_id}),writing)
        result.update({'persisted':True,'provisional_id':provisional_id,'action':update.action})
        return result

def _atomic_update_provisional(provisional_id:str, update:ProvisionalUpdate, db=Depends(database)):
    lock_movement_insert(db)
    item=db.provisional_transactions.find_one({"provisional_id":provisional_id,"status":{"$in":["provisional","enriched","confirmed"]}})
    if not item: raise HTTPException(status_code=404,detail="Movimentação editável não encontrada")
    values=update.model_dump(exclude_none=True)
    values.pop('request_id',None)
    values.pop('action',None)
    expected=values.pop("expected_revision",None)
    validate_review=values.pop("validate_review",None)
    if expected is None and item.get('revision') is not None:raise HTTPException(428,'Reabra a captura para obter a revisão atual.')
    if expected is not None and expected!=item.get("revision",1): raise HTTPException(409,"A captura mudou. Recarregue antes de salvar.")
    if not values and validate_review is None and update.action is None: raise HTTPException(status_code=422,detail="At least one field is required")
    if validate_review is not None and item.get("status")!="confirmed":
        raise HTTPException(422,"Confirme a captura antes de alterar o status de revisão do registro.")
    supplier=values.pop("vendedor",None)
    legacy_supplier=values.pop("fornecedor",None)
    if supplier is None: supplier=legacy_supplier
    clear_supplier=supplier is not None and not supplier.strip()
    if clear_supplier: supplier=None
    platform_name=values.pop("plataforma",None)
    if platform_name is not None: platform_name=platform_name.strip()
    from_account_key=values.pop("from_account_key",None)
    to_account_key=values.pop("to_account_key",None)
    account_profile_id=values.pop("account_profile_id",None)
    clear_account_profile=account_profile_id==""
    nature=values.get("natureza") or item.get("natureza")
    if nature=="TRANSFERENCIA" and (from_account_key is not None or to_account_key is not None or "natureza" in values):
        current_transfer=item.get("transfer") or {}
        origin=from_account_key or current_transfer.get("from_account_key")
        destination=to_account_key or current_transfer.get("to_account_key")
        validate_account_key(origin,db);validate_account_key(destination,db)
        if origin==destination: raise HTTPException(status_code=422,detail="Origem e destino devem ser contas diferentes.")
        if supplier: raise HTTPException(status_code=422,detail="Transferência própria não possui vendedor.")
        if platform_name: raise HTTPException(status_code=422,detail="Transferência própria não possui plataforma.")
        clear_supplier=True
        values["transfer"]=(dict(current_transfer) if (current_transfer.get("from_account_key"),current_transfer.get("to_account_key"))==(origin,destination) else {"from_account_key":origin,"to_account_key":destination,"reconciliation_status":"aguardando_extratos"})
    elif from_account_key is not None or to_account_key is not None:
        raise HTTPException(status_code=422,detail="Contas de origem e destino exigem natureza Transferência.")
    elif account_profile_id:
        validate_account_key(account_profile_id,db)
        values["account_profile_id"]=account_profile_id
        legacy_account=legacy_accounts_by_profile(db).get(account_profile_id)
        if legacy_account: values['account_id']=legacy_account['_id']
    if nature=="TRANSFERENCIA" and account_profile_id:
        raise HTTPException(status_code=422,detail="Use as contas de origem e destino para uma transferência.")
    if "valor" in values:
        amount=Decimal(str(values["valor"])).quantize(Decimal("0.01"))
        if not amount.is_finite() or not amount or abs(amount)>Decimal("100000000"): raise HTTPException(status_code=422,detail="Invalid amount")
        values["valor"]=Decimal128(str(amount))
    values={key:(value.strip() if isinstance(value,str) else value) for key,value in values.items()}
    if any(isinstance(value,str) and not value and key not in {'categoria_sugerida','subcategoria','metodo_pagamento','localidade','detalhe'} for key,value in values.items()):
        raise HTTPException(422,"Os campos informados não podem ficar vazios.")
    final={**item,**values}
    if nature!="TRANSFERENCIA": final.pop("transfer",None)
    if clear_account_profile: final.pop("account_profile_id",None)
    validate_signed_flow(final)
    if item.get("status")=="confirmed": validate_confirmed_provisional(final,db)
    effective_category=values.get("categoria_sugerida",item.get("categoria_sugerida"))
    retired=taxonomy_overrides(db).get(taxonomy_key(effective_category),{}).get(taxonomy_key(values.get("subcategoria")),{}).get("active") is False if "subcategoria" in values else False
    if values.get("subcategoria") and (effective_category in CURATED_SUBCATEGORIES or retired):
        allowed=set(category_subcategories(db).get(effective_category, []))
        if values["subcategoria"] not in allowed:
            raise HTTPException(status_code=422,detail=f"Selecione uma subcategoria válida para {effective_category}.")
    if values.get("localidade"): values["localidade"]=canonical_distinct(db,"localidade",values["localidade"])
    financial_changed=any(key in values and values[key]!=item.get(key) for key in ("valor","data_evento","natureza","fluxo","tipo")) or (from_account_key is not None and from_account_key!=(item.get("transfer") or {}).get("from_account_key")) or (to_account_key is not None and to_account_key!=(item.get("transfer") or {}).get("to_account_key")) or (account_profile_id is not None and account_profile_id!=(item.get("account_profile_id") or ""))
    if financial_changed and db.refund_claims.find_one({"_id":"capture:"+provisional_id}):
        raise HTTPException(409,"Este crédito sustenta um caso resolvido; a correção exige revisão do vínculo.")
    if financial_changed and db.commitments.find_one({"linked_transactions.provisional_id":provisional_id}):
        raise HTTPException(409,"Desvincule esta captura do compromisso antes de alterar seus dados financeiros.")
    if financial_changed and db.transaction_evidence.find_one({'provisional_id':provisional_id,'status':{'$ne':'reversed'}}):
        raise HTTPException(409,'Esta captura sustenta evidências vinculadas. Revise os vínculos antes de alterar seus dados financeiros.')
    if financial_changed and nature=="TRANSFERENCIA" and item.get("transfer"):
        transfer=values.get("transfer") or item["transfer"]
        values["transfer"]={"from_account_key":transfer["from_account_key"],"to_account_key":transfer["to_account_key"],"reconciliation_status":"aguardando_extratos"}
    before={key:json_safe(item.get(key)) for key in values}
    changed={key:value for key,value in values.items() if item.get(key)!=value}
    now=datetime.now(timezone.utc)
    previous_vendor=with_merchant_name(item,db).get("vendedor")
    previous_platform=with_merchant_name(item,db).get('plataforma') if platform_name is not None else None
    if supplier and supplier!=with_merchant_name(item,db).get("vendedor"):
        raw_source=item.get("merchant_raw")
        platform_active=bool(platform_name or item.get("platform_id") or platform_identity(raw_source))
        use_raw=bool(raw_source) and (not platform_active or has_vendor_tokens_beyond_platform(raw_source,platform_name))
        raw=(raw_source if use_raw else supplier) or supplier
        normalized=re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9 ]"," ",str(raw).upper())).strip()
        merchant=find_canonical_merchant(db,supplier)
        if not merchant:
            merchant={"_id":ObjectId(),"external_key":f"merchant-user-{uuid4()}","schema_version":2,"created_at":now,"updated_at":now,"tipo_entidade":"marca","nome_padrao":supplier,"aliases":[{"valor":raw,"normalizado":normalized,"status":"confirmado","source_record_ids":[]}]}
            db.merchants.insert_one(merchant)
        _sync_merchant_alias(db,merchant,raw_source,supplier,platform_active,platform_name,item.get("merchant_id"),now)
        changed.update({"merchant_id":merchant["_id"],"merchant_name":merchant["nome_padrao"]})
        before["vendedor"]=previous_vendor
    if platform_name:
        platform=find_or_create_platform(db,platform_name,now)
        if item.get("platform_id")!=platform["_id"]: changed["platform_id"]=platform["_id"]
        if previous_vendor and not supplier and canonical_key(previous_vendor)==canonical_key(platform["nome_padrao"]): clear_supplier=True
        if "platform_id" in changed: before["plataforma"]=with_merchant_name(item,db).get("plataforma")
    unset_values={}
    if account_profile_id is not None or nature=='TRANSFERENCIA':
        if clear_account_profile or nature=='TRANSFERENCIA' or not legacy_accounts_by_profile(db).get(account_profile_id):
            if 'account_id' in item:
                unset_values['account_id']=''
                before['account_id']=json_safe(item['account_id'])
        if (account_profile_id is not None and account_profile_id != (item.get('account_profile_id') or '')) or (nature=='TRANSFERENCIA' and item.get('account_id')):
            before['account_profile_id']=item.get('account_profile_id')
            before['account_id']=json_safe(item.get('account_id'))
            if not item.get('source_account_identity'):
                changed['source_account_identity']={key:item[key] for key in ('account_id','account_profile_id') if item.get(key)}
    if values.get('metodo_pagamento')=='':
        changed.pop('metodo_pagamento',None);unset_values['metodo_pagamento']=''
    if platform_name=='':
        platform_unsets={key:'' for key in ('platform_id','plataforma') if key in item}
        if platform_unsets:
            unset_values.update(platform_unsets);before['plataforma']=previous_platform
    if clear_account_profile and item.get("account_profile_id"): unset_values["account_profile_id"]=""
    if nature=="TRANSFERENCIA" and item.get("account_profile_id"): unset_values["account_profile_id"]=""
    if nature=="TRANSFERENCIA" and item.get("platform_id"): unset_values["platform_id"]=""
    if nature!="TRANSFERENCIA" and item.get("transfer"): unset_values["transfer"]=""
    if clear_supplier and (item.get("merchant_id") or item.get("merchant_name")):
        unset_values.update({"merchant_id":"","merchant_name":""})
        before["vendedor"]=previous_vendor
    if item.get("status")=="confirmed" and (changed or unset_values or validate_review is not None):
        next_review="validado" if validate_review else "pendente"
        if changed or unset_values or item.get("status_revisao")!=next_review:
            before["status_revisao"]=item.get("status_revisao")
            changed["status_revisao"]=next_review
            if next_review=="validado": changed["revisado_em"]=now
            elif item.get("revisado_em"): unset_values["revisado_em"]=""
    if not changed and not unset_values:
        result=with_merchant_name(item,db)
        if item.get('status')=='confirmed':
            historical=historical_statement_reviews(db,{'provisional_id':provisional_id,'status':'confirmed'})
            display_capture_review(result,historical)
        return {"item":result,"changed":False}
    changed["updated_at"]=now
    changed["revision"]=item.get("revision",1)+1
    db.provisional_transactions.update_one({"_id":item["_id"]},{"$set":changed,**({"$unset":unset_values} if unset_values else {})})
    db.audit_log.insert_one({"event":"provisional_transaction_updated","provisional_id":provisional_id,"source":"mobile_manual_review","before":before,"after":{**json_safe(changed),**{key:None for key in unset_values if key not in {'merchant_id','merchant_name','platform_id'}},**({'plataforma':None} if 'platform_id' in unset_values else {}),**({"vendedor":None} if 'merchant_id' in unset_values or 'merchant_name' in unset_values else {"vendedor":supplier} if supplier and "merchant_id" in changed else {})},"change_reason":"user_review","created_at":now})
    return {"item":with_merchant_name(db.provisional_transactions.find_one({"_id":item["_id"]}),db),"changed":True}

class ProvisionalAction(BaseModel):
    request_id:UUID|None=None
    expected_revision:int|None=Field(default=None,ge=1)
    action:str=Field(pattern="^(confirm|cancel)$")

@app.post("/v1/provisional/{provisional_id}/decision", dependencies=[Depends(require_session)])
@durable_mutation('capture_decision',('provisional_id',),'decision')
def decide_provisional(provisional_id:str, decision:ProvisionalAction, db=Depends(database)):
    with mongo_transaction(db) as session:
        return _atomic_decide_provisional(provisional_id, decision, db=session_database(db,session))

def _atomic_decide_provisional(provisional_id:str, decision:ProvisionalAction, db=Depends(database)):
    lock_movement_insert(db)
    item=db.provisional_transactions.find_one({"provisional_id":provisional_id})
    if not item: raise HTTPException(status_code=404,detail="Active provisional transaction not found")
    target="confirmed" if decision.action=="confirm" else "cancelled"
    if item.get("status")==target: return {"provisional_id":provisional_id,"status":target,"resolved_review_count":0,"duplicate":True}
    if item.get("status") not in {"provisional","enriched"}: raise HTTPException(409,"A captura já foi decidida. Recarregue.")
    if decision.expected_revision is None and item.get('revision') is not None:raise HTTPException(428,'Reabra a captura para obter a revisão atual.')
    if decision.expected_revision is not None and decision.expected_revision!=item.get("revision",1): raise HTTPException(409,"A captura mudou. Recarregue.")
    extra_set:dict[str,Any]={}
    if decision.action=="confirm":
        apply_historical_capture_defaults(db,extra_set,base_item=item)
        validate_confirmed_provisional({**item,**extra_set},db)
    if decision.action=='cancel':
        reasons=movement_blockers(db,'capture',provisional_id,item)
        if reasons:raise HTTPException(409,' '.join(reasons)+' Revise os vínculos antes de descartar.')
    now=datetime.now(timezone.utc);new_status="confirmed" if decision.action=="confirm" else "cancelled"
    db.provisional_transactions.update_one({"_id":item["_id"]},{"$set":{**extra_set,"revision":item.get("revision",1)+1,"status":new_status,"updated_at":now,**({"confirmed_at":now} if new_status=="confirmed" else {"cancelled_at":now})}})
    db.raw_events.update_one({"event_id":item.get("event_id")},{"$set":{"processing_status":new_status,"linked_transaction_id":item["_id"] if new_status=="confirmed" else None}})
    db.audit_log.insert_one({"event":"provisional_transaction_decision","provisional_id":provisional_id,"action":decision.action,"before":json_safe(item),"after":{"status":new_status},"created_at":now})
    resolved_review_count=resolve_pending_reviews_for_provisional(db, provisional_id, now, new_status)
    return {"provisional_id":provisional_id,"status":new_status,"resolved_review_count":resolved_review_count}

def review_sources(db,item):
    event_id=item.get('event_id')
    original={'event':db.raw_events.find_one({'event_id':event_id}) if event_id else None,
              'capture':db.provisional_transactions.find_one({'event_id':event_id}) if event_id else None}
    kind=item.get('candidate_type','transaction')
    identity=item.get('candidate_transaction_id') if kind=='transaction' else item.get('candidate_provisional_id')
    candidate=(db.transactions.find_one({'_id':identity}) if kind=='transaction' else db.provisional_transactions.find_one({'provisional_id':identity})) if identity else None
    return original,candidate


def review_view(db,item):
    original,candidate=review_sources(db,item)
    return {**json_safe(item),'review_token':source_token(item),'source_token':source_token(original),
            'candidate_token':source_token(candidate),'source':json_safe(original['capture']),'source_event':json_safe(original['event']),
            'candidate':json_safe(candidate),'has_source':bool(original['capture'] or original['event'])}

@app.get("/v1/reviews", dependencies=[Depends(require_session)])
def reviews(status_filter:str="pending",limit:int=Query(100,ge=1,le=500),cursor:str|None=None,db=Depends(database)):
    with mongo_snapshot(db) as (reading,consistency):
        rows,info=workflow_page(reading.review_queue,{"status":status_filter},"created_at",limit,cursor)
        return {"items":[review_view(reading,x) for x in rows],**info,'read_consistency':consistency}

class ReviewDecision(BaseModel):
    request_id:UUID|None=None
    review_token:str|None=None
    source_token:str|None=None
    candidate_token:str|None=None
    acknowledge_difference:bool=False
    action: str = Field(pattern="^(confirm_match|keep_separate|correct_category|discard)$")
    category: str | None = None

@app.post("/v1/reviews/{review_id}/decision", dependencies=[Depends(require_session)])
@durable_mutation('review_decision',('review_id',),'decision')
def decide_review(review_id:str, decision:ReviewDecision, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session)
        lock_movement_insert(writing)
        return _decide_review(review_id,decision,writing,None)


def _decide_review(review_id,decision,db,session):
    kwargs=session_args(session)
    item=db.review_queue.find_one({"review_id":review_id},**kwargs)
    if not item: raise HTTPException(status_code=404,detail="Review not found")
    if item.get("status")=="resolved":
        if item.get("decision")==decision.action and item.get("category")==decision.category: return {"review_id":review_id,"status":"resolved","action":decision.action,"duplicate":True}
        raise HTTPException(status_code=409,detail="A revisão já teve outra decisão.")
    if item.get("status")!="pending": raise HTTPException(status_code=409,detail="Revisão indisponível para decisão.")
    require_source_token(item,decision.review_token)
    original,current_candidate=review_sources(db,item)
    require_source_token(original,decision.source_token)
    require_source_token(current_candidate,decision.candidate_token)
    if not (original['capture'] or original['event']):raise HTTPException(409,'A fonte original não está disponível.')
    if original['capture'] and original['capture'].get('status') not in {'provisional','enriched'}:
        raise HTTPException(409,'A captura já foi decidida. Recarregue a revisão.')
    now=datetime.now(timezone.utc)
    if decision.action=='discard':
        if db.transaction_evidence.find_one({'event_id':item['event_id'],'status':{'$ne':'reversed'}}):
            raise HTTPException(409,'Esta fonte tem evidência vinculada. Desfaça o vínculo antes de descartar a revisão.')
        if original['capture']:
            reasons=movement_blockers(db,'capture',original['capture']['provisional_id'],original['capture'])
            if reasons:raise HTTPException(409,' '.join(reasons)+' Revise os vínculos antes de descartar.')
            db.provisional_transactions.update_one({'event_id':item['event_id']},{'$set':{'status':'cancelled','cancelled_at':now,'updated_at':now},'$inc':{'revision':1}},**kwargs)
            db.raw_events.update_one({'event_id':item['event_id']},{'$set':{'processing_status':'cancelled','linked_transaction_id':None,'cancelled_at':now}},**kwargs)
        else:
            db.raw_events.update_one({'event_id':item['event_id']},{'$set':{'processing_status':'cancelled','cancelled_at':now}},**kwargs)
    elif decision.action=="confirm_match":
        target_type=item.get("candidate_type","transaction")
        target_id=item.get("candidate_transaction_id") if target_type=="transaction" else item.get("candidate_provisional_id")
        if not target_id: raise HTTPException(status_code=422,detail="No matching candidate exists")
        target_collection=db.transactions if target_type=="transaction" else db.provisional_transactions
        target_query={"_id":target_id} if target_type=="transaction" else {"provisional_id":target_id,"status":{"$in":["provisional","enriched","confirmed"]}}
        candidate=target_collection.find_one(target_query,**kwargs)
        if not candidate:
            raise HTTPException(409,"O registro candidato não está mais disponível. Recarregue.")
        if target_type=="provisional": validate_confirmed_provisional(candidate,db)
        observed=original['capture']
        if observed and not decision.acknowledge_difference:
            mismatch=abs(decimal_amount(observed.get('valor')) or Decimal(0))!=abs(decimal_amount(candidate.get('valor')) or Decimal(0))
            mismatch=mismatch or (observed.get('data_evento') and candidate.get('data_evento') and local_date(observed['data_evento'])!=local_date(candidate['data_evento']))
            mismatch=mismatch or any((observed.get(key) or candidate.get(key)) and str(observed.get(key) or '')!=str(candidate.get(key) or '') for key in ('natureza','fluxo','account_id','account_profile_id'))
            if mismatch:raise HTTPException(409,'As fontes apresentam diferenças. Confira os dois registros e reconheça as diferenças antes de vincular.')
        target_collection.update_one(target_query,{"$set":{"updated_at":now}},**kwargs)
        evidence={"evidence_id":str(uuid4()),"event_id":item["event_id"],"target_type":target_type,"match_score":item["match_score"],"decision":"user_confirmed","acknowledged_difference":decision.acknowledge_difference,"review_token":decision.review_token,"source_token":decision.source_token,"candidate_token":decision.candidate_token,"created_at":now}
        evidence["transaction_id" if target_type=="transaction" else "provisional_id"]=target_id
        previous=db.transaction_evidence.find_one({"event_id":item["event_id"]},**kwargs)
        if previous:
            if previous.get("status")!="reversed":raise HTTPException(409,"A evidência já possui vínculo ativo.")
            evidence['revision']=previous.get('revision',1)+1
            db.audit_log.insert_one({"event":"evidence_relinked","before":json_safe(previous),"after":json_safe(evidence),"created_at":now},**kwargs)
            db.transaction_evidence.replace_one({"_id":previous["_id"]},evidence,**kwargs)
        else:db.transaction_evidence.insert_one(evidence,**kwargs)
        db.raw_events.update_one({"event_id":item["event_id"]},{"$set":{"processing_status":"matched","linked_transaction_id":target_id}},**kwargs)
        db.provisional_transactions.update_one({"event_id":item["event_id"]},{"$set":{"status":"matched","updated_at":now},"$inc":{"revision":1}},**kwargs)
        if target_type=="provisional":
            db.provisional_transactions.update_one({"provisional_id":target_id},{"$set":{"status":"confirmed","confirmed_at":now,"updated_at":now,"revision":candidate.get("revision",1)+1},"$inc":{"evidence_count":1}},**kwargs)
            db.raw_events.update_one({"event_id":candidate.get("event_id")},{"$set":{"processing_status":"confirmed","linked_transaction_id":candidate["_id"]}},**kwargs)
    elif decision.action=="keep_separate":
        provisional=db.provisional_transactions.find_one({"event_id":item["event_id"]},**kwargs)
        if not provisional: raise HTTPException(status_code=422,detail="Captura vinculada à revisão não encontrada.")
        validate_confirmed_provisional(provisional,db)
        db.provisional_transactions.update_one({"event_id":item["event_id"]},{"$set":{"status":"confirmed","confirmed_at":now,"updated_at":now,"revision":provisional.get("revision",1)+1}},**kwargs)
        db.raw_events.update_one({"event_id":item["event_id"]},{"$set":{"processing_status":"confirmed_separate","linked_transaction_id":provisional["_id"]}},**kwargs)
    elif decision.action=="correct_category":
        if not decision.category: raise HTTPException(status_code=422,detail="category is required")
        db.provisional_transactions.update_one({"event_id":item["event_id"]},{"$set":{"categoria_sugerida":decision.category,"status":"provisional","updated_at":now},"$inc":{"revision":1}},**kwargs)
    db.audit_log.insert_one({"event":"review_decision","review_id":review_id,"action":decision.action,"category":decision.category,"created_at":now},**kwargs)
    updated=db.review_queue.update_one({"_id":item["_id"],"status":"pending"},{"$set":{"status":"resolved","decision":decision.action,"category":decision.category,"resolved_at":now}},**kwargs)
    if updated is not None and updated.matched_count==0:
        raise HTTPException(status_code=409,detail="A revisão já foi decidida. Recarregue a lista.")
    if decision.action=='discard' and original['capture']:
        resolve_pending_reviews_for_provisional(db,original['capture']['provisional_id'],now,'cancelled')
    return {"review_id":review_id,"status":"resolved","action":decision.action}


MAX_DOCUMENT_BYTES=20*1024*1024
_document_locks = tuple(Lock() for _ in range(32))

def finish_receipt(db, event_id, result, extraction, validation):
    ack=persisted_event(db,event_id,result)
    existing=db.raw_events.find_one({"event_id":event_id}) or {}
    if validation and validation.get("review_required") and existing.get("processing_error")!="item_total_mismatch":
        with mongo_transaction(db) as session:
            writing=session_database(db,session)
            lock_movement_insert(writing)
            return _finish_legacy_receipt(writing,event_id,result,extraction,validation)
    return {"persisted":True,"extraction":extraction,"validation":validation,"processing":ack}


def _finish_legacy_receipt(db,event_id,result,extraction,validation):
        # Legacy receipts may predate atomic mismatch handling. A completed
        # capture/evidence never becomes pending merely because it was retried.
        ack=persisted_event(db,event_id,result)
        captured=db.provisional_transactions.find_one({'event_id':event_id})
        if not captured or captured.get('status') not in {'provisional','enriched'}:
            return {"persisted":True,"extraction":extraction,"validation":validation,"processing":ack}
        now=datetime.now(timezone.utc)
        # A replay must never reopen a review that a person already resolved.
        completed=db.review_queue.find_one({"event_id":event_id,"reason":"item_total_mismatch","status":"resolved"})
        if completed:
            return {"persisted":True,"extraction":extraction,"validation":validation,"processing":ack}
        review=db.review_queue.find_one({"event_id":event_id,"status":"pending"})
        review_id=str(uuid5(NAMESPACE_URL, f"receipt-review:{event_id}"))
        if not review:
            db.review_queue.insert_one({'_id':review_id,'review_id':review_id,'event_id':event_id,'status':'pending','reason':'item_total_mismatch',
                'expected_total':validation['total'],'items_total':validation['items_total'],'difference':validation['items_total']-validation['total'],
                'provisional_id':captured['provisional_id'],'created_at':now})
        else:
            db.review_queue.update_one({'_id':review['_id'],'status':'pending'},{'$set':{
                'additional_reasons':list(dict.fromkeys([*(review.get('additional_reasons') or []),'item_total_mismatch'])),
                'expected_total':validation['total'],'items_total':validation['items_total'],
                'difference':validation['items_total']-validation['total']}})
        db.raw_events.update_one({"event_id":event_id},{"$set":{"processing_status":"review_required","processing_error":"item_total_mismatch"}})
        result={**result,"review_reason":"item_total_mismatch"}
        ack=persisted_event(db,event_id,result)
        return {"persisted":True,"extraction":extraction,"validation":validation,"processing":ack}


@app.post("/v1/documents/extract", dependencies=[Depends(require_session)])
async def extract_document(file:UploadFile=File(...),source_provider:str=Form("user_upload"),db=Depends(database),request:Request=None,pdf_cpf:str|None=Form(None),event_id:UUID|None=Form(None),captured_at:datetime|None=Form(None)):
    return await run_upload(request, extract_document_sync, file, source_provider, db, request, pdf_cpf, event_id, captured_at)

def extract_document_sync(file, source_provider, db, request, pdf_cpf=None, event_id=None, captured_at=None):
    if request.app.state.service is None: raise HTTPException(503,"MongoDB is not configured")
    data=file.file.read(MAX_DOCUMENT_BYTES+1)
    if len(data)>MAX_DOCUMENT_BYTES: raise HTTPException(status_code=413,detail="Document exceeds 20 MiB")
    mime=file.content_type or "application/octet-stream"
    if mime not in {"image/jpeg","image/png","application/pdf","text/plain"}:
        raise HTTPException(status_code=415,detail="Supported formats: JPEG, PNG, PDF and plain text")
    voice_occurrence = source_provider == 'mobile_voice' and captured_at is not None
    occurrence_at = None
    if captured_at is not None:
        if not voice_occurrence or mime != 'text/plain':
            raise HTTPException(422,'O instante da declaração só se aplica à evidência de voz em texto.')
        if event_id is None:
            raise HTTPException(428,'Envie a identidade da declaração para permitir retentativa segura.')
        try:
            stamp = datetime.fromisoformat(captured_at.replace('Z','+00:00')) if isinstance(captured_at,str) else captured_at
            if not isinstance(stamp,datetime) or stamp.tzinfo is None: raise ValueError('missing offset')
            occurrence_at = stored_instant(stamp)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422,'Informe o instante da declaração com fuso horário.') from exc
    digest=hashlib.sha256(data).hexdigest()
    with _document_locks[int(digest[:8],16)%len(_document_locks)]:
        context={'source_provider':source_provider,'mime_type':mime}
        # Legacy queue payload {} retains its original hash/receipt/context.
        # Only a newly sealed voice command carries occurrence identity/time.
        if voice_occurrence:
            context.update({'voice_command_version':1,'captured_at':occurrence_at.isoformat()})
        previous=bind_upload(db,event_id,'document',digest,context)
        if previous is not None:return previous
        result=extract_document_data(data,mime,digest,file.filename,source_provider,db,request,pdf_cpf,
            capture_event_id=str(event_id) if voice_occurrence else None, capture_instant=occurrence_at)
        return complete_upload(db,event_id,'document',digest,result,context)

def extract_document_data(data,mime,digest,filename,source_provider,db,request,pdf_cpf=None,*,capture_event_id=None,capture_instant=None):
    event_id=capture_event_id or str(uuid5(NAMESPACE_URL,f"receipt:{source_provider}:{digest}"))
    stored=store_media(db,data,digest,Path(filename or "document.bin").suffix[:12])
    existing=db.raw_events.find_one({"event_id":event_id})
    if existing:
        if capture_event_id:
            try: same_instant = stored_instant(existing.get('captured_at')) == capture_instant
            except (TypeError,ValueError): same_instant = False
            if (existing.get('source_type') != 'receipt'
                or existing.get('source_provider') != source_provider
                or (existing.get('raw_payload') or {}).get('sha256') != digest
                or (existing.get('raw_payload') or {}).get('voice_command_version') != 1
                or not same_instant):
                raise HTTPException(409,'Esta identidade já pertence a outra declaração. Preserve o comando original.')
        db.raw_events.update_one({"event_id":event_id},{"$set":{"media_status":"media_ready","media_storage_key":str(stored)}})
        if existing.get('processing_status')=='pending':
            request.app.state.service.process({'event_id':event_id,**(existing.get('ingest_payload') or {key:existing.get(key) for key in ('source_type','source_provider','captured_at','raw_payload','parsed_payload','parser_version')})})
        ack=persisted_event(db,event_id,{"duplicate":True})
        raw=existing.get("raw_payload") or {}
        return finish_receipt(db,event_id,ack,raw.get("extraction"),raw.get("validation"))
    try:
        if mime in {"image/jpeg","image/png"}:
            extraction=XAIVisionProvider().extract_image(data,mime)
        else:
            if mime=="application/pdf":
                content=read_pdf(data,pdf_cpf)
                extraction=XAITextProvider().extract_text(content.text) if content.text is not None else XAIVisionProvider().extract_images(content.images)
            else:
                text=data.decode("utf-8",errors="replace")
                extraction=XAITextProvider().extract_text(text)
    except PDFPasswordError as exc: raise HTTPException(status_code=428,detail=str(exc)) from exc
    except XAIError as exc: raise HTTPException(status_code=503,detail=str(exc))
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=422,detail="Could not read document") from exc
    try: validation=validate_document_extraction(extraction)
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc))
    sum_items=validation["items_total"];total_decimal=validation["total"];discrepancy=validation["review_required"]
    captured=capture_instant or datetime.now(timezone.utc)
    extraction_payload=extraction.model_dump(mode="json")
    validation_payload={"items_total":float(sum_items),"total":float(total_decimal),"review_required":discrepancy}
    parsed={"amount":float(total_decimal),"currency":extraction.currency,"merchant_raw":extraction.merchant or "","event_timestamp":extraction.datetime.isoformat() if extraction.datetime else captured.isoformat(),"event_type":"receipt","direction":"expense","items":[x.model_dump(mode="json") for x in extraction.items]}
    event={"event_id":event_id,"source_type":"receipt","source_provider":source_provider,"captured_at":captured,"raw_payload":{"filename":filename,"mime_type":mime,"sha256":digest,"storage_key":str(stored),"extraction":extraction_payload,"validation":validation_payload},"parsed_payload":parsed,"parser_version":"xai-document-v1","ai_model":get_settings().xai_vision_model if mime.startswith("image/") else get_settings().xai_text_model,"media_status":"media_ready","media_storage_key":str(stored)}
    if capture_event_id: event['raw_payload']['voice_command_version']=1
    try: result=request.app.state.service.process(event)
    except EventConflict:
        existing=db.raw_events.find_one({"event_id":event_id})
        if not existing or existing.get("raw_payload",{}).get("sha256")!=digest: raise HTTPException(409,"Duplicate event conflict")
        raw=existing["raw_payload"]
        return finish_receipt(db,event_id,persisted_event(db,event_id,{"duplicate":True}),raw.get("extraction"),raw.get("validation"))
    return finish_receipt(db,event_id,result,extraction_payload,validation_payload)

@app.post("/v1/import", dependencies=[Depends(require_session)])
async def import_statement(file:UploadFile=File(...),source_provider:str=Form("statement_import"),request:Request=None,event_id:UUID|None=Form(None)):
    return await run_upload(request, import_statement_sync, file, source_provider, request, event_id)

def import_statement_sync(file, source_provider, request, event_id=None):
    if request.app.state.service is None: raise HTTPException(503,"MongoDB is not configured")
    data=file.file.read(10*1024*1024+1)
    if len(data)>10*1024*1024: raise HTTPException(status_code=413,detail="Import exceeds 10 MiB")
    ext=Path(file.filename or "").suffix.casefold()
    if ext not in {".csv",".xlsx"}: raise HTTPException(status_code=415,detail="This endpoint accepts CSV or XLSX statements")
    try: rows=ImportFinancialProvider().read_rows(file.filename or "statement.csv",data)
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc))
    if not rows:raise HTTPException(422,'Nenhuma movimentação foi encontrada. Preserve o arquivo e confira o cabeçalho.')
    if len(rows)>200:raise HTTPException(422,'Separe o extrato em arquivos com até 200 movimentações para confirmar cada importação de forma atômica.')
    db=request.app.state.db; service=request.app.state.service
    digest=hashlib.sha256(data).hexdigest(); results=[]; invalid=[]; events=[]
    operation_id=event_id
    context={'source_provider':source_provider,'format':ext}
    previous=bind_upload(db,operation_id,'statement-import',digest,context)
    if previous is not None:return previous
    stored=store_media(db,data,digest,ext)
    from app.statement_extraction import table_currency, table_value
    def getval(row,keys): return table_value(row,*keys)
    for index,row in enumerate(rows, start=2):
        raw_amount=getval(row,["valor","amount","valortransacao","montante","total"])
        merchant=getval(row,["estabelecimento","merchant","descricao","description","beneficiario"])
        raw_date=getval(row,["dataevento","data","date","datetime","datatransacao"])
        try:
            currency=table_currency(row)
            if currency!='BRL': raise ValueError('Moeda estrangeira ou não determinada. Use o lote de revisão para confirmar BRL quando não informado.')
            signed_amount=parse_brl(raw_amount)
            if not merchant or not signed_amount.is_finite() or signed_amount==0: raise ValueError()
            direction_raw=str(getval(row,["fluxo","direction","natureza","tipo"]) or "").casefold()
            method_raw=str(getval(row,["metodopagamento","paymentmethod","conta"]) or "").casefold()
            if signed_amount<0 or any(word in direction_raw for word in ("saida","despesa","compra","debito","expense","cartao")) or "cartao" in method_raw:
                direction="expense"
            elif any(word in direction_raw for word in ("entrada","receita","salario","credito","income")):
                direction="income"
            else: raise ValueError()
            amount=str(abs(signed_amount))
            stamp=parse_datetime(raw_date)
            bank_ref=getval(row,["identificadortransacao","idtransacao","transactionid","banktransactionref","bankref"])
            account_identity=getval(row,["accountidentity","accountid","accountref","contaid","contaref","conta","account","instituicao","banco"])
            if bank_ref is not None and len(str(bank_ref).strip())>160: raise ValueError('Referência bancária inválida.')
            if account_identity is not None and len(str(account_identity).strip())>240: raise ValueError('Identidade da conta inválida.')
            event_id=str(uuid5(NAMESPACE_URL,f"sha256:{digest}:row:{index}"))
            event={"event_id":event_id,"source_type":ext[1:],"source_provider":source_provider,"captured_at":stamp,"raw_payload":{"filename":file.filename,"file_sha256":digest,"storage_key":str(stored),"row_number":index,"row":{str(k):str(v) for k,v in row.items()}},"parsed_payload":{"amount":amount,"currency":currency,"merchant_raw":str(merchant),"event_timestamp":stamp.isoformat(),"event_type":"imported_transaction","direction":direction,"bank_transaction_ref":str(bank_ref).strip() if bank_ref is not None else None,"account_identity":str(account_identity).strip() if account_identity is not None else None},"parser_version":"statement-import-v2"}
        except (ValueError,TypeError,InvalidOperation,OverflowError):
            invalid.append(index)
            continue
        events.append(event)
    # A rejected file cannot leave an invisible partially imported prefix.
    # All rows are validated before the first financial workflow write.
    if invalid:raise HTTPException(422,{'message':'Confira as linhas inválidas antes de importar. O arquivo original foi preservado.','invalid_row_numbers':invalid})
    try:
        with mongo_transaction(db) as session:
            writing=session_database(db,session)
            processing=service.scoped(writing)
            lock_movement_insert(writing)
            # All rows and the aggregate ACK share the same transaction. A
            # failure on any row leaves only retained original bytes/identity.
            for event in events:
                event_id=event['event_id']
                existing=writing.raw_events.find_one({"event_id":event_id})
                if existing:
                    if existing.get('processing_status')=='pending':processing.process({'event_id':event_id,**(existing.get('ingest_payload') or {key:existing.get(key) for key in ('source_type','source_provider','captured_at','raw_payload','parsed_payload','parser_version')})})
                    results.append(persisted_event(writing,event_id,{"duplicate":True}))
                else:
                    try:result=processing.process(event)
                    except EventConflict:raise HTTPException(409,"Duplicate event conflict")
                    results.append(persisted_event(writing,event_id,result))
            return complete_upload(writing,operation_id,'statement-import',digest,{"persisted":True,"file_sha256":digest,"rows_read":len(rows),"accepted":len(results),"invalid_row_numbers":invalid,"results":results[:200]},context)
    except OperationFailure as exc:
        if exc.code==112 or exc.has_error_label('TransientTransactionError') or exc.has_error_label('UnknownTransactionCommitResult'):
            raise HTTPException(503,'A importação precisa ser confirmada novamente. Preserve o arquivo e reenvie a mesma tentativa.',headers={'Retry-After':'5'}) from exc
        raise

@app.get("/v1/accounts", dependencies=[Depends(require_session)])
def accounts(db=Depends(database)):
    projection={"instituicao":1,"tipo":1,"status":1,"moeda":1,"external_key":1,"cartao_final":1}
    legacy = [json_safe(x) for x in db.accounts.find({},projection).limit(500)]
    profiles = {row["_id"]: row for row in db.account_profiles.find({"_id": {"$regex": "^card-profile:"}})}
    for item in legacy:
        if item.get("tipo") == "cartao_credito":
            meta = profiles.get(f'card-profile:{item["id"]}', {})
            values = meta.get("values", {})
            item.update({"instituicao": values.get("apelido") or values.get("instituicao") or item.get("instituicao"),
                         "cartao_final": values.get("cartao_final", item.get("cartao_final", "")), "deleted": bool(meta.get("deleted"))})
    legacy = [item for item in legacy if not item.get("deleted")]
    return {"items": legacy}

@app.post("/v1/account-profiles", status_code=201, dependencies=[Depends(require_session)])
def add_account_profile(body:AccountProfileCreate, db=Depends(database)):
    with mongo_transaction(db) as session:
        return {"item":create_profile(session_database(db,session),body)}

@app.patch("/v1/account-profiles/{account_key}", dependencies=[Depends(require_session)])
@durable_mutation('profile_edit',('account_key',))
def edit_account_profile(account_key:str, body:AccountProfileUpdate, db=Depends(database)):
    with mongo_transaction(db) as session:
        return {"item":update_profile(session_database(db,session),account_key,body)}

@app.get("/v1/account-profiles/{account_key}/deletion", dependencies=[Depends(require_session)])
def account_profile_deletion(account_key:str, db=Depends(database)):
    with mongo_snapshot(db) as (reading,_):
        profile=validate_account_key(account_key,reading)
        usage=account_usage(reading,account_key)
    return {"can_delete":not bool(usage),"usage":usage,"revision":profile["revision"]}

@app.delete("/v1/account-profiles/{account_key}", dependencies=[Depends(require_session)])
@durable_mutation('profile_delete',('account_key',))
def remove_account_profile(account_key:str, body:AccountProfileDelete, db=Depends(database)):
    with mongo_transaction(db) as session:
        return delete_profile(session_database(db,session),account_key,body)

@app.get("/v1/account-positions", dependencies=[Depends(require_session)])
def list_account_positions(db=Depends(database)):
    with mongo_snapshot(db) as (reading,consistency):
        items=account_positions(reading)
    return {"items":items,"read_consistency":consistency,"read_at":datetime.now(timezone.utc).isoformat()}

class BalanceReferenceIn(BaseModel):
    amount:Decimal
    as_of_date:date|None=None
    expected_token:str|None=None
    request_id:UUID|None=None

@app.put("/v1/account-positions/{account_key}/balance-reference", dependencies=[Depends(require_session)])
def set_balance_reference(account_key:str, body:BalanceReferenceIn, db=Depends(database)):
    with mongo_transaction(db) as session:
        return _atomic_set_balance_reference(account_key, body, db=session_database(db,session))

def _atomic_set_balance_reference(account_key:str, body:BalanceReferenceIn, db=Depends(database)):
    lock_movement_insert(db)
    validate_account_key(account_key,db)
    if body.request_id is None: raise HTTPException(428,'Envie a identidade desta tentativa de salvar o saldo.')
    command_id='balance-reference:'+str(body.request_id)
    digest=source_token({'account_key':account_key,**body.model_dump(mode='json')})
    command=db.batch_commands.find_one({'_id':command_id})
    if command:
        if command['digest']!=digest: raise HTTPException(409,'Esta tentativa já foi usada com outros dados.')
        return command['result']
    previous=db.account_balance_snapshots.find_one({'account_key':account_key})
    require_source_token(previous,body.expected_token)
    if not body.amount.is_finite(): raise HTTPException(status_code=422,detail="Saldo deve ser um número finito.")
    amount=body.amount.quantize(Decimal("0.01"))
    if abs(amount)>Decimal("100000000000"):
        raise HTTPException(status_code=422,detail="Saldo fora do limite aceito.")
    now=datetime.now(timezone.utc)
    if body.as_of_date and body.as_of_date>now.astimezone(ZoneInfo("America/Sao_Paulo")).date():
        raise HTTPException(status_code=422,detail="A data do saldo não pode estar no futuro.")
    as_of=(datetime.combine(body.as_of_date+timedelta(days=1),datetime.min.time(),tzinfo=ZoneInfo("America/Sao_Paulo")).astimezone(timezone.utc) if body.as_of_date else now)
    db.account_balance_snapshots.update_one({"account_key":account_key},{"$set":{"amount":Decimal128(str(amount)),"as_of":as_of,"reference_kind":"civil_day" if body.as_of_date else "instant","as_of_date":body.as_of_date.isoformat() if body.as_of_date else None,"updated_at":now}},upsert=True)
    db.audit_log.insert_one({"event":"account_balance_reference_set","account_key":account_key,"before":json_safe(previous) if previous else None,"after":{"amount":str(amount),"as_of":as_of},"created_at":now})
    result={"item":next(item for item in account_positions(db) if item["key"]==account_key)}
    db.batch_commands.insert_one({'_id':command_id,'digest':digest,'status':'completed','result':result,'created_at':now})
    return result

@app.post("/v1/transfers/reconcile", dependencies=[Depends(require_session)])
def reconcile_transfers(db=Depends(database)):
    with mongo_transaction(db) as session:
        return _atomic_reconcile_transfers(db=session_database(db,session))

def _atomic_reconcile_transfers(db=Depends(database)):
    lock_movement_insert(db)
    updated=[]
    for capture in db.provisional_transactions.find({"status":"confirmed","transfer":{"$exists":True}}):
        transfer,changed=reconcile_capture_transfer(db,capture)
        if not changed: continue
        now=datetime.now(timezone.utc)
        db.provisional_transactions.update_one({"_id":capture["_id"]},{"$set":{"transfer":transfer,"updated_at":now,'revision':capture.get('revision',1)+1}})
        db.audit_log.insert_one({"event":"account_transfer_reconciled","provisional_id":capture["provisional_id"],"before":json_safe(capture.get("transfer")),"after":json_safe(transfer),"created_at":now})
        updated.append(capture["provisional_id"])
    return {"updated_count":len(updated),"provisional_ids":updated}

@app.get("/v1/cards", dependencies=[Depends(require_session)])
def cards(db=Depends(database)):
    return {"items":[json_safe(x) for x in db.credit_card_bills.find({}, {"external_key":1,"competencia":1,"data_vencimento":1,"valor_total":1,"saldo_aberto":1,"valor_pago":1,"status":1}).sort("data_vencimento",-1).limit(200)]}

@app.get("/v1/obligations", dependencies=[Depends(require_session)])
def obligations(limit:int=Query(200,ge=1,le=500),offset:int=Query(0,ge=0),status_filter:str|None=None,date_from:date|None=None,date_to:date|None=None,db=Depends(database)):
    with mongo_snapshot(db) as (reading, consistency):
        query={}
        if status_filter:query['status']=status_filter
        if date_from or date_to:
            query['data_vencimento']={}
            if date_from:query['data_vencimento']['$gte']=day_start(date_from)
            if date_to:query['data_vencimento']['$lt']=day_start(date_to+timedelta(days=1))
        rows=list(reading.obligations.find(query).sort([('data_vencimento',1),('_id',1)]).skip(offset).limit(limit))
        total=reading.obligations.count_documents(query);following=offset+len(rows)
        return {"items":[json_safe(x) for x in effective_documents(reading, 'obligation', rows)], 'read_consistency': consistency,
                'total':total,'offset':offset,'limit':limit,'has_more':following<total,'next_offset':following if following<total else None}

@app.get("/v1/installments", dependencies=[Depends(require_session)])
def installment_plans(limit:int=Query(200,ge=1,le=500),offset:int=Query(0,ge=0),db=Depends(database)):
    with mongo_snapshot(db) as (reading, consistency):
        rows=list(reading.installment_plans.find().sort([('data_compra',-1),('_id',-1)]).skip(offset).limit(limit))
        total=reading.installment_plans.count_documents({});following=offset+len(rows)
        return {"items":[json_safe(x) for x in attach_installment_cards(reading, effective_documents(reading, 'installment', rows))], 'read_consistency': consistency,
                'total':total,'offset':offset,'limit':limit,'has_more':following<total,'next_offset':following if following<total else None}


def commitment_output(item, db=None):
    if db is not None: item=effective_commitments(db, [item])[0]
    result=json_safe(item)
    if db is not None:
        identities = [link.get("transaction_id") for link in item.get("linked_transactions") or [] if link.get("transaction_id")]
        snapshot = linked_statement_snapshots(db, {item.get("commitment_id") or result.get("commitment_id") or "commitment": identities}).get(item.get("commitment_id") or result.get("commitment_id") or "commitment")
        if snapshot:
            result["linked_purchase"] = snapshot
            raw = result.get("original_description") or result.get("name") or ""
            if snapshot.get("vendedor") and result.get("display_name") in {None, "", raw} and snapshot["vendedor"] != raw:
                result["display_name"] = snapshot["vendedor"]
            if snapshot.get("categoria") and not result.get("categoria"):
                result["categoria"] = snapshot["categoria"]
            if snapshot.get("subcategoria") and not result.get("subcategoria"):
                result["subcategoria"] = snapshot["subcategoria"]
        if item.get("card_id") and not result.get("card_label"):
            result["card_label"] = card_labels(db, [item.get("card_id")]).get(str(item.get("card_id")))
    result["schedule"]=schedule(item)
    result["amount"]=float(item["amount"])
    for field in ("purchase_date","next_due_date","end_date","cash_due_date"):
        result[field]=as_date(item[field]).isoformat() if item.get(field) else None
    pending=future_pending_total(item)
    result["future_pending_total"]=float(pending) if pending is not None else None
    result["future_pending_requires_review"]=item.get("status")=="cancelado" and bool(item.get("remaining_charges"))
    result["payment_occurrence_requires_review"]=bool(historical_occurrence_conflicts(item))
    result["payment_occurrence_review_reasons"]=["Há pagamento marcado como histórico para a próxima cobrança ou uma posterior. Confira os vencimentos e a agenda antes de projetar ou vincular novamente."] if result["payment_occurrence_requires_review"] else []
    return result


@app.get("/v1/commitments", dependencies=[Depends(require_session)])
def list_commitments(limit:int=Query(default=1000,ge=1,le=1000), offset:int=Query(default=0,ge=0), db=Depends(database)):
    with mongo_snapshot(db) as (reading, consistency):
        query = {"future_deleted_at": None}
        total = reading.commitments.count_documents(query)
        rows = list(reading.commitments.find(query).sort([("next_due_date",1),("_id",1)]).skip(offset).limit(limit))
        next_offset = offset + len(rows)
        return {"items":[commitment_output(x, reading) for x in rows], "total":total, "limit":limit, "offset":offset,
                "has_more":next_offset < total, "next_offset":next_offset if next_offset < total else None,
                "commitment_cutoff_date": commitment_cutoff().isoformat(),
                "read_consistency":consistency}


def validate_commitment_refs(body:CommitmentIn,db,existing=None):
    if body.card_id:
        if not ObjectId.is_valid(body.card_id) or not db.accounts.find_one({"_id":ObjectId(body.card_id),"tipo":"cartao_credito"}):
            raise HTTPException(status_code=422,detail="Cartão não encontrado.")
        if card_metadata(db, "card", body.card_id).get("deleted") and body.card_id != (existing or {}).get('card_id'):
            raise HTTPException(422, "Restaure o cartão antes de vincular um compromisso.")
    if body.account_id:
        selectable = body.account_id != str((existing or {}).get('account_id') or '')
        if body.account_id.startswith('profile:'):
            validate_account_key(body.account_id.removeprefix('profile:'),db,selectable=selectable)
        elif not ObjectId.is_valid(body.account_id) or not db.accounts.find_one({"_id":ObjectId(body.account_id)}):
            raise HTTPException(status_code=422,detail="Conta não encontrada.")
        else:
            identity=db.accounts.find_one({'_id':ObjectId(body.account_id)})
            key=account_key_for_legacy(identity)
            if key: validate_account_key(key,db,selectable=selectable)


@app.post("/v1/commitments",status_code=201,dependencies=[Depends(require_session)])
def create_commitment(body:CommitmentIn,db=Depends(database)):
    if body.amount_mode == 'last_three_bills':
        raise HTTPException(422, 'A média automática exige uma regra de contas previamente conferida.')
    if body.creation_id is None:
        raise HTTPException(428,"Envie creation_id para permitir o reenvio seguro do cadastro.")
    command_id="commitment-create:"+str(body.creation_id)
    payload=body.model_dump(mode="json",exclude={"creation_id","revision","request_id"})
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    identity=str(uuid5(NAMESPACE_URL,command_id))
    def replay():
        command=db.batch_commands.find_one({"_id":command_id})
        if command:
            if command.get("digest")!=digest: raise HTTPException(409,"Esta tentativa de cadastro já foi usada com outros dados.")
            return command["result"]
        return None
    try:
        with mongo_transaction(db) as session:
            scoped=session_database(db,session)
            command=scoped.batch_commands.find_one({"_id":command_id})
            if command:
                if command.get("digest")!=digest: raise HTTPException(409,"Esta tentativa de cadastro já foi usada com outros dados.")
                return command["result"]
            lock_movement_insert(scoped)
            validate_commitment_refs(body,scoped)
            item=stored_document(body)
            item["commitment_id"]=identity
            ensure_parcelamento_purchase(scoped, item, materialize=True)
            result={**commitment_output(item),'persisted':True,'creation_id':str(body.creation_id)}
            scoped.batch_commands.insert_one({"_id":command_id,"kind":"commitment_create","digest":digest,"status":"completed","result":result,"created_at":item["created_at"]})
            scoped.commitments.insert_one(item)
            scoped.audit_log.insert_one({"event":"commitment_created","commitment_id":identity,"created_at":item["created_at"]})
        return result
    except (DuplicateKeyError,OperationFailure) as exc:
        if isinstance(exc,OperationFailure) and not (exc.code in {11000,112} or exc.has_error_label("TransientTransactionError")): raise
        result=replay()
        if result is not None: return result
        raise HTTPException(409,"O cadastro está sendo salvo. Reenvie a mesma tentativa.",headers={'X-Operation-Pending':'1'}) from exc


def commitment_revision_filter(item):
    return item["revision"] if item.get("revision") is not None else {"$exists":False}


@app.put("/v1/commitments/{commitment_id}",dependencies=[Depends(require_session)])
@durable_mutation('commitment_edit',('commitment_id',))
def update_commitment(commitment_id:str,body:CommitmentIn,db=Depends(database)):
    with mongo_transaction(db) as session:
        writing=session_database(db,session)
        lock_movement_insert(writing)
        return _atomic_update_commitment(commitment_id,body,writing)


def _atomic_update_commitment(commitment_id,body,db):
    existing=db.commitments.find_one({"commitment_id":commitment_id})
    if not existing: raise HTTPException(status_code=404,detail="Compromisso não encontrado.")
    if existing.get('future_deleted_at'): raise HTTPException(409, 'As cobranças futuras deste cadastro foram excluídas. O histórico continua preservado.')
    if body.revision is None:
        raise HTTPException(status_code=428,detail="Reabra o compromisso para obter sua revisão atual.")
    if body.revision!=(existing.get("revision") or 1):
        raise HTTPException(status_code=409,detail="O compromisso mudou. Recarregue antes de salvar.")
    validate_commitment_refs(body,db,existing)
    effective_mode = body.amount_mode or existing.get('amount_mode')
    rule = db.commitment_amount_rules.find_one({'_id':commitment_id})
    if effective_mode is None and rule:
        effective_mode = 'last_three_bills'
    if effective_mode == 'last_three_bills':
        if (not rule or rule.get('account_id') != body.account_id
            or rule.get('method') != 'last_three_bills' or rule.get('provider') not in PATTERNS
            or not ObjectId.is_valid(body.account_id or '') or body.card_id
            or body.kind != 'assinatura' or body.frequency != 'mensal'):
            raise HTTPException(422, 'A média automática não está configurada para esta conta. Use valor fixo ou mantenha a conta original.')
    item=stored_document(body,existing)
    if effective_mode == 'last_three_bills':
        item['amount_mode'] = effective_mode
        item['amount'] = existing['amount']  # Keep the reference, not a transient reading estimate.
    result=db.commitments.replace_one({"_id":existing["_id"],"revision":commitment_revision_filter(existing)},{"_id":existing["_id"],**item})
    if result is not None and result.matched_count==0:
        raise HTTPException(status_code=409,detail="O compromisso mudou. Recarregue antes de salvar.")
    sync_parcelamento_purchase(db, existing, item)
    db.audit_log.insert_one({"event":"commitment_updated","commitment_id":commitment_id,"revision":item["revision"],"created_at":item["updated_at"]})
    return commitment_output(item, db)


class CommitmentPaymentLink(BaseModel):
    request_id:UUID|None=None
    expected_revision:int|None=Field(default=None,ge=1)
    expected_source_token:str|None=None
    transaction_id:str|None=None
    provisional_id:str|None=None
    historical_only:bool=False
    occurrence_due_date:date|None=None

    @model_validator(mode="after")
    def exactly_one_source(self):
        if bool(self.transaction_id)==bool(self.provisional_id):
            raise ValueError("Informe uma movimentação ou uma captura confirmada.")
        return self


class CommitmentPaymentUnlink(BaseModel):
    request_id:UUID|None=None
    expected_revision:int|None=Field(default=None,ge=1)
    transaction_id:str|None=None
    provisional_id:str|None=None

    @model_validator(mode="after")
    def exactly_one_source(self):
        if bool(self.transaction_id)==bool(self.provisional_id):
            raise ValueError("Informe a movimentação ou a captura do vínculo.")
        return self


class CommitmentPaymentSwap(BaseModel):
    request_id:UUID|None=None
    expected_revision:int|None=Field(default=None,ge=1)
    expected_source_token:str|None=None
    transaction_id:str|None=None
    provisional_id:str|None=None
    replacement_transaction_id:str|None=None
    replacement_provisional_id:str|None=None

    @model_validator(mode="after")
    def distinct_sources(self):
        if bool(self.transaction_id)==bool(self.provisional_id):
            raise ValueError("Informe o lançamento atualmente vinculado.")
        if bool(self.replacement_transaction_id)==bool(self.replacement_provisional_id):
            raise ValueError("Informe o lançamento que substitui o vínculo.")
        if (self.transaction_id or self.provisional_id)==(self.replacement_transaction_id or self.replacement_provisional_id):
            raise ValueError("Escolha um lançamento diferente do vínculo atual.")
        return self


@app.get("/v1/commitments/{commitment_id}/payment-candidates",dependencies=[Depends(require_session)])
def commitment_payment_candidates(commitment_id:str,db=Depends(database)):
    item=db.commitments.find_one({"commitment_id":commitment_id})
    if not item: raise HTTPException(status_code=404,detail="Compromisso não encontrado.")
    item=effective_commitments(db, [item])[0]
    expected=Decimal(str(item["amount"]))
    if item.get("kind")=="obrigacao" and item.get("installment_count"):
        first_occurrence=schedule(item, 1)
        expected=Decimal(str(first_occurrence[0]["amount"])) if first_occurrence else Decimal(str(item["amount"]))
    due=as_date(item["next_due_date"])
    earliest=as_date(item["purchase_date"])-timedelta(days=10)
    lower=datetime(earliest.year,earliest.month,earliest.day,tzinfo=timezone.utc)
    candidates=[]
    name_key=canonical_key(item["name"])
    platform_words=[word for word in canonical_key(item.get("billing_platform") or "").split() if len(word)>=5]
    merchant_ids={merchant["_id"] for merchant in db.merchants.find({"nome_padrao":{"$regex":re.escape(item["name"]),"$options":"i"}},{"_id":1}).limit(50)}
    tolerance=max(Decimal("5"),expected*Decimal("0.25"))
    amount_filter={"$gte":Decimal128(str(-(expected+tolerance))),"$lte":Decimal128(str(-max(Decimal("0.01"),expected-tolerance)))}
    rolling=bool(item.get("amount_estimate"))
    provider_pattern=PATTERNS.get(item.get('amount_estimate',{}).get('provider')) if rolling else None
    account = str(item.get('account_id') or '')
    profile_key = estimate_account(db, account) if rolling else None
    if rolling and (not ObjectId.is_valid(account) or not item['amount_estimate']['rule_valid']):
        raise HTTPException(409, 'Confira a regra e a conta da previsão antes de vincular pagamentos.')
    source_scope={"account_id":{"$in":[ObjectId(account),account]}} if rolling else {"valor":amount_filter}
    capture_scope=source_scope
    if rolling and profile_key:
        capture_scope={'$or':[source_scope, {'account_profile_id':profile_key}]}
    source_limit=1000
    transaction_docs=list(db.transactions.find({"data_evento":{"$gte":lower},"fluxo":"saida","classification.migration_reason":{"$in":list(EXPENSE_REASONS)},**source_scope}).sort([("data_evento",-1),("_id",-1)]).limit(source_limit+1))
    capture_docs=list(db.provisional_transactions.find({"data_evento":{"$gte":lower},"fluxo":"saida","status":"confirmed",**capture_scope}).sort([("data_evento",-1),("_id",-1)]).limit(source_limit+1))
    truncated=len(transaction_docs)>source_limit or len(capture_docs)>source_limit
    transaction_docs=transaction_docs[:source_limit]
    capture_docs=capture_docs[:source_limit]
    source_ids=[str(doc["_id"]) for doc in transaction_docs]
    capture_ids=[doc.get("provisional_id") for doc in capture_docs if doc.get("provisional_id")]
    linked={key for commitment in db.commitments.find({"$or":[{"linked_transactions.transaction_id":{"$in":source_ids}},{"linked_transactions.provisional_id":{"$in":capture_ids}}]},{"linked_transactions":1}) for link in commitment.get("linked_transactions",[]) for key in (link.get("transaction_id"),link.get("provisional_id")) if key}
    sources=(("transaction",transaction_docs),("confirmed_capture",capture_docs))
    for kind,documents in sources:
        for document in documents:
            source_id=str(document["_id"]) if kind=="transaction" else document.get("provisional_id")
            if not source_id or source_id in linked or (kind=="confirmed_capture" and document.get("linked_transaction_id")): continue
            if not eligible_payment(document,kind=="confirmed_capture"): continue
            if rolling and (not same_estimate_account(document,account,profile_key,kind=='confirmed_capture')
                            or str(document.get('moeda') or document.get('currency') or 'BRL').upper() != 'BRL'): continue
            amount=decimal_amount(document.get("valor"))
            if amount is None or amount>=0 or (not rolling and abs(abs(amount)-expected)>max(Decimal("5"),expected*Decimal("0.25"))): continue
            raw=canonical_key(" ".join(str(document.get(key) or "") for key in ("descricao_original","estabelecimento_original","merchant_raw","merchant_name")))
            paid_day=local_date(document["data_evento"])
            related=bool((name_key and name_key in raw) or any(word in raw.split() for word in platform_words) or document.get("merchant_id") in merchant_ids)
            if provider_pattern and re.search(provider_pattern, raw, re.I): related=True
            same_card=bool(item.get("card_id") and str(document.get("account_id"))==item["card_id"])
            if not automatic_suggestion(paid_day, due, related=related, amount=amount, expected=expected, same_card=same_card): continue
            candidate=with_merchant_name(document,db)
            candidate["source_kind"]=kind
            candidate["match_reason"]="nome ou plataforma" if related else "cartão, data e valor"
            occurrences=payment_occurrences(item,local_date(document["data_evento"]))
            candidate["occurrence_options"]=occurrences
            candidate["requires_occurrence_selection"]=len(occurrences)>1
            next_occurrence=schedule(item,1)
            candidate["next_occurrence_due_date"]=next_occurrence[0]["due_date"] if next_occurrence else None
            candidates.append(candidate)
    def suggestion_rank(value):
        paid_day=local_date(value["data_evento"])
        gap=abs(abs(Decimal(str(value["valor"]))) - expected)
        named=0 if value.get("match_reason")=="nome ou plataforma" else 1
        return (named, gap, abs((paid_day-due).days))
    candidates.sort(key=suggestion_rank)
    return {"items":candidates[:25],"commitment_revision":item.get('revision',1),"truncated":truncated or len(candidates)>25,"scanned":len(transaction_docs)+len(capture_docs)}


_STATEMENT_TEXT_FIELDS = ("descricao_original", "estabelecimento_original", "merchant_raw", "merchant_name", "merchant_normalized")


def _statement_abs_amount(document):
    value = decimal_amount(document.get("valor"))
    return abs(value) if value is not None else None


def _statement_money_query(text):
    """A typed amount is a value, not a merchant name. Grouped thousands stay Brazilian."""
    raw = (text or "").strip().replace("R$", "").replace(" ", "")
    if not raw or not re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?|\d+\.\d{1,2}", raw):
        return None
    grouped = bool(re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", raw))
    normalized = raw.replace(".", "").replace(",", ".") if ("," in raw or grouped) else raw
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return None
    return number if number > 0 else None


def _statement_text(document):
    return canonical_key(" ".join(str(document.get(key) or "") for key in _STATEMENT_TEXT_FIELDS))


def _statement_matches(document, text, money, exact, low, high, start, end):
    if not document.get("data_evento"):
        return False
    try:
        day = local_date(document["data_evento"])
    except (TypeError, ValueError):
        return False
    if (start and day < start) or (end and day > end):
        return False
    absolute = _statement_abs_amount(document)
    if exact is not None and (absolute is None or abs(absolute - exact) > Decimal("0.01")):
        return False
    if exact is None and (low is not None or high is not None):
        if absolute is None or (low is not None and absolute < low) or (high is not None and absolute > high):
            return False
    if not text:
        return True
    blob = _statement_text(document)
    named = canonical_key(text) in blob
    # A number typed alone matches that amount anywhere in the selected period,
    # and also a description that contains the same digits.
    if money is not None and exact is None and low is None and high is None:
        amount_hit = absolute is not None and abs(absolute - money) <= Decimal("0.01")
        return named or amount_hit
    return named


def _decorate_statement_row(item, document, kind, db, linked, *, with_occurrences=True):
    source_id = str(document["_id"]) if kind == "transaction" else document.get("provisional_id")
    candidate = with_merchant_name(document, db)
    candidate["source_kind"] = kind
    commitment = linked.get(source_id)
    if commitment:
        candidate["commitment"] = commitment
        candidate["linkable"] = False
        candidate["block_reason"] = f"Já vinculado a {commitment['name']}."
        return candidate
    if not eligible_payment(document, kind == "confirmed_capture"):
        candidate["linkable"] = False
        candidate["block_reason"] = "Esta movimentação não é uma despesa realizada. Transferências, receitas e pagamentos de fatura não liquidam um compromisso."
        return candidate
    candidate["linkable"] = True
    candidate["block_reason"] = None
    if not with_occurrences:
        return candidate
    paid = local_date(document["data_evento"])
    occurrences = payment_occurrences(item, paid)
    candidate["occurrence_options"] = occurrences
    candidate["requires_occurrence_selection"] = len(occurrences) > 1
    next_occurrence = schedule(item, 1)
    candidate["next_occurrence_due_date"] = next_occurrence[0]["due_date"] if next_occurrence else None
    return candidate


def _statement_relevance(document, name_key, due, expected):
    named = 0 if name_key and name_key in _statement_text(document) else 1
    absolute = _statement_abs_amount(document)
    gap = abs(absolute - expected) if absolute is not None and expected is not None else Decimal("999999")
    distance = abs((local_date(document["data_evento"]) - due).days) if due else 0
    return (named, gap, distance)


def _statement_page(item, q, date_from, date_to, amount, amount_min, amount_max, limit, offset, db, *, rank=None, due=None, expected=None, with_occurrences=True):
    """Read the whole statement. Candidates stay a separate, narrower suggestion."""
    text = (q or "").strip()
    money = _statement_money_query(text)
    if amount is not None and amount <= 0:
        raise HTTPException(422, "Informe um valor positivo.")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(422, "O valor mínimo deve ser menor ou igual ao máximo.")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "A data inicial deve ser anterior à data final.")
    if not text and amount is None and amount_min is None and amount_max is None and not date_from and not date_to:
        raise HTTPException(422, "Informe um nome, um valor ou um período para consultar o extrato.")
    window = period_query(date_from, date_to)
    transactions = list(db.transactions.find(dict(window)))
    captures = [doc for doc in db.provisional_transactions.find({"status": "confirmed", **window}) if not doc.get("linked_transaction_id")]
    matched = []
    for kind, documents in (("transaction", transactions), ("confirmed_capture", captures)):
        for document in documents:
            if _statement_matches(document, text, money, amount, None if amount is not None else amount_min, None if amount is not None else amount_max, date_from, date_to):
                matched.append((kind, document))
    def instant(document):
        value = document.get("data_evento")
        return normalized_datetime(value).isoformat() if isinstance(value, datetime) else str(value or "")
    if rank == "relevance" and due is not None and expected is not None:
        name_key = canonical_key(item.get("name") or "")
        matched.sort(key=lambda pair: _statement_relevance(pair[1], name_key, due, expected))
    else:
        matched.sort(key=lambda pair: (instant(pair[1]), 0 if pair[0] == "transaction" else 1, str(pair[1].get("_id"))), reverse=True)
    page = matched[offset:offset + limit]
    transaction_ids = [str(doc["_id"]) for kind, doc in page if kind == "transaction"]
    capture_ids = [doc.get("provisional_id") for kind, doc in page if kind == "confirmed_capture" and doc.get("provisional_id")]
    linked = {}
    clauses = []
    if transaction_ids:
        clauses.append({"linked_transactions.transaction_id": {"$in": transaction_ids}})
    if capture_ids:
        clauses.append({"linked_transactions.provisional_id": {"$in": capture_ids}})
    if clauses:
        query = clauses[0] if len(clauses) == 1 else {"$or": clauses}
        for commitment in db.commitments.find(query, {"commitment_id": 1, "name": 1, "kind": 1, "linked_transactions": 1}):
            for link in commitment.get("linked_transactions") or []:
                key = link.get("transaction_id") or link.get("provisional_id")
                if key in transaction_ids or key in capture_ids:
                    linked[key] = {"id": commitment.get("commitment_id"), "name": commitment.get("name") or "Compromisso", "kind": commitment.get("kind")}
    for key, owner in overlay_link_names(db, transaction_ids, capture_ids).items():
        linked.setdefault(key, owner)
    return {
        "items": [_decorate_statement_row(item, document, kind, db, linked, with_occurrences=with_occurrences) for kind, document in page],
        "total": len(matched),
        "limit": limit,
        "offset": offset,
        "truncated": offset + len(page) < len(matched),
        "commitment_revision": item.get("revision", 1),
    }


def commitment_statement_page(commitment_id, q, date_from, date_to, amount, amount_min, amount_max, limit, offset, db, *, rank=None, due=None, expected=None):
    stored = db.commitments.find_one({"commitment_id": commitment_id})
    if not stored:
        raise HTTPException(status_code=404, detail="Compromisso não encontrado.")
    item = effective_commitments(db, [stored])[0]
    return _statement_page(item, q, date_from, date_to, amount, amount_min, amount_max, limit, offset, db, rank=rank, due=due, expected=expected)


def statement_lookup_page(name, due, expected, q, date_from, date_to, amount, amount_min, amount_max, limit, offset, db, *, rank=None):
    """Probable statement rows for a charge that is not a managed commitment. Nothing is written."""
    item = {"name": name or "", "revision": 1}
    return _statement_page(item, q, date_from, date_to, amount, amount_min, amount_max, limit, offset, db, rank=rank, due=due, expected=expected, with_occurrences=False)


@app.get("/v1/statement-search", dependencies=[Depends(require_session)])
def statement_search(
    name: str | None = Query(default=None, max_length=160),
    due: date | None = None,
    expected: Decimal | None = Query(default=None, gt=0, le=100_000_000),
    q: str | None = Query(default=None, max_length=120),
    date_from: date | None = None,
    date_to: date | None = None,
    amount: Decimal | None = Query(default=None, gt=0, le=100_000_000),
    amount_min: Decimal | None = Query(default=None, ge=0, le=100_000_000),
    amount_max: Decimal | None = Query(default=None, ge=0, le=100_000_000),
    limit: int = Query(40, ge=1, le=80),
    offset: int = Query(0, ge=0, le=5000),
    rank: str | None = Query(default=None, pattern="^relevance$"),
    db=Depends(database),
):
    return statement_lookup_page(name, due, expected, q, date_from, date_to, amount, amount_min, amount_max, limit, offset, db, rank=rank)


@app.get("/v1/commitments/{commitment_id}/statement-search", dependencies=[Depends(require_session)])
def commitment_statement_search(
    commitment_id: str,
    q: str | None = Query(default=None, max_length=120),
    date_from: date | None = None,
    date_to: date | None = None,
    amount: Decimal | None = Query(default=None, gt=0, le=100_000_000),
    amount_min: Decimal | None = Query(default=None, ge=0, le=100_000_000),
    amount_max: Decimal | None = Query(default=None, ge=0, le=100_000_000),
    limit: int = Query(40, ge=1, le=80),
    offset: int = Query(0, ge=0, le=5000),
    rank: str | None = Query(default=None, pattern="^relevance$"),
    due: date | None = None,
    expected: Decimal | None = Query(default=None, gt=0, le=100_000_000),
    db=Depends(database),
):
    return commitment_statement_page(commitment_id, q, date_from, date_to, amount, amount_min, amount_max, limit, offset, db, rank=rank, due=due, expected=expected)


@app.post("/v1/commitments/{commitment_id}/payment",dependencies=[Depends(require_session)])
@durable_mutation('commitment_payment',('commitment_id',))
def link_commitment_payment(commitment_id:str,body:CommitmentPaymentLink,db=Depends(database)):
    try:
        with mongo_transaction(db) as session:
            return _atomic_link_commitment_payment(commitment_id,body,session_database(db,session))
    except OperationFailure as exc:
        if exc.code==112 or exc.has_error_label("TransientTransactionError"):
            raise HTTPException(409,"A movimentação ou o compromisso mudou. Recarregue antes de vincular.") from exc
        raise

def _atomic_link_commitment_payment(commitment_id:str,body:CommitmentPaymentLink,db):
    lock_movement_insert(db)
    item=db.commitments.find_one({"commitment_id":commitment_id})
    if not item: raise HTTPException(status_code=404,detail="Compromisso não encontrado.")
    item=effective_commitments(db, [item])[0]
    if historical_occurrence_conflicts(item):
        raise HTTPException(409,"Há vínculo histórico contraditório com a agenda atual. Confira o histórico e a próxima cobrança antes de vincular.")
    if body.historical_only and body.occurrence_due_date and body.occurrence_due_date>=as_date(item["next_due_date"]):
        raise HTTPException(422,"Um pagamento histórico deve indicar vencimento anterior à próxima cobrança. Para liquidar a próxima ocorrência, use o vínculo normal.")
    if body.transaction_id:
        if not ObjectId.is_valid(body.transaction_id): raise HTTPException(status_code=422,detail="Movimentação inválida.")
        tx=db.transactions.find_one({"_id":ObjectId(body.transaction_id)})
        link_key="transaction_id";source_id=body.transaction_id;source_kind="transaction"
    else:
        tx=db.provisional_transactions.find_one({"provisional_id":body.provisional_id,"status":"confirmed"})
        link_key="provisional_id";source_id=body.provisional_id;source_kind="confirmed_capture"
        if tx and tx.get("linked_transaction_id"):
            raise HTTPException(status_code=409,detail="Esta captura já corresponde a uma movimentação importada.")
    if not tx: raise HTTPException(status_code=404,detail="Movimentação não encontrada.")
    if body.expected_revision is None or body.expected_source_token is None:
        raise HTTPException(428,'Confira o compromisso e a movimentação antes de vincular.')
    else:
        if body.expected_revision!=item.get('revision',1):raise HTTPException(409,'O compromisso mudou. Confira a agenda antes de vincular.')
        require_source_token(tx,body.expected_source_token,'A movimentação mudou. Confira os dados antes de vincular.')
    if db.commitments.find_one({f"linked_transactions.{link_key}":source_id}) or overlay_link_names(db,[source_id] if link_key=="transaction_id" else [],[source_id] if link_key=="provisional_id" else []):
        raise HTTPException(status_code=409,detail="Esta movimentação já foi vinculada a um compromisso.")
    amount=decimal_amount(tx.get("valor"))
    reason=(tx.get("classification") or {}).get("migration_reason") if body.transaction_id else "despesa_real"
    if not eligible_payment(tx,bool(body.provisional_id)) or not tx.get("data_evento"):
        raise HTTPException(status_code=422,detail="Selecione uma despesa realizada, não um pagamento de fatura ou transferência.")
    now=datetime.now(timezone.utc)
    paid_date=local_date(tx["data_evento"])
    occurrence=schedule(item,1,include_base=True)
    options=payment_occurrences(item,paid_date)
    if not body.historical_only and len(options)>1 and body.occurrence_due_date is None:
        raise HTTPException(422,"O pagamento pode corresponder a mais de uma ocorrência. Escolha explicitamente o vencimento liquidado.")
    selected=body.occurrence_due_date.isoformat() if body.occurrence_due_date else (occurrence[0]["due_date"] if occurrence and len(options)==1 and options[0]["due_date"]==occurrence[0]["due_date"] and not body.historical_only else None)
    if selected and not body.historical_only and (not occurrence or selected!=occurrence[0]["due_date"] or selected not in {row["due_date"] for row in options}):
        raise HTTPException(422,"Confira a agenda antes de liquidar uma ocorrência posterior ou fora da janela da próxima cobrança.")
    if selected and occurrence_already_linked(item,selected,paid_date):
        raise HTTPException(409,"Esta ocorrência já está vinculada ou há um vínculo antigo sem ocorrência definida. Confira o histórico.")
    if not selected and occurrence_already_linked(item,paid_date.isoformat(),paid_date):
        raise HTTPException(409,"Há um vínculo sem ocorrência definida neste período. Confira o histórico antes de vincular.")
    settles_next=bool(not body.historical_only and selected and occurrence and selected==occurrence[0]["due_date"])
    expected_occurrence_amount=str(occurrence[0]["amount"]) if item.get("kind")=="obrigacao" and occurrence else str(item["amount"])
    link={link_key:source_id,"source_kind":source_kind,"due_date":selected,"paid_date":paid_date.isoformat(),"original_description":tx.get("descricao_original") or tx.get("merchant_raw") or "","expected_amount":expected_occurrence_amount,"actual_amount":str(abs(amount)),"linked_at":now,"settled_next":settles_next}
    if selected:
        link["occurrence_key"]="due:"+selected
        if settles_next and occurrence[0].get("base_due_date") and occurrence[0]["base_due_date"]!=selected:
            link["base_due_date"]=occurrence[0]["base_due_date"]
        if settles_next and occurrence[0].get("installment_number") is not None:link["installment_number"]=occurrence[0]["installment_number"]
    else:link["occurrence_requires_review"]=True
    if item.get("frequency")=="mensal": link["period_key"]=(selected or paid_date.isoformat())[:7]
    link["schedule_before"]={field:deepcopy(item.get(field)) for field in ("next_due_date","next_installment_number","status","remaining_charges","schedule_anchor_day","schedule_anchor_end")}
    next_number=(item.get("next_installment_number") or 0)+1
    updates={"updated_at":now,"schedule_anchor_day":item.get("schedule_anchor_day") or as_date(item["next_due_date"]).day,"schedule_anchor_end":item.get("schedule_anchor_end",False)}
    if settles_next:
        next_occurrences=schedule(item,2,include_base=True)
        next_base_due=next_occurrences[1].get("base_due_date") or next_occurrences[1]["due_date"] if len(next_occurrences)>1 else None
        updates["next_due_date"]=bson_date(as_date(next_base_due)) if next_base_due else item["next_due_date"]
        if item["kind"]=="parcelamento":
            updates["next_installment_number"]=next_number
            if next_number>item["installment_count"]: updates["status"]="concluido"
        elif item["kind"]=="obrigacao":
            if item.get("installment_count"):
                updates["next_installment_number"]=next_number
                if next_number>item["installment_count"]: updates["status"]="concluido"
            else:
                updates["status"]="concluido"
        elif item.get("renewal_status")=="nao_renovar":
            remaining=max(0,item["remaining_charges"]-1)
            updates["remaining_charges"]=remaining
            if remaining==0: updates["status"]="concluido"
        elif item.get("renewal_status")=="manual":
            updates["next_due_date"]=bson_date(next_occurrence_date(item))
            updates["status"]="pausado"
        if item.get("end_date") and next_occurrence_date(item)>as_date(item["end_date"]):
            updates["status"]="concluido"
    guard={"_id":item["_id"],"revision":commitment_revision_filter(item),f"linked_transactions.{link_key}":{"$ne":source_id}}
    if link.get("occurrence_key"): guard["linked_transactions.occurrence_key"]={"$ne":link["occurrence_key"]}
    with mongo_transaction(db) as session:
        kwargs=session_args(session)
        try:
            payment_collection=db.transactions if body.transaction_id else db.provisional_transactions
            payment_query={"_id":ObjectId(body.transaction_id)} if body.transaction_id else {"provisional_id":body.provisional_id,"status":"confirmed"}
            touched=payment_collection.update_one(payment_query,{"$set":{"updated_at":now}},**kwargs)
            if touched is not None and touched.matched_count==0:
                raise HTTPException(409,"A movimentação não está mais disponível. Recarregue.")
            result=db.commitments.update_one(guard,{"$set":updates,"$push":{"linked_transactions":link},"$inc":{"revision":1}},**kwargs)
        except DuplicateKeyError:
            raise HTTPException(status_code=409,detail="Esta movimentação já foi vinculada a outro compromisso.")
        if result is not None and result.matched_count==0:
            raise HTTPException(status_code=409,detail="O compromisso mudou ou a cobrança já foi vinculada. Recarregue.")
        db.audit_log.insert_one({"event":"commitment_payment_linked","commitment_id":commitment_id,link_key:source_id,"settled_next":settles_next,"due_date":selected,"paid_date":paid_date.isoformat(),"occurrence_key":link.get("occurrence_key"),"created_at":now},**kwargs)
    return {"commitment_id":commitment_id,"payment":json_safe(link),"status":updates.get("status",item["status"]),"revision":(item.get("revision") or 1)+1}


_SCHEDULE_RESTORE_FIELDS=("next_due_date","next_installment_number","status","remaining_charges","schedule_anchor_day","schedule_anchor_end")


def _linked_payment_source(db,transaction_id,provisional_id):
    if transaction_id:
        if not ObjectId.is_valid(transaction_id): raise HTTPException(422,"Movimentação inválida.")
        return db.transactions.find_one({"_id":ObjectId(transaction_id)}),"transaction_id",transaction_id,"transaction"
    document=db.provisional_transactions.find_one({"provisional_id":provisional_id,"status":"confirmed"})
    if document and document.get("linked_transaction_id"):
        raise HTTPException(409,"Esta captura já corresponde a uma movimentação importada.")
    return document,"provisional_id",provisional_id,"confirmed_capture"


def _schedule_restore(snapshot):
    sets,unset={},{}
    for field in _SCHEDULE_RESTORE_FIELDS:
        if field not in snapshot: continue
        if snapshot[field] is None: unset[field]=""
        else: sets[field]=snapshot[field]
    return sets,unset


@app.post("/v1/commitments/{commitment_id}/payment-unlink",dependencies=[Depends(require_session)])
@durable_mutation('commitment_payment_unlink',('commitment_id',))
def unlink_commitment_payment(commitment_id:str,body:CommitmentPaymentUnlink,db=Depends(database)):
    try:
        with mongo_transaction(db) as session:
            return _atomic_unlink_commitment_payment(commitment_id,body,session_database(db,session))
    except OperationFailure as exc:
        if exc.code==112 or exc.has_error_label("TransientTransactionError"):
            raise HTTPException(409,"O compromisso mudou. Recarregue antes de desvincular.") from exc
        raise


def _atomic_unlink_commitment_payment(commitment_id:str,body:CommitmentPaymentUnlink,db):
    lock_movement_insert(db)
    item=db.commitments.find_one({"commitment_id":commitment_id})
    if not item: raise HTTPException(404,"Compromisso não encontrado.")
    if body.expected_revision is None:
        raise HTTPException(428,"Confira o compromisso antes de desvincular.")
    if body.expected_revision!=(item.get("revision") or 1):
        raise HTTPException(409,"O compromisso mudou. Recarregue antes de desvincular.")
    link_key="transaction_id" if body.transaction_id else "provisional_id"
    source_id=body.transaction_id or body.provisional_id
    links=list(item.get("linked_transactions") or [])
    index=next((position for position,link in enumerate(links) if link.get(link_key)==source_id),None)
    if index is None: raise HTTPException(404,"Este vínculo não está neste compromisso.")
    current=links[index]
    later=links[index+1:]
    moved=bool(current.get("settled_next"))
    snapshot=current.get("schedule_before")
    warning=None
    restore=bool(moved and not later and isinstance(snapshot,dict))
    if moved and later:
        warning="A agenda permanece onde está porque há um vínculo mais recente."
    elif moved and not isinstance(snapshot,dict):
        warning="O vínculo foi removido. A agenda anterior não estava gravada e permanece como está."
    now=datetime.now(timezone.utc)
    sets={"updated_at":now}
    unset={}
    if restore:
        restored,unset=_schedule_restore(snapshot)
        sets.update(restored)
    guard={"_id":item["_id"],"revision":commitment_revision_filter(item),f"linked_transactions.{link_key}":source_id}
    update={"$set":sets,"$inc":{"revision":1},"$pull":{"linked_transactions":{link_key:source_id}}}
    if unset: update["$unset"]=unset
    with mongo_transaction(db) as session:
        kwargs=session_args(session)
        result=db.commitments.update_one(guard,update,**kwargs)
        if result is not None and result.matched_count==0:
            raise HTTPException(409,"O compromisso mudou. Recarregue antes de desvincular.")
        db.audit_log.insert_one({"event":"commitment_payment_unlinked","commitment_id":commitment_id,link_key:source_id,"agenda_restored":restore,"warning":warning,"created_at":now},**kwargs)
    return {"commitment_id":commitment_id,"removed":{link_key:source_id},"agenda_restored":restore,"warning":warning,"revision":(item.get("revision") or 1)+1,"status":sets.get("status",item.get("status")),"next_due_date":json_safe(sets.get("next_due_date",item.get("next_due_date"))),"next_installment_number":sets.get("next_installment_number",item.get("next_installment_number"))}


@app.post("/v1/commitments/{commitment_id}/payment-swap",dependencies=[Depends(require_session)])
@durable_mutation('commitment_payment_swap',('commitment_id',))
def swap_commitment_payment(commitment_id:str,body:CommitmentPaymentSwap,db=Depends(database)):
    try:
        with mongo_transaction(db) as session:
            return _atomic_swap_commitment_payment(commitment_id,body,session_database(db,session))
    except OperationFailure as exc:
        if exc.code==112 or exc.has_error_label("TransientTransactionError"):
            raise HTTPException(409,"O compromisso mudou. Recarregue antes de trocar o lançamento.") from exc
        raise


def _atomic_swap_commitment_payment(commitment_id:str,body:CommitmentPaymentSwap,db):
    lock_movement_insert(db)
    item=db.commitments.find_one({"commitment_id":commitment_id})
    if not item: raise HTTPException(404,"Compromisso não encontrado.")
    if body.expected_revision is None or body.expected_source_token is None:
        raise HTTPException(428,"Confira o compromisso e o lançamento antes de trocar o vínculo.")
    if body.expected_revision!=(item.get("revision") or 1):
        raise HTTPException(409,"O compromisso mudou. Recarregue antes de trocar o lançamento.")
    current_key="transaction_id" if body.transaction_id else "provisional_id"
    current_id=body.transaction_id or body.provisional_id
    links=list(item.get("linked_transactions") or [])
    current=next((link for link in links if link.get(current_key)==current_id),None)
    if current is None: raise HTTPException(404,"Este vínculo não está neste compromisso.")
    replacement,replacement_key,replacement_id,replacement_kind=_linked_payment_source(db,body.replacement_transaction_id,body.replacement_provisional_id)
    if not replacement or not replacement.get("data_evento"): raise HTTPException(404,"Movimentação não encontrada.")
    require_source_token(replacement,body.expected_source_token,"A movimentação mudou. Confira os dados antes de trocar o vínculo.")
    if not eligible_payment(replacement,bool(body.replacement_provisional_id)):
        raise HTTPException(422,"Selecione uma despesa realizada, não um pagamento de fatura ou transferência.")
    if db.commitments.find_one({f"linked_transactions.{replacement_key}":replacement_id}):
        raise HTTPException(409,"Esta movimentação já foi vinculada a um compromisso.")
    amount=decimal_amount(replacement.get("valor"))
    now=datetime.now(timezone.utc)
    paid_date=local_date(replacement["data_evento"])
    sets={"updated_at":now,f"linked_transactions.$.{replacement_key}":replacement_id,"linked_transactions.$.source_kind":replacement_kind,"linked_transactions.$.paid_date":paid_date.isoformat(),"linked_transactions.$.original_description":replacement.get("descricao_original") or replacement.get("merchant_raw") or "","linked_transactions.$.actual_amount":str(abs(amount))}
    unset={}
    if replacement_key!=current_key: unset[f"linked_transactions.$.{current_key}"]=""
    guard={"_id":item["_id"],"revision":commitment_revision_filter(item),f"linked_transactions.{current_key}":current_id}
    update={"$set":sets,"$inc":{"revision":1}}
    if unset: update["$unset"]=unset
    with mongo_transaction(db) as session:
        kwargs=session_args(session)
        try:
            result=db.commitments.update_one(guard,update,**kwargs)
        except DuplicateKeyError:
            raise HTTPException(409,"Esta movimentação já foi vinculada a outro compromisso.")
        if result is not None and result.matched_count==0:
            raise HTTPException(409,"O compromisso mudou. Recarregue antes de trocar o lançamento.")
        db.audit_log.insert_one({"event":"commitment_payment_swapped","commitment_id":commitment_id,"from":current_id,"to":replacement_id,"due_date":current.get("due_date"),"created_at":now},**kwargs)
    return {"commitment_id":commitment_id,"agenda_restored":False,"warning":None,"revision":(item.get("revision") or 1)+1,"next_due_date":json_safe(item.get("next_due_date")),"next_installment_number":item.get("next_installment_number"),"payment":{replacement_key:replacement_id,"due_date":current.get("due_date"),"settled_next":current.get("settled_next"),"occurrence_key":current.get("occurrence_key")}}


@app.get("/v1/investments", dependencies=[Depends(require_session)])
def investments(db=Depends(database)):
    return {"items":[json_safe(x) for x in db.investments.find().limit(200)],"positions_available":db.investments.count_documents({})>0}

@app.get("/v1/categories", dependencies=[Depends(require_session)])
def categories(db=Depends(database)):
    catalog={item["key"]:item for item in db.categories.find({"active":{"$ne":False}})}
    names=set(catalog_category_names(db))
    ordered=sorted(names,key=lambda value:value.casefold())
    transaction_counts={}
    for item in db.transactions.aggregate([{"$group":{"_id":"$categoria","count":{"$sum":1}}}]):
        if item.get("_id"):
            k=canonical_key(item["_id"])
            transaction_counts[k]=transaction_counts.get(k,0)+item["count"]
    provisional_counts={}
    for item in db.provisional_transactions.aggregate([{"$match":{"status":{"$in":["provisional","enriched","confirmed"]}}},{"$group":{"_id":"$categoria_sugerida","count":{"$sum":1}}}]):
        if item.get("_id"):
            k=canonical_key(item["_id"])
            provisional_counts[k]=provisional_counts.get(k,0)+item["count"]
    extra_counts={}
    if not isinstance(db, dict) or "commitments" in db:
        for row in db.commitments.find({}):
            cat=row.get("category") or row.get("categoria") or row.get("categoria")
            if cat:
                k=canonical_key(cat)
                extra_counts[k]=extra_counts.get(k,0)+1
    if not isinstance(db, dict) or "future_schedule_overrides" in db:
        for row in db.future_schedule_overrides.find({}):
            cat=(row.get("profile") or {}).get("categoria") if isinstance(row.get("profile"), dict) else None
            if cat:
                k=canonical_key(cat)
                extra_counts[k]=extra_counts.get(k,0)+1
    if not isinstance(db, dict) or "saving_experiments" in db:
        for row in db.saving_experiments.find({}):
            cat=row.get("category") or row.get("categoria")
            if cat:
                k=canonical_key(cat)
                extra_counts[k]=extra_counts.get(k,0)+1
    details=[]
    subcategories_by_category=category_subcategories(db)
    for name in ordered:
        k=canonical_key(name)
        transaction_count=transaction_counts.get(k,0)
        provisional_count=provisional_counts.get(k,0)
        extra_count=extra_counts.get(k,0)
        details.append({"name":name,"usage_count":transaction_count+provisional_count+extra_count,"transaction_count":transaction_count,"provisional_count":provisional_count,"custom":k in catalog,"subcategory_count":len(subcategories_by_category.get(name, []))})
    return {"items":ordered,"details":details}

class CategoryCreate(BaseModel):
    name:str=Field(min_length=1,max_length=120)

@app.post("/v1/categories",status_code=status.HTTP_201_CREATED,dependencies=[Depends(require_session)])
def create_category(category:CategoryCreate,db=Depends(database)):
    with mongo_transaction(db) as session:
        return _atomic_create_category(category, db=session_database(db,session))

def _atomic_create_category(category:CategoryCreate,db=Depends(database)):
    lock_movement_insert(db)
    name=re.sub(r"\s+"," ",category.name).strip();key=canonical_key(name)
    if not key: raise HTTPException(status_code=422,detail="Informe um nome de categoria válido.")
    existing_names=catalog_category_names(db)
    existing=db.categories.find_one({"key":key})
    matched=next((value for value in existing_names if canonical_key(value)==key),None)
    if (existing and existing.get("active",True)) or matched: raise HTTPException(status_code=409,detail=f"A categoria '{(existing or {}).get('name') or matched}' já existe.")
    now=datetime.now(timezone.utc)
    db.categories.update_one({"key":key},{"$set":{"name":name,"key":key,"active":True,"updated_at":now,"source":"mobile_manual"},"$setOnInsert":{"created_at":now}},upsert=True)
    db.audit_log.insert_one({"event":"category_created","category":name,"source":"mobile_manual","created_at":now})
    return {"name":name,"usage_count":0,"custom":True}

@app.delete("/v1/categories",dependencies=[Depends(require_session)])
def delete_category(name:str=Query(min_length=1,max_length=120),replacement:str|None=Query(default=None,min_length=1,max_length=120),db=Depends(database)):
    with mongo_transaction(db) as session:
        return _atomic_delete_category(name, replacement, db=session_database(db,session))

def _atomic_delete_category(name:str=Query(min_length=1,max_length=120),replacement:str|None=Query(default=None,min_length=1,max_length=120),db=Depends(database)):
    lock_movement_insert(db)
    known={canonical_key(value):value for value in catalog_category_names(db)}
    key=canonical_key(name);canonical_name=known.get(key)
    if not canonical_name: raise HTTPException(status_code=404,detail="Categoria não encontrada.")
    tx_rows=[row for row in db.transactions.find({"categoria":{"$nin":[None,""]}},{"_id":1,"categoria":1}) if canonical_key(row.get("categoria"))==key]
    prov_rows=[row for row in db.provisional_transactions.find({"categoria_sugerida":{"$nin":[None,""]},"status":{"$in":["provisional","enriched","confirmed"]}},{"_id":1,"categoria_sugerida":1}) if canonical_key(row.get("categoria_sugerida"))==key]
    comm_rows=[row for row in db.commitments.find({}) if canonical_key(row.get("category") or row.get("categoria") or "")==key] if (not isinstance(db, dict) or "commitments" in db) else []
    over_rows=[row for row in db.future_schedule_overrides.find({}) if isinstance(row.get("profile"), dict) and canonical_key(row["profile"].get("categoria") or "")==key] if (not isinstance(db, dict) or "future_schedule_overrides" in db) else []
    exp_rows=[row for row in db.saving_experiments.find({}) if canonical_key(row.get("category") or row.get("categoria") or "")==key] if (not isinstance(db, dict) or "saving_experiments" in db) else []
    transaction_count=len(tx_rows)
    provisional_count=len(prov_rows)
    extra_count=len(comm_rows)+len(over_rows)+len(exp_rows)
    usage_count=transaction_count+provisional_count+extra_count
    canonical_replacement=None
    if usage_count:
        if not replacement: raise HTTPException(status_code=409,detail=f"A categoria está em uso por {usage_count} registro(s). Escolha uma categoria substituta.")
        replacement_key=canonical_key(replacement);canonical_replacement=known.get(replacement_key)
        if not canonical_replacement: raise HTTPException(status_code=422,detail="A categoria substituta precisa existir no catálogo.")
        if replacement_key==key: raise HTTPException(status_code=422,detail="Escolha uma categoria substituta diferente.")
    now=datetime.now(timezone.utc)
    if canonical_replacement:
        if tx_rows:
            tx_ids=[row["_id"] for row in tx_rows if "_id" in row]
            if tx_ids:
                db.transactions.update_many({"_id":{"$in":tx_ids}},{"$set":{"categoria":canonical_replacement,"updated_at":now},"$unset":{"subcategoria":""}})
            else:
                db.transactions.update_many({"categoria":canonical_name},{"$set":{"categoria":canonical_replacement,"updated_at":now},"$unset":{"subcategoria":""}})
        if prov_rows:
            prov_ids=[row["_id"] for row in prov_rows if "_id" in row]
            if prov_ids:
                db.provisional_transactions.update_many({"_id":{"$in":prov_ids},"status":{"$in":["provisional","enriched","confirmed"]}},{"$set":{"categoria_sugerida":canonical_replacement,"updated_at":now},"$unset":{"subcategoria":""},"$inc":{"revision":1}})
            else:
                db.provisional_transactions.update_many({"categoria_sugerida":canonical_name,"status":{"$in":["provisional","enriched","confirmed"]}},{"$set":{"categoria_sugerida":canonical_replacement,"updated_at":now},"$unset":{"subcategoria":""},"$inc":{"revision":1}})
        for row in comm_rows:
            updates={"category":canonical_replacement,"updated_at":now}
            if "categoria" in row: updates["categoria"]=canonical_replacement
            db.commitments.update_one({"_id":row["_id"]},{"$set":updates,"$unset":{"subcategoria":""},"$inc":{"revision":1}})
        for row in over_rows:
            profile=dict(row.get("profile") or {})
            profile["categoria"]=canonical_replacement
            profile.pop("subcategoria",None)
            db.future_schedule_overrides.update_one({"_id":row["_id"]},{"$set":{"profile":profile,"updated_at":now},"$inc":{"revision":1}})
        for row in exp_rows:
            exp_updates={"category":canonical_replacement,"updated_at":now}
            if "categoria" in row: exp_updates["categoria"]=canonical_replacement
            db.saving_experiments.update_one({"_id":row["_id"]},{"$set":exp_updates,"$inc":{"revision":1}})
    db.categories.update_one({"key":key},{"$set":{"name":canonical_name,"active":False,"deleted_at":now,"replacement":canonical_replacement,"updated_at":now}},upsert=True)
    db.audit_log.insert_one({"event":"category_deleted","category":canonical_name,"replacement":canonical_replacement,"affected_transactions":transaction_count,"affected_provisionals":provisional_count,"affected_commitments":len(comm_rows)+len(over_rows),"affected_experiments":len(exp_rows),"source":"mobile_manual","created_at":now})
    return {"name":canonical_name,"deleted":True,"replacement":canonical_replacement,"affected_count":usage_count}
