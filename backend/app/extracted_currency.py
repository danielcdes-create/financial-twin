"""Canonicalize explicit BRL labels without inferring or converting a currency."""
import unicodedata


def normalize_extracted_currency(value):
    if not isinstance(value, str):
        return value
    unaccented = ''.join(character for character in unicodedata.normalize('NFKD', value)
                         if not unicodedata.combining(character))
    label = ' '.join(unaccented.upper().split())
    if label in {'BRL', 'R$', 'REAL', 'REAIS', 'REAL BRASILEIRO', 'REAIS BRASILEIROS',
                 'BRAZILIAN REAL', 'BRAZILIAN REAIS', 'R$ (BRL)', 'BRL (R$)'}:
        return 'BRL'
    return value.strip()
