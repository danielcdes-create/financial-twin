"""Operational errors never include driver details, validation inputs or tracebacks."""
import re


def safe_error(exc):
    kind = re.sub(r'[^A-Za-z0-9_]', '', type(exc).__name__)[:80] or 'Error'
    code = getattr(exc, 'code', None)
    if isinstance(code, int):
        return f'{kind}:code={code}'
    if isinstance(code, str) and re.fullmatch(r'[a-zA-Z0-9_]{1,40}', code):
        return f'{kind}:code={code}'
    return kind
