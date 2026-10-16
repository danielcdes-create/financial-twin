"""Optimistic tokens without changing the strict historical ledger schema."""
import hashlib
from bson import json_util
from fastapi import HTTPException


def source_token(document):
    return hashlib.sha256(json_util.dumps(document, sort_keys=True).encode()).hexdigest()


def require_source_token(document, expected, message='A fonte mudou. Recarregue e confira os dados antes de salvar.'):
    if expected is None:
        raise HTTPException(428, 'Reabra os dados para obter a versão atual.')
    if expected != source_token(document):
        raise HTTPException(409, message)
