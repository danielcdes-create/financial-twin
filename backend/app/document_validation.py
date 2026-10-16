from decimal import Decimal
import re
import unicodedata
from app.schemas_document import DocumentExtraction

def validate_document_extraction(document:DocumentExtraction):
    kind=re.sub(r'[^a-z0-9]+','_',unicodedata.normalize('NFKD',document.document_type).encode('ascii','ignore').decode().casefold()).strip('_')
    if kind in {'statement','bank_statement','card_statement','credit_card_statement','account_statement',
                'extrato','extrato_bancario','extrato_de_conta','extrato_de_conta_corrente',
                'fatura','fatura_cartao','fatura_de_cartao','fatura_de_cartao_de_credito'}:
        raise ValueError('Este documento é uma fatura ou extrato. Em Capturar → Foto / arquivo, escolha Fatura / extrato e confira cada linha antes de importar.')
    if not document.currency: raise ValueError('Não foi possível identificar a moeda deste comprovante. Confira se o documento informa valores em reais (BRL).')
    if document.currency!="BRL": raise ValueError('Este comprovante informa outra moeda. Apenas valores em reais (BRL) podem ser importados; não há conversão automática.')
    if document.total is None: raise ValueError("document total is required")
    total=Decimal(str(document.total)).quantize(Decimal("0.01"))
    if total<=0 or total>Decimal("100000000"): raise ValueError("document total is outside the supported range")
    items_total=sum((Decimal(str(item.amount)).quantize(Decimal("0.01")) for item in document.items),start=Decimal("0.00"))
    return {"total":total,"items_total":items_total,"review_required":bool(document.items and abs(items_total-total)>Decimal("0.02")),"difference":items_total-total}
