"""Read up to ten PDF pages in memory; the supplied CPF is never persisted."""
import re
from dataclasses import dataclass

MAX_PDF_PAGES = 10
MAX_PDF_TEXT = 100000
MAX_RENDER_PIXELS = 25000000
MAX_SINGLE_PAGE_PIXELS = 8000000
MAX_TOTAL_PNG_BYTES = 24 * 1024 * 1024
PDF_DPI = 130

_CPF_FORMATTED_RE = re.compile(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b')
_CPF_LABELED_RE = re.compile(r'(?i)\b(cpf\s*[:#\-]?\s*)\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b')
_CNPJ_FORMATTED_RE = re.compile(r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b')
_ACCOUNT_HEADER_RE = re.compile(r'(?i)\b(ag[eê]ncia|conta(?:\s+corrente)?)\s*[:#\-]?\s*[\d.\-]{4,16}\b')


def redact_pdf_pii(text: str, cpf_raw_digits: str | None = None) -> str:
    if not text:
        return text
    cleaned = _CPF_LABELED_RE.sub(r'\1[CPF]', text)
    cleaned = _CPF_FORMATTED_RE.sub('[CPF]', cleaned)
    cleaned = _CNPJ_FORMATTED_RE.sub('[CNPJ]', cleaned)
    cleaned = _ACCOUNT_HEADER_RE.sub(r'\1: [CONTA]', cleaned)
    if cpf_raw_digits and len(cpf_raw_digits) == 11:
        formatted = f'{cpf_raw_digits[:3]}.{cpf_raw_digits[3:6]}.{cpf_raw_digits[6:9]}-{cpf_raw_digits[9:]}'
        cleaned = cleaned.replace(formatted, '[CPF]').replace(cpf_raw_digits, '[CPF]')
    return cleaned


class PDFPasswordError(ValueError):
    pass


@dataclass
class PDFInput:
    text: str | None
    images: list[tuple[bytes, str]]


def cpf_digits(value: str | None) -> str | None:
    if not value: return None
    digits = re.sub(r'[.\-\s]', '', value)
    if not re.fullmatch(r'[0-9]{11}', digits):
        raise PDFPasswordError('Informe o CPF completo, com 11 dígitos, em Foto / arquivo.')
    return digits


def read_pdf(data: bytes, cpf: str | None = None) -> PDFInput:
    import pymupdf as fitz
    try: pdf = fitz.open(stream=data, filetype='pdf')
    except (ValueError, RuntimeError) as exc:
        raise ValueError('O PDF não pôde ser lido. Escolha outro arquivo.') from exc
    try:
        password = None
        if pdf.is_encrypted:
            password = cpf_digits(cpf)
            if not password:
                raise PDFPasswordError('PDF protegido: informe seu CPF em Capturar → Foto / arquivo. O arquivo continua na fila.')
            if not pdf.authenticate(password):
                raise PDFPasswordError('O CPF informado não abriu este PDF. Confira o CPF ou envie uma cópia sem senha. O arquivo continua na fila.')
        if not 1 <= len(pdf) <= MAX_PDF_PAGES:
            raise ValueError(f'Separe o documento em PDFs de até {MAX_PDF_PAGES} páginas.')
        pages = [redact_pdf_pii(page.get_text(), password) for page in pdf]
        text = '\n'.join(pages)
        if len(text) > MAX_PDF_TEXT:
            raise ValueError('O PDF contém texto demais. Separe o período.')
        # A mixed PDF must retain its scanned pages, too.
        if all(len(page.strip()) >= 40 for page in pages):
            return PDFInput(text=text, images=[])
        page_pixels = [page.rect.width * page.rect.height * (PDF_DPI / 72) ** 2 for page in pdf]
        if sum(page_pixels) > MAX_RENDER_PIXELS or any(px > MAX_SINGLE_PAGE_PIXELS for px in page_pixels):
            raise ValueError('Separe o PDF em arquivos menores, de até 25 milhões de pixels renderizados.')
        images: list[tuple[bytes, str]] = []
        total_png_bytes = 0
        for page in pdf:
            pix = page.get_pixmap(dpi=PDF_DPI, alpha=False)
            try:
                png_bytes = pix.tobytes('png')
            finally:
                del pix
            total_png_bytes += len(png_bytes)
            if total_png_bytes > MAX_TOTAL_PNG_BYTES:
                raise ValueError('Separe o PDF escaneado em arquivos menores para caber no limite de memória.')
            images.append((png_bytes, 'image/png'))
        return PDFInput(text=None, images=images)
    finally: pdf.close()
