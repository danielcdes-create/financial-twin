"""Shared query contracts for statements, review and dashboard drilldowns."""
import hashlib
import json
import re
from datetime import timedelta
from fastapi import HTTPException
from app.category_filter import UNCATEGORIZED, category_query
from app.financial_time import day_start


def period_query(start, end, basis='cash'):
    if start and end and start > end:
        raise HTTPException(422, 'A data inicial deve ser anterior à data final.')
    if not start and not end:
        return {}
    window = {}
    if start: window['$gte'] = day_start(start)
    if end: window['$lt'] = day_start(end + timedelta(days=1))
    if basis == 'cash': return {'data_evento': window}
    competence = {'$regex': r'^\d{4}-(0[1-9]|1[0-2])$'}
    if start: competence['$gte'] = start.strftime('%Y-%m')
    if end: competence['$lte'] = end.strftime('%Y-%m')
    return {'$or': [{'competencia': competence},
                    {'competencia': {'$in': [None, '']}, 'data_evento': window}]}


def intersect(query, condition):
    return {'$and': [query, condition]} if condition else query


def search_query(db, collection, category_field, search):
    term = (search or '').strip()
    if not term: return {}
    # An apostrophe splits brand names into tokens (McDonald + s). Mongo's
    # default OR search lets the isolated suffix match unrelated merchants.
    # Keep the brand's token sequence together; relational names still match.
    text_term = '"' + term.replace('"', '\\"') + '"' if re.search("['’]", term) else term
    text = {'$text': {'$search': text_term}}
    named = [row['_id'] for row in db.merchants.find(
        {'nome_padrao': {'$regex': re.escape(term), '$options': 'i'}}, {'_id': 1})]
    aliases = []
    if named: aliases.extend([{'merchant_id': {'$in': named}}, {'platform_id': {'$in': named}}])
    if term.casefold() == UNCATEGORIZED.casefold(): aliases.append(category_query(category_field, UNCATEGORIZED))
    if not aliases: return text
    # Separate indexed text search from relational/display-label matches;
    # MongoDB requires every branch of an OR containing $text to be indexed.
    ids = {row['_id'] for row in collection.find(text, {'_id': 1})}
    ids.update(row['_id'] for row in collection.find({'$or': aliases}, {'_id': 1}))
    return {'_id': {'$in': list(ids)}}


def filter_key(kind, search, category, account, start, end, status=None, basis='cash', spending=False):
    values = [kind, (search or '').strip(), (category or '').strip(), (account or '').strip(),
              str(start) if start else None, str(end) if end else None, status, basis, spending]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()
