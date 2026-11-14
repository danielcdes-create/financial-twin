import csv
import io
import itertools
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException
from defusedxml import ElementTree as DefusedET
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

MAX_IMPORT_ROWS_SCAN = 201
MAX_XLSX_ENTRIES = 128
MAX_XLSX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_XLSX_SINGLE_ENTRY_BYTES = 16 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100


def _validate_xlsx_archive(data: bytes) -> None:
    with ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_XLSX_ENTRIES:
            raise ValueError("Arquivo XLSX excede o limite estrutural permitido.")
        total_uncompressed = 0
        for info in infos:
            if info.file_size < 0 or info.file_size > MAX_XLSX_SINGLE_ENTRY_BYTES:
                raise ValueError("Arquivo XLSX excede o tamanho seguro descompactado.")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise ValueError("Arquivo XLSX excede o tamanho seguro descompactado.")
            if info.file_size > 1024 * 1024:
                ratio = info.file_size / max(1, info.compress_size)
                if ratio > MAX_XLSX_COMPRESSION_RATIO:
                    raise ValueError("Arquivo XLSX rejeitado por compressão anômala.")
            lower_name = info.filename.lower()
            if lower_name.endswith((".xml", ".rels")):
                with archive.open(info) as xml_stream:
                    for _event, elem in DefusedET.iterparse(
                        xml_stream,
                        events=("start",),
                        forbid_dtd=True,
                        forbid_entities=True,
                        forbid_external=True,
                    ):
                        elem.clear()


class ImportFinancialProvider:
    """Read statement rows without writing to MongoDB or guessing column meaning."""
    def read_rows(self, filename: str, data: bytes) -> list[dict[str, Any]]:
        ext = Path(filename).suffix.casefold()
        if ext == ".csv":
            try:
                text = data.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError("CSV must use UTF-8 encoding") from exc
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
            except csv.Error:
                dialect = csv.excel
            rows = csv.DictReader(io.StringIO(text), dialect=dialect)
            if not rows.fieldnames:
                raise ValueError("CSV header row is missing")
            return list(itertools.islice(rows, MAX_IMPORT_ROWS_SCAN))
        if ext == ".xlsx":
            book = None
            try:
                _validate_xlsx_archive(data)
                book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
                sheet = book.active
                if sheet is None:
                    raise ValueError("XLSX sem planilha legível.")
                iterator = sheet.iter_rows(values_only=True)
                headers = [str(x or "").strip() for x in next(iterator, ())]
                if not any(headers):
                    raise ValueError("XLSX header row is missing")
                return [dict(zip(headers, row)) for row in itertools.islice(iterator, MAX_IMPORT_ROWS_SCAN)]
            except (BadZipFile, InvalidFileException, ParseError, DefusedXmlException, KeyError, EOFError) as exc:
                raise ValueError("O arquivo XLSX está inválido ou corrompido. Escolha uma cópia válida.") from exc
            finally:
                if book is not None:
                    book.close()
        raise ValueError("Only CSV and XLSX imports are supported")
