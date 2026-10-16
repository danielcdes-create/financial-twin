"""Read up to ten PDF pages in memory; the supplied CPF is never persisted."""
import re
from dataclasses import dataclass

MAX_PDF_PAGES = 10
MAX_PDF_TEXT = 100000
MAX_RENDER_PIXELS = 25000000
PDF_DPI = 130


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
        if pdf.is_encrypted:
            password = cpf_digits(cpf)
            if not password:
                raise PDFPasswordError('PDF protegido: informe seu CPF em Capturar → Foto / arquivo. O arquivo continua na fila.')
            if not pdf.authenticate(password):
                raise PDFPasswordError('O CPF informado não abriu este PDF. Confira o CPF ou envie uma cópia sem senha. O arquivo continua na fila.')
        if not 1 <= len(pdf) <= MAX_PDF_PAGES:
            raise ValueError(f'Separe o documento em PDFs de até {MAX_PDF_PAGES} páginas.')
        pages = [page.get_text() for page in pdf]
        text = '\n'.join(pages)
        if len(text) > MAX_PDF_TEXT:
            raise ValueError('O PDF contém texto demais. Separe o período.')
        # A mixed PDF must retain its scanned pages, too.
        if all(len(page.strip()) >= 40 for page in pages):
            return PDFInput(text=text, images=[])
        pixels = sum(page.rect.width * page.rect.height * (PDF_DPI / 72) ** 2 for page in pdf)
        if pixels > MAX_RENDER_PIXELS:
            raise ValueError('Separe o PDF em arquivos menores, de até 25 milhões de pixels renderizados.')
        return PDFInput(text=None, images=[(page.get_pixmap(dpi=PDF_DPI, alpha=False).tobytes('png'), 'image/png') for page in pdf])
    finally: pdf.close()
