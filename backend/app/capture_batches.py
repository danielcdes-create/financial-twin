"""Reviewable statement drafts and atomic, duplication-aware insertion."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from app.auth import require_session
from app.atomic import mongo_snapshot, mongo_transaction, session_database
from app.account_profiles import validate_account_key, legacy_accounts_by_profile, profile_for_legacy
from app.financial_time import LOCAL_ZONE, day_start, local_date
from app.extracted_currency import normalize_extracted_currency
from app.media_store import store_media
from app.movement_insert_lock import lock_movement_insert
from app.statement_extraction import MAX_ROWS, StatementLine, extract_statement, extract_table
from app.pdf_input import PDFPasswordError
from app.services.ai.xai.provider import XAIError, public_xai_error
from app.upload_worker import run_upload
from app.pagination import page
from app.taxonomy import infer_classification, taxonomy_overrides

router = APIRouter(prefix='/v1/capture-batches', dependencies=[Depends(require_session)])


def database(request: Request):
    if request.app.state.db is None: raise HTTPException(503, 'Banco indisponível.')
    return request.app.state.db


class ReviewedLine(StatementLine):
    row_id: int = Field(ge=1, le=MAX_ROWS)
    selected: bool = True
    confirmed_distinct: bool = False
    # Legacy non-table drafts used an explicit BRL extraction contract. New table
    # drafts always include currency, including null when it is unknown.
    currency: str | None = Field(default='BRL', max_length=40)


class BatchReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_ref: str = Field(min_length=1, max_length=100)
    rows: list[ReviewedLine] = Field(min_length=1, max_length=MAX_ROWS)
    acknowledge_partial: bool = False
    acknowledge_brl: bool = False


class BatchCommit(BatchReview):
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')


class BatchDiscard(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


def draft_view(batch):
    return {key: batch[key] for key in ('batch_id', 'filename', 'sha256', 'rows', 'warnings', 'complete', 'account_hint', 'status')}


def checked_table_currency(batch, data=None):
    """Read retained bytes for old table drafts whose extractor discarded currency.

    Keep the historical draft unchanged. Its returned source version includes the
    recovered evidence, so the UI preserves an older local draft as a conflict.
    """
    if batch.get('status') != 'draft' or Path(batch.get('filename', '')).suffix.casefold() not in {'.csv', '.xlsx'}:
        return batch
    if all('currency' in row for row in batch['rows']): return batch
    try:
        original = data if data is not None else Path(batch['storage_key']).read_bytes()
        if hashlib.sha256(original).hexdigest() != batch['sha256']: raise ValueError('Checksum diferente.')
        extracted = extract_table(batch['filename'], original)
        if len(extracted.rows) != len(batch['rows']): raise ValueError('A quantidade de linhas mudou.')
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(422, 'Não foi possível conferir a moeda no arquivo original deste lote antigo. Preserve a revisão e capture novamente o arquivo.') from exc
    return {**batch, 'rows': [{**row, 'currency': source.currency} for row, source in zip(batch['rows'], extracted.rows)]}


def read_batch_upload(file):
    data = file.file.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024: raise HTTPException(413, 'Escolha um arquivo de até 10 MiB.')
    return data


def scan_sync(file, batch_id, request, pdf_cpf=None):
    db = request.app.state.db
    data = read_batch_upload(file)
    digest = hashlib.sha256(data).hexdigest(); identity = str(batch_id)
    previous = db.capture_batches.find_one({'_id': identity})
    if previous:
        if previous['sha256'] != digest: raise HTTPException(409, 'O lote já usa outro arquivo.')
        previous = checked_table_currency(previous, data)
        return {'persisted': True, 'event_id': identity, 'draft_persisted': True, 'needs_review': previous['status'] == 'draft', 'batch': draft_view(previous)}
    cached = db.statement_extractions.find_one({'_id': digest})
    try:
        if cached and (Path(file.filename or '').suffix.casefold() not in {'.csv', '.xlsx'} or cached.get('table_currency_version') == 1):
            extraction = cached['extraction']
        else:
            parsed = extract_statement(file.filename or 'statement', data, pdf_cpf)
            if parsed.document_kind == 'other': raise ValueError('Este arquivo não foi reconhecido como extrato. Use a captura de comprovante.')
            if not parsed.rows: raise ValueError('Nenhuma movimentação legível foi encontrada no extrato.')
            extraction = parsed.model_dump(mode='json')
            db.statement_extractions.update_one({'_id': digest}, {'$set': {'extraction': extraction, 'table_currency_version': 1}}, upsert=True)
    except PDFPasswordError as exc: raise HTTPException(428, str(exc)) from exc
    except XAIError as exc: raise HTTPException(503, public_xai_error(exc)) from exc
    except (ValueError, RuntimeError) as exc: raise HTTPException(422, str(exc)) from exc
    stored = store_media(db, data, digest, '')
    now = datetime.now(timezone.utc)
    batch = {'_id': identity, 'batch_id': identity, 'filename': (file.filename or 'extrato')[:240],
        'sha256': digest, 'storage_key': str(stored), 'created_at': now, 'status': 'draft',
        'rows': [{'row_id': index, **row, 'currency': row.get('currency') or extraction.get('currency')} for index, row in enumerate(extraction['rows'], 1)],
        'warnings': extraction['warnings'], 'complete': extraction['complete'], 'account_hint': extraction['account_hint']}
    try: db.capture_batches.insert_one(batch)
    except DuplicateKeyError:
        batch = db.capture_batches.find_one({'_id': identity})
        if batch['sha256'] != digest: raise HTTPException(409, 'O lote já usa outro arquivo.')
    return {'persisted': True, 'event_id': identity, 'draft_persisted': True, 'needs_review': batch['status'] == 'draft', 'batch': draft_view(batch)}


@router.post('/scan')
async def scan(request: Request, file: UploadFile = File(...), batch_id: UUID = Form(...), db=Depends(database), pdf_cpf: str | None = Form(None)):
    return await run_upload(request, scan_sync, file, batch_id, request, pdf_cpf)


@router.post('/file-status')
def file_status(file: UploadFile = File(...), event_id: UUID = Form(...), db=Depends(database)):
    """Check original bytes against a committed review without extracting or writing.

    A draft or discarded batch is not an import acknowledgement. The echoed local
    event id lets the client retire only the queue item whose upload it checked.
    """
    digest = hashlib.sha256(read_batch_upload(file)).hexdigest()
    acknowledgement = {'event_id': str(event_id), 'sha256': digest, 'imported': False}
    batches = db.capture_batches.find({'sha256': digest, 'status': 'committed', 'result.persisted': True},
        {'batch_id': 1, 'result.persisted': 1, 'result.batch_id': 1})
    for batch in batches:
        result = batch.get('result')
        identity = batch.get('batch_id')
        if not isinstance(result, dict) or result.get('persisted') is not True: continue
        try: UUID(identity)
        except (ValueError, TypeError, AttributeError): continue
        if batch.get('_id') != identity or result.get('batch_id') != identity: continue
        return {**acknowledgement, 'imported': True, 'batch_id': identity}
    return acknowledgement


@router.get('')
def drafts(limit:int=Query(50,ge=1,le=200),cursor:str|None=None,db=Depends(database)):
    with mongo_snapshot(db) as (reading,consistency):
        rows,info=page(reading.capture_batches,{'status':'draft'},'created_at',limit,cursor)
        return _draft_page(rows,info,consistency)

def _draft_page(rows,info,consistency):
    return {'items': [{'batch_id': row['batch_id'], 'filename': row['filename'], 'sha256': row['sha256'], 'row_count': len(row['rows']),
                      'created_at': row['created_at'].isoformat()} for row in rows],**info,'read_consistency':consistency}


@router.get('/{batch_id}')
def get_batch(batch_id: UUID, db=Depends(database)):
    batch = db.capture_batches.find_one({'_id': str(batch_id)})
    if not batch: raise HTTPException(404, 'Lote não encontrado.')
    return {**draft_view(checked_table_currency(batch)), 'result': batch.get('result')}


def normalize(text):
    value = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9]+', ' ', value).strip()


def informative_words(description):
    noise = {'PIX', 'PAGAMENTO', 'PAG', 'PGTO', 'COMPRA', 'DEBITO', 'CREDITO', 'CARTAO',
             'TRANSFERENCIA', 'TRANSF', 'TED', 'DOC', 'ENVIADO', 'RECEBIDO', 'DES', 'REM', 'BENEF', 'DE', 'DA', 'DO'}
    return {word for word in description.split() if word not in noise and len(word) > 1 and not word.isdigit()}


def similar_description(left, right):
    a, b = informative_words(left), informative_words(right)
    # Bank boilerplate alone never supports a probable match.
    if not a or not b: return False
    if a & b and SequenceMatcher(None, left, right).ratio() >= .75: return True
    return min(len(a), len(b)) >= 2 and len(a & b) / min(len(a), len(b)) >= .8


def account_context(db, reference):
    kind, _, identity = reference.partition(':')
    if kind == 'profile':
        validate_account_key(identity,db)
        legacy = legacy_accounts_by_profile(db).get(identity)
        accounts = [legacy] if legacy else []
        return {'key': reference, 'profile': identity, 'ids': [row['_id'] for row in accounts], 'account_id': None}
    if kind == 'ledger' and ObjectId.is_valid(identity):
        account = db.accounts.find_one({'_id': ObjectId(identity)})
        if account and account.get('tipo') in {'conta_corrente', 'cartao_credito'}:
            profile = profile_for_legacy(db,account)
            return {'key': f'profile:{profile}' if profile else reference, 'profile': profile,
                    'ids': [account['_id']], 'account_id': account['_id']}
    raise HTTPException(422, 'Escolha a conta ou cartão deste extrato.')


def candidate_account(document, context, captured):
    if captured and (document.get('tipo') == 'transferencia' or document.get('natureza') == 'TRANSFERENCIA'):
        transfer = document.get('transfer') or {}
        endpoints = {transfer.get('from_account_key'), transfer.get('to_account_key')} - {None, ''}
        if endpoints and context['profile']:
            return 'same' if context['profile'] in endpoints else 'different'
        return 'unknown'
    if captured and document.get('account_profile_id'):
        return 'same' if document['account_profile_id'] == context['profile'] else 'different'
    if document.get('account_id'):
        return 'same' if document['account_id'] in context['ids'] else 'different'
    return 'unknown'


def validate_rows(batch, body):
    ids = [row.row_id for row in body.rows]
    if len(set(ids)) != len(ids) or set(ids) != {row['row_id'] for row in batch['rows']}:
        raise HTTPException(422, 'O lote deve conter exatamente suas linhas originais, sem repetir identificadores.')
    days = [row.date for row in body.rows if row.date]
    if days and (max(days) - min(days)).days > 366: raise HTTPException(422, 'Separe o extrato em períodos de até um ano.')
    source_rows = {row['row_id']: row for row in batch['rows']}
    for row in body.rows:
        source = source_rows[row.row_id]
        # Review can acknowledge an unknown currency, never erase source evidence.
        if 'currency' in source and row.currency != normalize_extracted_currency(source['currency']):
            raise HTTPException(422, 'A moeda da fonte deve permanecer intacta. Confirme BRL apenas quando a moeda não está determinada.')
        source_reference = str(source.get('bank_ref') or '').strip() or None
        if row.bank_ref != source_reference:
            raise HTTPException(422, 'O identificador bancário original deve permanecer intacto durante a revisão.')


def fingerprint(batch, body):
    payload = {'sha256': batch['sha256'], 'review': BatchReview.model_validate(body.model_dump()).model_dump(mode='json')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def row_errors(row, acknowledge_brl=False):
    errors = []
    currency = normalize_extracted_currency(row.currency)
    if currency not in (None, '', 'BRL'):
        errors.append(f'Moeda {currency} na fonte. Este app não converte valores estrangeiros para BRL.')
    elif currency != 'BRL' and not acknowledge_brl:
        errors.append('A moeda não está determinada. Confira o arquivo e confirme explicitamente que os valores são em BRL.')
    if not row.date: errors.append('Informe a data completa, incluindo o ano.')
    if row.amount is None or not row.amount.is_finite() or row.amount == 0 or abs(row.amount) > Decimal('100000000'):
        errors.append('Informe um valor válido com sinal.')
    if len(row.description.strip()) < 3: errors.append('Confira a descrição.')
    if row.kind in {'unknown', 'own_transfer'}: errors.append('Confira o tipo. Transferências entre suas contas devem ser registradas em Digitar, com origem e destino.')
    elif row.amount is not None and row.amount.is_finite() and (row.amount < 0) != (row.kind in {'expense', 'bill_payment'}):
        errors.append('O sinal do valor não corresponde ao tipo.')
    return errors


def analyze(db, batch, body):
    batch = checked_table_currency(batch)
    validate_rows(batch, body)
    context = account_context(db, body.account_ref)
    valid = [row for row in body.rows if not row_errors(row, body.acknowledge_brl)]
    candidates = []; limited = False
    if valid:
        window = {'$gte': day_start(min(row.date for row in valid) - timedelta(days=1)),
                  '$lt': day_start(max(row.date for row in valid) + timedelta(days=2))}
        amounts = [Decimal128(value) for value in {str(row.amount.quantize(Decimal('.01'))) for row in valid}]
        references = [row.bank_ref for row in valid if row.bank_ref]
        def scope(prefix=''):
            ordinary={prefix+'data_evento': window, prefix+'valor': {'$in': amounts}}
            return {'$or': [ordinary, {prefix+'bank_transaction_ref': {'$in': references}}]} if references else ordinary
        # Bound every candidate scan; incomplete scans prevent insertion, never imply no duplicate.
        for source, collection, query in (
            ('ledger', db.transactions, scope()),
            ('capture', db.provisional_transactions, {**scope(), 'status': {'$in': ['provisional', 'enriched', 'confirmed']}}),
            ('deleted', db.deleted_movements, scope('document.')),
        ):
            found = list(collection.find(query).limit(1001))
            if len(found) > 1000: limited = True
            for doc in found[:1000]:
                original = doc['document'] if source == 'deleted' else doc
                captured = bool(original.get('provisional_id'))
                relation = candidate_account(original, context, captured)
                if relation == 'different': continue
                display_descriptions = [str(original.get(key) or '').strip() for key in ('merchant_raw', 'merchant_name', 'estabelecimento_original', 'descricao_original', 'vendedor')]
                descriptions = {normalize(value) for value in display_descriptions}
                candidates.append({'source': source, 'identity': str(original.get('provisional_id') or original['_id']),
                    'date': local_date(original['data_evento']), 'amount': original['valor'].to_decimal() if hasattr(original['valor'], 'to_decimal') else Decimal(str(original['valor'])),
                    'descriptions': descriptions - {''}, 'description': next((value for value in display_descriptions if value), 'Sem descrição'),
                    'account': relation, 'bank_ref': original.get('bank_transaction_ref')})
    results = []; seen = {}; seen_refs = {}
    for row in sorted(body.rows, key=lambda row: row.row_id):
        errors = row_errors(row, body.acknowledge_brl); status = 'invalid' if errors else 'new'; reason = ' '.join(errors); matches = []
        if not batch['complete'] and not body.acknowledge_partial:
            errors.append('Confirme que está importando apenas as linhas visíveis deste arquivo parcial.')
            status = 'blocked'; reason = errors[-1]
        if not errors and limited: status = 'blocked'; reason = 'Há coincidências demais para conferir com segurança. Separe o período.'
        if not errors and not limited:
            desc = normalize(row.description)
            key = (row.date, str(row.amount.quantize(Decimal('.01'))), desc)
            for candidate in candidates:
                if row.bank_ref and candidate['bank_ref'] and row.bank_ref != candidate['bank_ref'] and candidate['account'] == 'same':
                    continue
                bank_same = bool(row.bank_ref and row.bank_ref == candidate['bank_ref'] and candidate['account'] == 'same')
                if not bank_same and (candidate['amount'] != row.amount or abs((candidate['date'] - row.date).days) > 1): continue
                same_day = candidate['date'] == row.date
                same_description = desc in candidate['descriptions'] and len(desc) >= 5 and bool(informative_words(desc))
                similar = any(similar_description(desc, existing) for existing in candidate['descriptions'])
                if bank_same:
                    confidence = 'confirmed'
                    matching_reason = 'Mesmo identificador bancário na mesma conta.'
                    if candidate['source'] == 'deleted': matching_reason = 'Este registro já foi excluído anteriormente; a importação não o recria.'
                elif same_day or similar:
                    confidence = 'high' if similar and candidate['account'] == 'same' else 'possible'
                    matching_reason = ('Mesma conta e valor, com descrição semelhante' + (' na mesma data.' if same_day else ' e data com diferença de até um dia.')) if confidence == 'high' else 'Mesmo valor e data próxima; confira a descrição e a conta.'
                    if candidate['account'] != 'same': matching_reason += ' A conta do registro existente não está identificada.'
                else: continue
                if candidate['source'] == 'deleted' and not bank_same:
                    matching_reason += ' Há um registro semelhante excluído; confirme somente se esta é outra movimentação.'
                matches.append({'source': candidate['source'], 'identity': candidate['identity'],
                    'date': candidate['date'].isoformat(), 'amount': str(candidate['amount']),
                    'description': candidate['description'], 'account_known': candidate['account'] == 'same',
                    'confidence': confidence, 'reason': matching_reason})
            matches.sort(key=lambda match: {'confirmed': 0, 'high': 1, 'possible': 2}[match['confidence']])
            if matches:
                status = 'duplicate' if matches[0]['confidence'] == 'confirmed' else 'possible_duplicate'
                reason = matches[0]['reason']
            prior = seen_refs.get(row.bank_ref) if row.bank_ref else None
            same_row = seen.get(key)
            distinct_refs = same_row and row.bank_ref and same_row.bank_ref and row.bank_ref != same_row.bank_ref
            if prior:
                status = 'duplicate'; reason = f'Mesmo identificador bancário dentro deste lote, na linha {prior}.'
            elif same_row and not distinct_refs and status != 'duplicate':
                status = 'possible_duplicate'; reason = f'Mesma data, valor e descrição da linha {same_row.row_id}, sem identidade bancária que prove duplicidade. Confira se são movimentos distintos.'
            eligible = status == 'new' or (status == 'possible_duplicate' and row.confirmed_distinct)
            if row.selected and eligible:
                seen[key] = row
                if row.bank_ref: seen_refs[row.bank_ref] = row.row_id
        results.append({'row_id': row.row_id, 'status': status, 'reason': reason, 'matches': matches[:3],
            'confidence': 'confirmed' if status == 'duplicate' else matches[0]['confidence'] if matches else None,
            'selected': row.selected,
            'will_insert': row.selected and (status == 'new' or (status == 'possible_duplicate' and row.confirmed_distinct)),
            'confirmed_distinct': row.confirmed_distinct})
    return {'rows': results, 'insert_count': sum(row['will_insert'] for row in results),
            'duplicate_count': sum(row['status'] == 'duplicate' for row in results),
            'possible_duplicate_count': sum(row['status'] == 'possible_duplicate' for row in results),
            'new_count': sum(row['status'] == 'new' for row in results),
            'scan_complete': not limited, 'source_complete': batch['complete']}, context


def load_draft(db, batch_id):
    batch = db.capture_batches.find_one({'_id': str(batch_id)})
    if not batch: raise HTTPException(404, 'Lote não encontrado.')
    return checked_table_currency(batch)


@router.delete('/{batch_id}')
def discard_batch(batch_id: UUID, body: BatchDiscard, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        lock_movement_insert(writing)
        batch = load_draft(writing, batch_id)
        if batch['sha256'] != body.expected_sha256:
            raise HTTPException(409, 'O arquivo deste lote mudou. Atualize os lotes antes de remover.')
        if batch['status'] == 'committed':
            raise HTTPException(409, 'Este lote já foi importado. A remoção do lote não apaga movimentações.')
        if batch['status'] == 'discarded':
            return {'persisted': True, 'batch_id': str(batch_id), 'status': 'discarded', 'duplicate_request': True}
        if batch['status'] != 'draft':
            raise HTTPException(409, 'Este lote não está disponível para remoção.')
        now = datetime.now(timezone.utc)
        updated = writing.capture_batches.update_one({'_id': str(batch_id), 'status': 'draft', 'sha256': body.expected_sha256},
            {'$set': {'status': 'discarded', 'discarded_at': now}})
        if updated is not None and updated.matched_count != 1:
            raise HTTPException(409, 'O lote mudou. Atualize os lotes antes de remover.')
        writing.audit_log.insert_one({'event': 'statement_batch_discarded', 'batch_id': str(batch_id),
            'sha256': batch['sha256'], 'created_at': now, 'source': 'mobile_manual_review'})
        return {'persisted': True, 'batch_id': str(batch_id), 'status': 'discarded', 'duplicate_request': False}


@router.post('/{batch_id}/preview')
def preview(batch_id: UUID, body: BatchReview, db=Depends(database)):
    with mongo_snapshot(db) as (reading, consistency):
        batch = load_draft(reading, batch_id)
        if batch['status'] != 'draft': raise HTTPException(409, 'Este lote já foi concluído.')
        report, _ = analyze(reading, batch, body)
        return {**report, 'preview_token': fingerprint(batch, body), 'read_consistency': consistency, 'persisted': False}


@router.post('/{batch_id}/commit')
def commit(batch_id: UUID, body: BatchCommit, db=Depends(database)):
    # The global fence is also taken by notification/manual ingestion. A conflicting
    # writer gets 409 and retries; the next transaction scans the newly committed rows.
    try:
        with mongo_transaction(db) as session:
            writing = session_database(db, session)
            lock_movement_insert(writing)
            batch = load_draft(writing, batch_id)
            review = BatchReview.model_validate(body.model_dump(exclude={'preview_token'}))
            if batch['status'] == 'committed':
                if batch['commit_token'] != body.preview_token: raise HTTPException(409, 'O lote já teve outra confirmação.')
                original_review = BatchReview.model_validate(batch.get('review') or {})
                if original_review.model_dump(mode='json') != review.model_dump(mode='json'):
                    raise HTTPException(409, 'Esta confirmação pertence a outra revisão. Preserve a revisão original.')
                return {**batch['result'], 'duplicate_request': True}
            if batch['status'] != 'draft':
                raise HTTPException(409, 'Este lote foi removido e não pode ser importado. Capture o arquivo novamente se necessário.')
            expected = fingerprint(batch, review)
            if expected != body.preview_token: raise HTTPException(409, 'O lote mudou. Confira as duplicidades novamente.')
            report, context = analyze(writing, batch, review)
            if not report['scan_complete']: raise HTTPException(409, 'Separe o período para conferir todas as coincidências.')
            if any(outcome['selected'] and outcome['status'] in {'invalid', 'blocked'} for outcome in report['rows']):
                raise HTTPException(422, 'Corrija ou desmarque as linhas inválidas antes de confirmar. Nenhuma movimentação foi inserida.')
            if any(outcome['selected'] and outcome['status'] == 'possible_duplicate' and not outcome['confirmed_distinct'] for outcome in report['rows']):
                raise HTTPException(409, 'Confira cada coincidência: confirme que é outra movimentação ou desmarque a linha. Nenhuma movimentação foi inserida.')
            now = datetime.now(timezone.utc); inserted = []
            rows = {row.row_id: row for row in review.rows}
            overrides = taxonomy_overrides(writing)
            for outcome in report['rows']:
                if not outcome['will_insert']: continue
                row = rows[outcome['row_id']]
                event_id = str(uuid5(NAMESPACE_URL, f'capture-batch:{batch_id}:row:{row.row_id}'))
                provisional_id = str(uuid5(NAMESPACE_URL, f'provisional:{event_id}'))
                stamp = datetime.combine(row.date, time(12), LOCAL_ZONE).astimezone(timezone.utc)
                kinds = {'expense': ('compra', 'COMPRA'), 'income': ('receita', 'RENDIMENTO'),
                         'refund': ('estorno', 'ESTORNO'), 'bill_payment': ('pagamento_fatura', None)}
                tipo, natureza = kinds[row.kind]
                document = {'provisional_id': provisional_id, 'event_id': event_id, 'status': 'confirmed', 'revision': 1,
                    'status_revisao': 'validado', 'revisado_em': now,
                    'data_evento': stamp, 'valor': Decimal128(str(row.amount)), 'fluxo': 'saida' if row.amount < 0 else 'entrada',
                    'moeda': 'BRL', 'merchant_raw': row.description.strip(), 'tipo': tipo, 'natureza': natureza,
                    'account_profile_id': context['profile'], 'account_id': context['account_id'],
                    'bank_transaction_ref': row.bank_ref, 'capture_batch_id': str(batch_id), 'capture_batch_row': row.row_id,
                    'confirmed_distinct': row.confirmed_distinct, 'source_currency': row.currency,
                    'currency_acknowledged_brl': review.acknowledge_brl and row.currency != 'BRL',
                    'created_at': now, 'confirmed_at': now, 'date_precision': 'day'}
                suggestion = infer_classification(document, overrides)
                if suggestion.get('categoria'): document['categoria_sugerida'] = suggestion['categoria']
                if suggestion.get('subcategoria'): document['subcategoria'] = suggestion['subcategoria']
                if row.kind == 'expense' and suggestion.get('natureza') in {'IMPOSTO_TRIBUTO', 'TARIFA_ENCARGO'}: document['natureza'] = suggestion['natureza']
                inserted_id = writing.provisional_transactions.insert_one(document).inserted_id
                writing.raw_events.insert_one({'event_id': event_id, 'source_type': 'manual', 'source_provider': 'statement_batch_review',
                    'captured_at': now, 'received_at': now, 'processing_status': 'confirmed', 'linked_transaction_id': inserted_id,
                    'raw_payload': {'batch_id': str(batch_id), 'row_id': row.row_id, 'sha256': batch['sha256'],
                                    'storage_key': batch['storage_key'], 'reviewed_row': row.model_dump(mode='json')},
                    'parsed_payload': {'amount': str(abs(row.amount)), 'currency': 'BRL', 'merchant_raw': row.description,
                                       'event_timestamp': stamp.isoformat(), 'direction': 'expense' if row.amount < 0 else 'income'},
                    'parser_version': 'statement-batch-reviewed-v1', 'media_storage_key': batch['storage_key'], 'media_status': 'media_ready'})
                inserted.append({'row_id': row.row_id, 'provisional_id': provisional_id})
            result = {'batch_id': str(batch_id), 'persisted': True, 'inserted_count': len(inserted), 'inserted': inserted,
                'rows': report['rows'], 'skipped_count': len(review.rows) - len(inserted), 'duplicate_request': False}
            writing.capture_batches.update_one({'_id': str(batch_id)}, {'$set': {'status': 'committed', 'commit_token': body.preview_token,
                'review': review.model_dump(mode='json'), 'result': result, 'committed_at': now}})
            writing.audit_log.insert_one({'event': 'statement_batch_committed', 'batch_id': str(batch_id), 'created_at': now,
                'account_ref': review.account_ref, 'inserted': inserted, 'rows': report['rows'], 'source': 'mobile_manual_review'})
            return result
    except DuplicateKeyError as exc:
        raise HTTPException(409, 'Outra inserção foi concluída ao mesmo tempo. Confira e tente novamente.') from exc
