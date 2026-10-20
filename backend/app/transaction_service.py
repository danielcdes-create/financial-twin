from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4, uuid5, NAMESPACE_URL
from copy import copy, deepcopy
from app.source_tokens import source_token
from bson.decimal128 import Decimal128
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from pymongo.errors import OperationFailure
import logging
from app.matching import classify, score_evidence, automatic_eligible
from app.platforms import apply_historical_capture_defaults, platform_identity
from app.taxonomy import infer_classification, taxonomy_overrides
from app.account_profiles import account_profiles, validate_account_key
from fastapi import HTTPException
from app.atomic import mongo_transaction, session_args, session_database, with_batch_size
from app.movement_insert_lock import lock_movement_insert
from app.schemas import validate_parsed_payload
from app.parsers import parse_datetime
from app.safe_errors import safe_error
from app.matching_scope import compatible, strong_reference
import re, unicodedata

class EventConflict(Exception): pass
class EventBusy(Exception): pass

def stored_instant(value):
    if isinstance(value,str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if not isinstance(value,datetime): raise ValueError("Data de captura inválida.")
    value=value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.replace(microsecond=(value.microsecond//1000)*1000)


ITAÚ_PURCHASE_RE=re.compile(
    r"compra(?:\s+parcelada)?\s+aprovada\s+de\s+R\$\s*(?P<amount>[0-9.]+,[0-9]{2})"
    r"(?:\s+em\s+(?P<installments>\d{1,2})\s*x)?"
    r"\s+em\s+(?P<merchant>.+?)\s+no\s+dia\s+(?P<day>\d{2})/(?P<month>\d{2})(?:/(?P<year>\d{2,4}))?"
    r"\s+[àa]s\s+(?P<hour>\d{2}):(?P<minute>\d{2})"
    r"(?:\s+no\s+cart[aã]o\s+(?P<card>[^.!\n]+))?",
    re.IGNORECASE)

def enrich_notification_event(event:dict[str,Any])->dict[str,Any]:
    if event.get("source_type")!="android_notification": return event
    # Native parsing deliberately preserves conflicting financial messages.
    # A fallback regex must not turn that uncertainty into a purchase.
    if (event.get('parsed_payload') or {}).get('event_type') == 'financial_notification': return event
    raw=event.get("raw_payload") or {};text=str(raw.get("text") or raw.get("big_text") or "")
    match=ITAÚ_PURCHASE_RE.search(text)
    if not match: return event
    parsed=dict(event.get("parsed_payload") or {})
    parsed["merchant_raw"]=match.group("merchant").strip(" .,-")
    if parsed.get("amount") is None: parsed["amount"]=match.group("amount").replace(".","").replace(",",".")
    installments=match.group("installments")
    if installments and 2<=int(installments)<=48:
        parsed["installment_count"]=int(installments)
        parsed["event_type"]="card_purchase"
        parsed["direction"]="expense"
        parsed["payment_method"]="cartao_credito"
    card=(match.group("card") or "").strip(" .,-")
    if card and not parsed.get("payment_instrument_hint"): parsed["payment_instrument_hint"]=card
    captured=event.get("captured_at")
    if isinstance(captured,str):
        try: captured=datetime.fromisoformat(captured.replace("Z","+00:00"))
        except ValueError: captured=datetime.now(timezone.utc)
    if not isinstance(captured,datetime): captured=datetime.now(timezone.utc)
    if captured.tzinfo is None: captured=captured.replace(tzinfo=timezone.utc)
    brasilia=timezone(timedelta(hours=-3));local=captured.astimezone(brasilia);raw_year=match.group("year")
    year=int(raw_year) if raw_year else local.year
    if raw_year and year<100: year+=2000
    try:
        transaction_time=datetime(year,int(match.group("month")),int(match.group("day")),int(match.group("hour")),int(match.group("minute")),tzinfo=brasilia)
        if not raw_year and transaction_time>local+timedelta(days=2): transaction_time=transaction_time.replace(year=year-1)
        parsed["event_timestamp"]=transaction_time.astimezone(timezone.utc).isoformat()
    except ValueError: pass
    event["parsed_payload"]=parsed
    event["parser_version"]="android-notification-br-v3" if parsed.get("installment_count") else "android-notification-br-v2"
    return event

def merchant_alias_key(value:str)->str:
    plain=unicodedata.normalize("NFKD",value).encode("ascii","ignore").decode("ascii").upper()
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9 ]"," ",plain)).strip()

class TransactionService:
    def __init__(self, db):
        self.db=db
        self.events=db["raw_events"]
        self.ledger=db["transactions"]
        self.evidence=db["transaction_evidence"]
        self.provisional=db["provisional_transactions"]
        self.review=db["review_queue"]
        self.audit=db["audit_log"]
        self.events.create_index("event_id",unique=True)
        self.events.create_index([("processing_status",1),("received_at",1)],name="pending_event_recovery")
        self.evidence.create_index("event_id",unique=True)
        self.provisional.create_index("event_id",unique=True)
        self.review.create_index([("status",1),("created_at",-1)])

    def scoped(self,db):
        """Reuse initialized indexes while joining a parent financial transaction."""
        service=copy(self)
        service.db=db
        for name,collection in (('events','raw_events'),('ledger','transactions'),('evidence','transaction_evidence'),
                                ('provisional','provisional_transactions'),('review','review_queue'),('audit','audit_log')):
            setattr(service,name,db[collection])
        return service

    def process(self,event:dict[str,Any])->dict[str,Any]:
        # Parsing and human edits are mutable projections; replay identity is the
        # originally submitted command, retained separately from those fields.
        submitted={key:deepcopy(event.get(key)) for key in ('source_type','source_provider','captured_at','raw_payload','parsed_payload','parser_version')}
        submitted['captured_at']=stored_instant(submitted['captured_at'])
        ingest_token=source_token(submitted)
        event=enrich_notification_event(dict(event))
        event["captured_at"]=stored_instant(event["captured_at"])
        validate_parsed_payload(event.get("parsed_payload"))
        existing=self.events.find_one({"event_id":event["event_id"]})
        if existing:
            keys=("source_type","source_provider","captured_at","raw_payload","parsed_payload","parser_version")
            if event.get('source_type')=='android_notification':
                # Native notification identity is its original evidence. This
                # also recognizes legacy notifications whose mutable parser
                # fields predate ingest_payload, without rewriting history.
                identity_conflict=(
                    any(existing.get(k)!=event.get(k) for k in ('source_type','source_provider','raw_payload'))
                    or stored_instant(existing.get('captured_at')) != event['captured_at']
                )
            else:
                identity_conflict=(existing.get('ingest_token') != ingest_token if existing.get('ingest_token') else any(existing.get(k)!=event.get(k) for k in keys))
            if identity_conflict:
                raise EventConflict(event["event_id"])
            if existing.get("processing_status")=="rejected":
                raise ValueError(existing.get("processing_error") or "invalid event")
            if existing.get("processing_status")!="pending":
                return {"status":existing.get("processing_status","already_received"),"event_id":event["event_id"],"duplicate":True,"linked_transaction_id":str(existing["linked_transaction_id"]) if existing.get("linked_transaction_id") else None}
        else:
            event.update({"received_at":datetime.now(timezone.utc),"processing_status":"pending","confidence":None,"linked_transaction_id":None,
                          'ingest_token':ingest_token,'ingest_payload':submitted})
            try: self.events.insert_one(event)
            except DuplicateKeyError: return self.process({'event_id':event['event_id'],**submitted})
        for attempt in range(3):
            try:
                with mongo_transaction(self.db) as session:
                    stored=self.events.find_one({"event_id":event["event_id"]},**session_args(session))
                    if stored.get("processing_status")!="pending":
                        return {"status":stored["processing_status"],"event_id":event["event_id"],"duplicate":True,"linked_transaction_id":str(stored["linked_transaction_id"]) if stored.get("linked_transaction_id") else None}
                    result=self._finish(stored,session)
                break
            except DuplicateKeyError as exc:
                if attempt==2: raise EventBusy(event["event_id"]) from exc
            except OperationFailure as exc:
                if not (exc.code==112 or exc.has_error_label("TransientTransactionError")):
                    raise
                if attempt==2: raise EventBusy(event["event_id"]) from exc
        if result["status"]=="rejected":
            raise ValueError(result["reason"])
        return result

    def recover_pending(self,limit:int=100)->int:
        """Re-run durable events left pending by an interrupted worker."""
        recovered=0
        cutoff=datetime.now(timezone.utc)-timedelta(seconds=30)
        now=datetime.now(timezone.utc)
        query={"processing_status":"pending","received_at":{"$lt":cutoff},"$or":[{"next_attempt_at":{"$exists":False}},{"next_attempt_at":{"$lte":now}}]}
        for event in with_batch_size(self.events.find(query)).sort("received_at",1).limit(limit):
            try:
                self.process({'event_id':event['event_id'], **(event.get('ingest_payload') or {key:event.get(key) for key in ("source_type","source_provider","captured_at","raw_payload","parsed_payload","parser_version")})})
                recovered+=1
            except ValueError as exc:
                self.events.update_one({"event_id":event["event_id"],"processing_status":"pending"},{"$set":{"processing_status":"rejected","processing_error":safe_error(exc),"last_attempt_at":now},"$inc":{"processing_attempts":1}})
            except Exception as exc:
                attempts=event.get("processing_attempts",0)+1
                self.events.update_one({"event_id":event["event_id"],"processing_status":"pending"},{"$set":{"last_attempt_at":now,"processing_error":safe_error(exc),"next_attempt_at":now+timedelta(seconds=min(3600,30*2**min(attempts,7)))},"$inc":{"processing_attempts":1}})
                logging.error("Could not recover pending event %s (%s)",event.get("event_id"),safe_error(exc))
        return recovered

    def _finish(self,event:dict[str,Any],session=None)->dict[str,Any]:
        kwargs=session_args(session)
        lock_movement_insert(self.db, session)
        tx_db=session_database(self.db,session)
        # Load account_profiles once per event ingestion and reuse in memory (eliminates N+1 queries).
        profiles=account_profiles(tx_db)
        try:
            setattr(tx_db,"_account_profiles_cache",profiles)
        except Exception:
            pass
        try:
            parsed=event.get("parsed_payload") or {}
            validate_parsed_payload(parsed)
            own_transfer=parsed.get("event_type")=="own_transfer"
            if own_transfer:
                origin=parsed.get("from_account_key")
                destination=parsed.get("to_account_key")
                try:
                    validate_account_key(origin, tx_db, selectable=True)
                    validate_account_key(destination, tx_db, selectable=True)
                    if origin==destination: raise HTTPException(422,"Contas iguais")
                except HTTPException:
                    self.events.update_one({"event_id":event["event_id"]},{"$set":{"processing_status":"rejected","processing_error":"invalid_transfer_accounts"}},**kwargs)
                    return {"status":"rejected","event_id":event["event_id"],"reason":"select two different owned accounts"}
            amount=parsed.get("amount")
            merchant=parsed.get("merchant_raw") or parsed.get("merchant")
            try: amount_decimal=Decimal(str(amount)) if amount is not None else None
            except (InvalidOperation,ValueError): amount_decimal=None
            if amount_decimal is not None and (not amount_decimal.is_finite() or amount_decimal<=0 or amount_decimal>Decimal("100000000")):
                self.events.update_one({"event_id":event["event_id"]},{"$set":{"processing_status":"rejected","processing_error":"invalid_amount"}},**kwargs)
                return {"status":"rejected","event_id":event["event_id"],"reason":"amount must be a finite positive value no greater than 100000000"}
            if parsed.get("currency","BRL")!="BRL":
                self.events.update_one({"event_id":event["event_id"]},{"$set":{"processing_status":"rejected","processing_error":"unsupported_currency"}},**kwargs)
                return {"status":"rejected","event_id":event["event_id"],"reason":"only BRL is supported in this MVP"}
            if amount_decimal is None or not merchant:
                self._parse_review(event, session)
                self.events.update_one({"event_id":event["event_id"]},{"$set":{"processing_status":"needs_parse"}},**kwargs)
                return {"status":"needs_parse","event_id":event["event_id"]}
            stamp=parsed.get("event_timestamp") or event["captured_at"]
            if isinstance(stamp,str):
                try: stamp=datetime.fromisoformat(stamp.replace("Z","+00:00"))
                except ValueError:
                    self._parse_review(event, session)
                    self.events.update_one({'event_id':event['event_id']},{'$set':{'processing_status':'needs_parse','processing_error':'invalid_event_timestamp'}},**kwargs)
                    return {'status':'needs_parse','event_id':event['event_id'],'reason':'invalid_event_timestamp'}
            if not isinstance(stamp,datetime):raise ValueError('Data do evento inválida.')
            from app.parcelamento_link import claim_existing_purchase, installment_count_of, mark_installment_draft, open_parcelamento_for_capture
            installment_count=None if own_transfer else installment_count_of(parsed)
            if installment_count and claim_existing_purchase(tx_db,event,str(merchant),installment_count):
                return {"status":"matched","target_type":"provisional","event_id":event["event_id"],"parcelamento":True}
            tx_date=parse_datetime(stamp).astimezone(timezone.utc)
            matching_event={**event,'parsed_payload':{**parsed,'event_timestamp':tx_date}}
            date_filter={"data_evento":{"$gte":tx_date-timedelta(days=3),"$lte":tx_date+timedelta(days=3)}}
            candidates=[] if own_transfer else [("transaction",tx) for tx in with_batch_size(self.ledger.find(date_filter,**kwargs)).limit(501)]
            if not own_transfer:
                candidates.extend(("provisional",tx) for tx in with_batch_size(self.provisional.find({**date_filter,"status":{"$in":["provisional","enriched","confirmed"]}},**kwargs)).limit(501))
            if parsed.get('bank_transaction_ref') and not own_transfer:
                reference_query={'bank_transaction_ref':parsed['bank_transaction_ref'],'source_provider':event['source_provider'],
                                 'account_identity':parsed.get('account_identity') or ''}
                known={(kind,str(tx['_id'])) for kind,tx in candidates}
                for kind,collection in (('transaction',self.ledger),('provisional',self.provisional)):
                    query={**reference_query,**({'status':{'$in':['provisional','enriched','confirmed']}} if kind=='provisional' else {})}
                    for tx in with_batch_size(collection.find(query,**kwargs)).limit(501):
                        if (kind,str(tx['_id'])) not in known:candidates.append((kind,tx))
            truncated=any(sum(1 for kind,_ in candidates if kind==source)>500 for source in ("transaction","provisional"))
            accounts_by_id=None
            if event.get('source_type')=='android_notification' and candidates:
                acc_ids=[tx.get('account_id') for _,tx in candidates if tx.get('account_id') and not tx.get('account_profile_id')]
                if acc_ids:
                    unique_ids=list({str(aid):aid for aid in acc_ids}.values())
                    accounts_by_id={}
                    for acc in with_batch_size(tx_db.accounts.find({'_id':{'$in':unique_ids}})):
                        accounts_by_id[acc.get('_id')]=acc
                        accounts_by_id[str(acc.get('_id'))]=acc
            ranked=[]
            best,best_type,best_score,best_parts=None,None,0.0,{}
            for candidate_type,tx in candidates:
                if not compatible(event,tx,tx_db,profiles=profiles,accounts_by_id=accounts_by_id):continue
                score,parts=score_evidence(matching_event,tx)
                if strong_reference(event,tx) and automatic_eligible(event,tx,tx_db,profiles=profiles,accounts_by_id=accounts_by_id):
                    score=1.0;parts['bank_reference']=1.0
                ranked.append(score)
                if score>best_score: best,best_type,best_score,best_parts=tx,candidate_type,score,parts
            level=classify(best_score) if best else "LOW"
            validation=(event.get('raw_payload') or {}).get('validation') or {}
            receipt_mismatch=validation.get('review_required') is True
            if receipt_mismatch and level=='HIGH':level='MEDIUM'
            ranked.sort(reverse=True)
            ambiguous=len(ranked)>1 and best_score-ranked[1]<0.08
            if level=="HIGH" and (truncated or ambiguous or not automatic_eligible(event,best,tx_db,profiles=profiles,accounts_by_id=accounts_by_id)): level="MEDIUM"
            now=datetime.now(timezone.utc)
            if best and level=="HIGH":
                # Serialize new matches with an explicit deletion of the same active row.
                target=self.ledger if best_type=="transaction" else self.provisional
                touched=target.update_one({"_id":best["_id"]},{"$set":{"updated_at":now}},**kwargs)
                if touched is not None and getattr(touched,"matched_count",1)==0:
                    raise EventBusy(event["event_id"])
                evidence={"evidence_id":str(uuid4()),"event_id":event["event_id"],"target_type":best_type,"match_score":best_score,"score_components":best_parts,"decision":"automatic","created_at":now}
                if best_type=="transaction": evidence["transaction_id"]=best["_id"]
                else: evidence["provisional_id"]=best["provisional_id"]
                self.evidence.insert_one(evidence,**kwargs)
                self.events.update_one({"event_id":event["event_id"],"processing_status":"pending"},{"$set":{"processing_status":"matched","linked_transaction_id":best["_id"],"confidence":best_score}},**kwargs)
                if best_type=="provisional":
                    self.provisional.update_one({"_id":best["_id"]},{"$set":{"status":"enriched"} if best.get("status")=="provisional" else {"updated_at":now},"$inc":{"evidence_count":1,'revision':1}},**kwargs)
                return {"status":"matched","target_type":best_type,"transaction_id":str(best["_id"]),"score":best_score}
            signed=amount_decimal.quantize(Decimal("0.01"))*(-1 if parsed.get("direction","expense")=="expense" else 1)
            merchant_entity=None if own_transfer else self.db.merchants.find_one({"aliases.normalizado":merchant_alias_key(str(merchant))},{"nome_padrao":1},**kwargs)
            draft={"provisional_id":str(uuid4()),"event_id":event["event_id"],"data_evento":tx_date,"valor":Decimal128(str(signed)),"fluxo":"saida" if signed<0 else "entrada","moeda":parsed.get("currency","BRL"),"merchant_raw":merchant,"merchant_normalized":"".join(c.lower() for c in merchant if c.isalnum()),"categoria_sugerida":parsed.get("category"),"items":parsed.get("items",[]),"status":"provisional","revision":1,"candidate_scan_truncated":truncated,"match_score":best_score,"match_candidate_id":best["_id"] if best else None,"created_at":now}
            if parsed.get('bank_transaction_ref'):
                draft.update({'bank_transaction_ref':parsed['bank_transaction_ref'],'account_identity':parsed.get('account_identity') or '',
                              'source_provider':event['source_provider']})
            if parsed.get('payment_instrument_hint'):draft['instrument_hint']=parsed['payment_instrument_hint']
            if parsed.get('account_profile_id'):
                validate_account_key(parsed['account_profile_id'],tx_db,selectable=event.get('source_type')=='manual')
                draft['account_profile_id']=parsed['account_profile_id']
            if parsed.get('payment_method') and not own_transfer: draft['metodo_pagamento']=parsed['payment_method']
            draft['event_time_basis']='explicit_offset' if stamp.tzinfo is not None else 'assumed_brasilia'
            if own_transfer:
                draft.update({"tipo":"transferencia","natureza":"TRANSFERENCIA","transfer":{"from_account_key":origin,"to_account_key":destination,"reconciliation_status":"aguardando_extratos"}})
            if parsed.get("event_type") in {"card_purchase","receipt","manual_purchase"}: draft["tipo"]="compra"
            if parsed.get("event_type")=="manual_income": draft["tipo"]="receita"
            if isinstance(self.db, Database):
                from app.main import cached_taxonomy_overrides
                overrides=cached_taxonomy_overrides(self.db)
            else:
                overrides=taxonomy_overrides(self.db,**kwargs)
            suggestion=infer_classification(draft,overrides)
            if suggestion.get("categoria") and not draft.get("categoria_sugerida"): draft["categoria_sugerida"]=suggestion["categoria"]
            if suggestion.get("subcategoria"): draft["subcategoria"]=suggestion["subcategoria"]
            if suggestion.get("natureza"): draft["natureza"]=suggestion["natureza"]
            detected_platform=None if own_transfer else platform_identity(str(merchant))
            platform=self.db.merchants.find_one({"external_key":detected_platform[0]},{"nome_padrao":1},**kwargs) if detected_platform else None
            if platform: draft["platform_id"]=platform["_id"]
            if merchant_entity and (not platform or merchant_entity["_id"]!=platform["_id"]): draft.update({"merchant_id":merchant_entity["_id"],"merchant_name":merchant_entity.get("nome_padrao")})
            if not own_transfer: apply_historical_capture_defaults(tx_db,draft,profiles=profiles)
            if installment_count: mark_installment_draft(draft,installment_count,amount_decimal)
            self.provisional.insert_one(draft,**kwargs)
            if (best and level=="MEDIUM") or receipt_mismatch:
                item={"review_id":str(uuid4()),"event_id":event["event_id"],"provisional_id":draft["provisional_id"],"candidate_type":best_type,"match_score":best_score,"score_components":best_parts,"candidate_scan_truncated":truncated,"ambiguous":ambiguous,"status":"pending","created_at":now}
                if best and best_type=="transaction": item["candidate_transaction_id"]=best["_id"]
                elif best: item["candidate_provisional_id"]=best["provisional_id"]
                if receipt_mismatch:
                    item.update({'reason':'item_total_mismatch','expected_total':validation['total'],'items_total':validation['items_total'],
                                 'difference':validation['items_total']-validation['total']})
                self.review.insert_one(item,**kwargs)
                self.events.update_one({"event_id":event["event_id"],"processing_status":"pending"},{"$set":{"processing_status":"review_required","confidence":best_score,
                    **({'processing_error':'item_total_mismatch'} if receipt_mismatch else {})}},**kwargs)
                return {"status":"review_required","provisional_id":draft["provisional_id"],"review_id":item["review_id"],"score":best_score}
            self.events.update_one({"event_id":event["event_id"],"processing_status":"pending"},{"$set":{"processing_status":"provisional","confidence":0.5}},**kwargs)
            commitment_id=open_parcelamento_for_capture(tx_db,event,draft,parsed) if installment_count else None
            return {"status":"provisional","provisional_id":draft["provisional_id"],"score":best_score,**({"commitment_id":commitment_id} if commitment_id else {})}
        finally:
            if "_account_profiles_cache" in getattr(tx_db, "__dict__", {}):
                tx_db.__dict__.pop("_account_profiles_cache", None)

    def _parse_review(self,event,session=None):
        identity=str(uuid5(NAMESPACE_URL,'parse-review:'+event['event_id']))
        if self.review.find_one({'review_id':identity},**session_args(session)):return
        self.review.insert_one({'review_id':identity,'event_id':event['event_id'],'status':'pending','reason':'event_needs_parse',
                                'created_at':datetime.now(timezone.utc)},**session_args(session))
