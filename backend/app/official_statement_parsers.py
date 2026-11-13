"""Deterministic, self-verifying parsers for official Itaú and Santander PDFs.

Parses official Brazilian bank statements and credit card invoices directly from
local PDF files (unlocking CPF-protected PDFs in memory when needed), enriches
items with popular establishment names, and verifies exact cent-level closure
against each document's official totals/balances.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import pymupdf

from app.popular_merchants import classify_official_descriptor, extract_embedded_purchase_date

_PT_MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

_BRL_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}-?$")
_CPF_CANDIDATE_RE = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")


def parse_brl_decimal(raw: str) -> Decimal:
    text = raw.replace("R$", "").strip()
    trailing_neg = text.endswith("-")
    if trailing_neg:
        text = text[:-1].strip()
    cleaned = text.replace(".", "").replace(",", ".")
    val = Decimal(cleaned)
    return -val if trailing_neg else val


@dataclass
class OfficialStatementItem:
    doc_key: str
    row_index: int
    target: str
    date: str
    purchase_date_hint: str
    statement_month: str
    amount: str
    kind: str
    description: str
    popular_vendor: str
    platform: str | None
    categoria: str | None
    subcategoria: str | None
    natureza: str | None
    tipo: str
    principal_amount: str | None = None
    fee_amount: str | None = None
    bank_ref: str | None = None
    subcard_final: str | None = None
    installment_number: int | None = None
    installment_total: int | None = None
    prior_cycle_installment: bool = False
    technical_transit: bool = False
    own_transfer: bool = False
    skip_insert: bool = False
    bank_category_hint: str = ""
    brand_aliases: list[str] = field(default_factory=list)
    context_hints: list[str] = field(default_factory=list)

    @property
    def item_key(self) -> str:
        return f"{self.doc_key}:{self.row_index}"

    def to_dict(self) -> dict:
        return {**asdict(self), "item_key": self.item_key}


@dataclass
class OfficialStatementDocument:
    doc_key: str
    filename: str
    sha256: str
    kind: str  # "card_statement" | "bank_statement"
    institution: str
    account_profile_key: str | None = None
    card_final: str | None = None
    additional_finals: list[str] = field(default_factory=list)
    competencia: str | None = None
    due_date: str | None = None
    paid_date: str | None = None
    total_amount: str | None = None
    status: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    opening_balance: str | None = None
    closing_balance: str | None = None
    closing_as_of: str | None = None
    items: list[OfficialStatementItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["items"] = [it.to_dict() for it in self.items]
        return data


def _infer_statement_item_date(ddmm: str, ref_year: int, ref_month: int) -> tuple[date, bool]:
    day_num, month_num = map(int, ddmm.split("/"))
    # Purchase months > ref_month belong to the prior calendar year (e.g. Dec purchase on Jan bill)
    year = ref_year - 1 if month_num > ref_month else ref_year
    d_obj = date(year, month_num, day_num)
    # Prior cycle installment if more than ~38 days before ref_month's 10th
    prior_cycle = (ref_year * 12 + ref_month) - (year * 12 + month_num) >= 2
    return d_obj, prior_cycle


def _parse_installment_suffix(desc: str) -> tuple[int | None, int | None]:
    m = re.search(r"\b(\d{2})/(\d{2})\b$", desc.strip())
    if m:
        num, tot = int(m.group(1)), int(m.group(2))
        if 1 <= num <= tot <= 48:
            return num, tot
    return None, None


def _extract_in_memory_cpf_candidates(paths: Iterable[Path]) -> list[str]:
    """Read unencrypted PDFs in the same batch to find formatted CPF candidates in memory."""
    found: list[str] = []
    for path in paths:
        try:
            doc = pymupdf.open(str(path))
        except Exception:
            continue
        try:
            if doc.is_encrypted:
                continue
            for page in doc:
                for match in _CPF_CANDIDATE_RE.finditer(page.get_text()):
                    digits = re.sub(r"\D", "", match.group(1))
                    if len(digits) == 11 and digits not in found:
                        found.append(digits)
        finally:
            doc.close()
    return found


def _open_pdf_lines(path: Path, passwords: Iterable[str] = ()) -> tuple[str, list[list[str]]]:
    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    doc = pymupdf.open(stream=raw_bytes, filetype="pdf")
    try:
        if doc.is_encrypted:
            unlocked = False
            for pw in passwords:
                if not pw:
                    continue
                for candidate in (pw.strip(), re.sub(r"\D", "", pw), re.sub(r"\D", "", pw)[:6]):
                    if candidate and doc.authenticate(candidate):
                        unlocked = True
                        break
                if unlocked:
                    break
            if not unlocked:
                raise ValueError(
                    f"O PDF '{path.name}' está protegido por senha. Passe --cpf <CPF_11_DIGITOS> "
                    "ou inclua o extrato desbloqueado no mesmo diretório."
                )
        pages = [[line.strip() for line in page.get_text().splitlines() if line.strip()] for page in doc]
        return digest, pages
    finally:
        doc.close()


def _parse_itau_card_invoice(path: Path, digest: str, pages: list[list[str]]) -> OfficialStatementDocument:
    lines = [line for page in pages for line in page]
    joined = "\n".join(lines)

    card_m = re.search(r"Cartão\s+\d{4}\.[X*]{4}\.[X*]{4}\.(\d{4})", joined, re.IGNORECASE)
    if not card_m:
        raise ValueError(f"Não foi possível identificar o final do cartão Itaú em {path.name}.")
    card_final = card_m.group(1)

    due_m = re.search(r"Vencimento:\s*(\d{2})/(\d{2})/(\d{4})", joined)
    if not due_m:
        idx_v = lines.index("Com vencimento em:")
        d_s, m_s, y_s = lines[idx_v + 1].split("/")
    else:
        d_s, m_s, y_s = due_m.group(1), due_m.group(2), due_m.group(3)
    due_dt = date(int(y_s), int(m_s), int(d_s))
    competencia = f"{due_dt.year:04d}-{due_dt.month:02d}"
    doc_key = f"fatura_itau_{card_final}_{due_dt.year:04d}{due_dt.month:02d}"
    target = f"card:itau:{card_final}"

    idx_total = lines.index("Total desta fatura")
    total_amount = parse_brl_decimal(lines[idx_total + 1])

    holder_name = ""
    for i, line in enumerate(lines):
        if line.startswith("Titular "):
            holder_name = line.removeprefix("Titular ").strip()
            break

    items: list[OfficialStatementItem] = []
    row_idx = 0

    # Prior bill payment credit
    for i, line in enumerate(lines):
        m_pay = re.match(r"^Pagamento efetuado em\s+(\d{2})/(\d{2})/(\d{4})$", line)
        if m_pay and i + 1 < len(lines) and _BRL_RE.match(lines[i + 1]):
            pay_dt = date(int(m_pay.group(3)), int(m_pay.group(2)), int(m_pay.group(1)))
            pay_val = parse_brl_decimal(lines[i + 1])
            items.append(
                OfficialStatementItem(
                    doc_key=doc_key,
                    row_index=0,
                    target=target,
                    date=pay_dt.isoformat(),
                    purchase_date_hint=pay_dt.isoformat(),
                    statement_month=competencia,
                    amount=f"{pay_val:.2f}",
                    kind="bill_payment_credit",
                    description="Pagamento efetuado (fatura anterior)",
                    popular_vendor="Pagamento Fatura Anterior Itaú",
                    platform=None,
                    categoria=None,
                    subcategoria=None,
                    natureza=None,
                    tipo="pagamento_fatura",
                    skip_insert=True,
                )
            )
            break

    # 1. National purchases & refunds
    if "Total dos pagamentos" in lines and "Lançamentos no cartão" in lines:
        start = lines.index("Total dos pagamentos") + 2
        end = lines.index("Lançamentos no cartão")
        block = lines[start:end]
        skip_exact = {
            holder_name,
            "Lançamentos: compras e saques",
            "DATA",
            "ESTABELECIMENTO",
            "VALOR EM R$",
            "continua...",
            "1",
            "2",
            "3",
            "4",
            "L",
            "S",
            "=",
        }
        skip_prefixes = (
            "Caso você",
            "pagamento",
            "O pagamento",
            "a parcelamentos",
            "Previsão para",
            "Cartão ",
        )
        idx = 0
        while idx < len(block):
            cur = block[idx]
            if cur in skip_exact or cur.startswith(skip_prefixes) or not re.match(r"^\d{2}/\d{2}$", cur):
                idx += 1
                continue
            dt_ddmm = block[idx]
            est = block[idx + 1]
            val_raw = block[idx + 2]
            if not _BRL_RE.match(val_raw):
                idx += 1
                continue
            val = parse_brl_decimal(val_raw)
            cat_hint = ""
            if (
                idx + 3 < len(block)
                and not re.match(r"^\d{2}/\d{2}$", block[idx + 3])
                and block[idx + 3] not in skip_exact
                and not block[idx + 3].startswith(skip_prefixes)
            ):
                cat_hint = block[idx + 3]
                idx += 4
            else:
                idx += 3

            row_idx += 1
            item_dt, prior_cycle = _infer_statement_item_date(dt_ddmm, due_dt.year, due_dt.month)
            inst_num, inst_tot = _parse_installment_suffix(est)
            info = classify_official_descriptor(est, cat_hint)
            signed_amt = -val
            is_refund = signed_amt > 0
            items.append(
                OfficialStatementItem(
                    doc_key=doc_key,
                    row_index=row_idx,
                    target=target,
                    date=item_dt.isoformat(),
                    purchase_date_hint=item_dt.isoformat(),
                    statement_month=competencia,
                    amount=f"{signed_amt:.2f}",
                    kind="refund" if is_refund else "expense",
                    description=est,
                    popular_vendor=info.popular_vendor,
                    platform=info.platform,
                    categoria=info.categoria,
                    subcategoria=info.subcategoria,
                    natureza="ESTORNO" if is_refund else "COMPRA",
                    tipo="estorno" if is_refund else "compra",
                    installment_number=inst_num,
                    installment_total=inst_tot,
                    prior_cycle_installment=prior_cycle and bool(inst_tot),
                    bank_category_hint=cat_hint,
                    brand_aliases=list(info.brand_aliases),
                    context_hints=list(info.context_hints),
                )
            )

    # 2. International purchases & IOF
    if "Lançamentos internacionais" in lines:
        i_start = lines.index("Lançamentos internacionais") + 1
        i_end = (
            lines.index("Lançamentos: produtos e serviços")
            if "Lançamentos: produtos e serviços" in lines
            else len(lines)
        )
        iblock = lines[i_start:i_end]
        last_intl_dt = due_dt
        idx = 0
        while idx < len(iblock):
            m_intl = re.match(r"^(\d{2}/\d{2})\s+(.+)$", iblock[idx])
            if m_intl and idx + 1 < len(iblock) and _BRL_RE.match(iblock[idx + 1]):
                dt_ddmm, est = m_intl.group(1), m_intl.group(2).strip()
                brl_val = parse_brl_decimal(iblock[idx + 1])
                last_intl_dt, _ = _infer_statement_item_date(dt_ddmm, due_dt.year, due_dt.month)
                info = classify_official_descriptor(est)
                row_idx += 1
                items.append(
                    OfficialStatementItem(
                        doc_key=doc_key,
                        row_index=row_idx,
                        target=target,
                        date=last_intl_dt.isoformat(),
                        purchase_date_hint=last_intl_dt.isoformat(),
                        statement_month=competencia,
                        amount=f"{-brl_val:.2f}",
                        kind="expense",
                        description=est,
                        popular_vendor=info.popular_vendor,
                        platform=info.platform,
                        categoria=info.categoria,
                        subcategoria=info.subcategoria,
                        natureza="COMPRA",
                        tipo="compra",
                        brand_aliases=list(info.brand_aliases),
                        context_hints=list(info.context_hints),
                    )
                )
                idx += 2
                continue
            if iblock[idx].startswith("Repasse de IOF") and idx + 1 < len(iblock) and _BRL_RE.match(iblock[idx + 1]):
                iof_val = parse_brl_decimal(iblock[idx + 1])
                if iof_val != 0:
                    row_idx += 1
                    info = classify_official_descriptor("IOF Compra Internacional")
                    items.append(
                        OfficialStatementItem(
                            doc_key=doc_key,
                            row_index=row_idx,
                            target=target,
                            date=last_intl_dt.isoformat(),
                            purchase_date_hint=last_intl_dt.isoformat(),
                            statement_month=competencia,
                            amount=f"{-iof_val:.2f}",
                            kind="expense",
                            description="IOF Compra Internacional",
                            popular_vendor=info.popular_vendor,
                            platform=None,
                            categoria=info.categoria,
                            subcategoria=info.subcategoria,
                            natureza="IMPOSTO_TRIBUTO",
                            tipo="compra",
                            brand_aliases=list(info.brand_aliases),
                            context_hints=list(info.context_hints),
                        )
                    )
                idx += 2
                continue
            idx += 1

    # 3. Products & services (Anuidade, Seguro, PgConta, Card PIX)
    if "Lançamentos: produtos e serviços" in lines and "Lançamentos produtos e serviços" in lines:
        s_start = lines.index("Lançamentos: produtos e serviços") + 1
        s_end = lines.index("Lançamentos produtos e serviços")
        sblock = lines[s_start:s_end]
        idx = 0
        while idx < len(sblock):
            if not re.match(r"^\d{2}/\d{2}$", sblock[idx]):
                idx += 1
                continue
            dt_ddmm = sblock[idx]
            est = sblock[idx + 1]
            val_raw = sblock[idx + 2]
            if not _BRL_RE.match(val_raw):
                idx += 1
                continue
            val = parse_brl_decimal(val_raw)
            idx += 3
            extra_lines: list[str] = []
            while idx < len(sblock) and not re.match(r"^\d{2}/\d{2}$", sblock[idx]):
                extra_lines.append(sblock[idx])
                idx += 1
            extra_joined = " ".join(extra_lines).strip()
            principal_str = None
            fee_str = None
            m_pf = re.search(
                r"Principal\s*\(R\$\s*([\d.,]+)\)\s*\+\s*Juros\s*\(R\$\s*([\d.,]+)\)",
                extra_joined,
                re.IGNORECASE,
            )
            if m_pf:
                p_val = parse_brl_decimal(m_pf.group(1))
                f_val = parse_brl_decimal(m_pf.group(2))
                principal_str = f"{-p_val:.2f}"
                fee_str = f"{-f_val:.2f}"
            elif extra_joined and "Principal" not in extra_joined:
                est = f"{est} / {extra_joined}"

            item_dt, _ = _infer_statement_item_date(dt_ddmm, due_dt.year, due_dt.month)
            info = classify_official_descriptor(est)
            signed_amt = -val
            is_refund = signed_amt > 0
            nat = (
                "ESTORNO"
                if is_refund
                else "IMPOSTO_TRIBUTO"
                if "IOF" in est.upper()
                else "TARIFA_ENCARGO"
                if info.categoria == "Finanças/Impostos"
                else "COMPRA"
            )
            row_idx += 1
            items.append(
                OfficialStatementItem(
                    doc_key=doc_key,
                    row_index=row_idx,
                    target=target,
                    date=item_dt.isoformat(),
                    purchase_date_hint=item_dt.isoformat(),
                    statement_month=competencia,
                    amount=f"{signed_amt:.2f}",
                    principal_amount=principal_str,
                    fee_amount=fee_str,
                    kind="refund" if is_refund else "expense",
                    description=est,
                    popular_vendor=info.popular_vendor,
                    platform=info.platform,
                    categoria=info.categoria,
                    subcategoria=info.subcategoria,
                    natureza=nat,
                    tipo="estorno" if is_refund else "compra",
                    brand_aliases=list(info.brand_aliases),
                    context_hints=list(info.context_hints),
                )
            )

    computed_net = sum(Decimal(it.amount) for it in items if not it.skip_insert)
    if computed_net != -total_amount:
        raise ValueError(
            f"Divergência matemática na fatura Itaú {path.name}: soma dos itens={-computed_net:.2f} != total={total_amount:.2f}"
        )

    return OfficialStatementDocument(
        doc_key=doc_key,
        filename=path.name,
        sha256=digest,
        kind="card_statement",
        institution="Itaú",
        card_final=card_final,
        competencia=competencia,
        due_date=due_dt.isoformat(),
        total_amount=f"{total_amount:.2f}",
        status="aberta",
        items=items,
    )


def _parse_itau_bank_statement(path: Path, digest: str, pages: list[list[str]]) -> OfficialStatementDocument:
    lines = [line for page in pages for line in page]
    joined = "\n".join(lines)

    per_m = re.search(
        r"período de visualização:\s*(\d{2}/\d{2}/\d{4})\s*até\s*(\d{2}/\d{2}/\d{4})",
        joined,
        re.IGNORECASE,
    )
    if not per_m:
        raise ValueError(f"Período do extrato Itaú não encontrado em {path.name}.")
    d1, m1, y1 = map(int, per_m.group(1).split("/"))
    d2, m2, y2 = map(int, per_m.group(2).split("/"))
    period_start = date(y1, m1, d1)
    period_end = date(y2, m2, d2)
    doc_key = f"extrato_itau_{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}"

    holder_first = ""
    for idx, line in enumerate(lines):
        if line.lower().startswith("agência:") and idx >= 2:
            candidate_name = lines[idx - 2] if _CPF_CANDIDATE_RE.search(lines[idx - 1]) else lines[idx - 1]
            if re.match(r"^[A-ZÀ-Ú]{2,}(?:\s+[A-ZÀ-Ú]+)+$", candidate_name):
                holder_first = candidate_name.split()[0].upper()
                break
        if line.lower() == "nome" and idx + 1 < len(lines):
            holder_first = lines[idx + 1].split()[0].upper()
            break
    if not holder_first:
        m_nome = re.search(r"Titular\s+([A-ZÀ-Ú]+)", joined)
        if m_nome:
            holder_first = m_nome.group(1).upper()

    start_idx = lines.index("saldo (R$)") + 1
    raw_entries: list[tuple[date, str, Decimal]] = []
    daily_balances: list[tuple[date, Decimal]] = []
    idx = start_idx
    while idx < len(lines):
        if lines[idx].startswith("Aviso!"):
            break
        m_inline = re.match(r"^(\d{2}/\d{2}/\d{4})\s+(.+)$", lines[idx])
        if m_inline and idx + 1 < len(lines) and _BRL_RE.match(lines[idx + 1]):
            dd, mm, yy = map(int, m_inline.group(1).split("/"))
            dt_obj = date(yy, mm, dd)
            desc = m_inline.group(2).strip()
            val = parse_brl_decimal(lines[idx + 1])
            if desc == "SALDO DO DIA":
                daily_balances.append((dt_obj, val))
            else:
                raw_entries.append((dt_obj, desc, val))
            idx += 2
            continue
        if re.match(r"^\d{2}/\d{2}/\d{4}$", lines[idx]) and idx + 2 < len(lines) and _BRL_RE.match(lines[idx + 2]):
            dd, mm, yy = map(int, lines[idx].split("/"))
            dt_obj = date(yy, mm, dd)
            desc = lines[idx + 1].strip()
            val = parse_brl_decimal(lines[idx + 2])
            if desc == "SALDO DO DIA":
                daily_balances.append((dt_obj, val))
            else:
                raw_entries.append((dt_obj, desc, val))
            idx += 3
            continue
        idx += 1

    if len(daily_balances) < 2:
        raise ValueError(f"Saldos diários insuficientes para conciliar o extrato Itaú {path.name}.")

    # Itaú prints newest-first; earliest SALDO DO DIA at the bottom is the prior closing balance
    closing_as_of_dt, closing_balance = daily_balances[0]
    _, opening_balance = daily_balances[-1]
    chronological = list(reversed(raw_entries))

    if opening_balance + sum(v for _, _, v in chronological) != closing_balance:
        raise ValueError(
            f"Divergência matemática no extrato Itaú {path.name}: "
            f"{opening_balance} + movs != {closing_balance}"
        )

    # Detect zero-sum technical credit-card PIX transit pairs on the same day
    tech_indices: set[int] = set()
    for i, (dt_i, desc_i, val_i) in enumerate(chronological):
        if desc_i.upper().startswith("CREDITO LIBERAD PIX") and val_i > 0:
            for j, (dt_j, desc_j, val_j) in enumerate(chronological):
                if j != i and dt_j == dt_i and val_j == -val_i and "PIX CARTAO" in desc_j.upper():
                    tech_indices.add(i)
                    tech_indices.add(j)
                    break

    items: list[OfficialStatementItem] = []
    for row_idx, (dt_obj, desc, val) in enumerate(chronological, 1):
        hint_dt = extract_embedded_purchase_date(desc, dt_obj) or dt_obj
        info = classify_official_descriptor(desc)
        upper_desc = desc.upper()
        is_bill_pay = "FATURA PAGA" in upper_desc or "PAG FATURA" in upper_desc
        is_tech = (row_idx - 1) in tech_indices
        is_own = bool(holder_first and re.match(rf"^PIX\s+TRANSF\s+{re.escape(holder_first)}\b", upper_desc))

        if is_bill_pay:
            kind, tipo, nat, cat, subcat, vendor = (
                "bill_payment",
                "pagamento_fatura",
                None,
                None,
                None,
                "Pagamento Fatura Cartão Itaú",
            )
        elif is_tech:
            kind = "income" if val > 0 else "expense"
            tipo, nat, cat, subcat, vendor = (
                "transferencia",
                "TRANSFERENCIA",
                None,
                None,
                "Trânsito Técnico PIX no Cartão",
            )
        elif is_own:
            kind = "income" if val > 0 else "expense"
            tipo, nat, cat, subcat, vendor = (
                "transferencia",
                "TRANSFERENCIA",
                None,
                None,
                "Transferência Própria",
            )
        elif val > 0:
            kind, tipo, nat = "income", "receita", "RENDIMENTO"
            cat, subcat, vendor = info.categoria, info.subcategoria, info.popular_vendor
        else:
            nat = (
                "IMPOSTO_TRIBUTO"
                if upper_desc == "IOF" or "IOF " in upper_desc
                else "TARIFA_ENCARGO"
                if info.categoria == "Finanças/Impostos"
                else "COMPRA"
            )
            kind, tipo = "expense", "compra"
            cat, subcat, vendor = info.categoria, info.subcategoria, info.popular_vendor

        items.append(
            OfficialStatementItem(
                doc_key=doc_key,
                row_index=row_idx,
                target="profile:itau",
                date=dt_obj.isoformat(),
                purchase_date_hint=hint_dt.isoformat(),
                statement_month=dt_obj.strftime("%Y-%m"),
                amount=f"{val:.2f}",
                kind=kind,
                description=desc,
                popular_vendor=vendor,
                platform=info.platform,
                categoria=cat,
                subcategoria=subcat,
                natureza=nat,
                tipo=tipo,
                technical_transit=is_tech,
                own_transfer=is_own,
                brand_aliases=list(info.brand_aliases),
                context_hints=list(info.context_hints),
            )
        )

    return OfficialStatementDocument(
        doc_key=doc_key,
        filename=path.name,
        sha256=digest,
        kind="bank_statement",
        institution="Itaú",
        account_profile_key="itau",
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        opening_balance=f"{opening_balance:.2f}",
        closing_balance=f"{closing_balance:.2f}",
        closing_as_of=max(closing_as_of_dt, period_end).isoformat(),
        items=items,
    )


def _parse_santander_bank_statement(path: Path, digest: str, pages: list[list[str]]) -> OfficialStatementDocument:
    lines = [line for page in pages for line in page]
    joined = "\n".join(lines)

    m_ym = re.search(
        r"\b(janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)/(\d{4})\b",
        joined,
        re.IGNORECASE,
    )
    if not m_ym:
        raise ValueError(f"Mês/ano do extrato Santander não encontrado em {path.name}.")
    stmt_month = _PT_MONTHS[m_ym.group(1).lower()]
    stmt_year = int(m_ym.group(2))
    competencia = f"{stmt_year:04d}-{stmt_month:02d}"
    doc_key = f"extrato_santander_{stmt_year:04d}{stmt_month:02d}"

    # Locate Conta Corrente -> Movimentação section
    mov_idx = None
    for idx in range(len(lines) - 2):
        if lines[idx] == "Conta Corrente" and lines[idx + 1] == "Movimentação":
            mov_idx = idx + 2
            break
    if mov_idx is None:
        raise ValueError(f"Tabela de movimentação Santander não encontrada em {path.name}.")

    # Find opening SALDO EM DD/MM
    idx = mov_idx
    while idx < len(lines) and not lines[idx].startswith("SALDO EM "):
        idx += 1
    if idx >= len(lines):
        raise ValueError(f"Saldo inicial Santander não encontrado em {path.name}.")
    opening_balance = parse_brl_decimal(lines[idx + 1])
    idx += 2

    raw_rows: list[tuple[date, str, str | None, Decimal]] = []
    current_dt: date | None = None
    closing_dt: date | None = None
    closing_balance: Decimal | None = None

    while idx < len(lines):
        cur = lines[idx]
        m_close = re.match(r"^SALDO EM\s+(\d{2})/(\d{2})$", cur)
        if m_close and idx + 1 < len(lines) and _BRL_RE.match(lines[idx + 1]):
            closing_dt = date(stmt_year, int(m_close.group(2)), int(m_close.group(1)))
            closing_balance = parse_brl_decimal(lines[idx + 1])
            break
        if re.match(r"^\d{2}/\d{2}$", cur):
            d_n, m_n = map(int, cur.split("/"))
            current_dt = date(stmt_year, m_n, d_n)
            idx += 1
            continue
        if current_dt is None:
            idx += 1
            continue
        # Skip optional daily running balance line right after a movement
        if _BRL_RE.match(cur):
            idx += 1
            continue
        # Collect description lines until doc_number ('-' or digits) followed by BRL amount
        desc_parts: list[str] = []
        while idx < len(lines):
            if (
                idx + 1 < len(lines)
                and (lines[idx] == "-" or re.match(r"^\d{3,}$", lines[idx]))
                and _BRL_RE.match(lines[idx + 1])
            ):
                break
            desc_parts.append(lines[idx])
            idx += 1
        if idx + 1 >= len(lines) or not _BRL_RE.match(lines[idx + 1]):
            break
        doc_ref = None if lines[idx] == "-" else lines[idx]
        amt = parse_brl_decimal(lines[idx + 1])
        idx += 2
        full_desc = " ".join(desc_parts).strip()
        raw_rows.append((current_dt, full_desc, doc_ref, amt))

    if closing_balance is None or closing_dt is None:
        raise ValueError(f"Saldo final Santander não encontrado em {path.name}.")

    if opening_balance + sum(r[3] for r in raw_rows) != closing_balance:
        raise ValueError(
            f"Divergência matemática no extrato Santander {path.name}: "
            f"{opening_balance} + movs != {closing_balance}"
        )

    items: list[OfficialStatementItem] = []
    for row_idx, (dt_obj, desc, doc_ref, val) in enumerate(raw_rows, 1):
        hint_dt = extract_embedded_purchase_date(desc, dt_obj) or dt_obj
        info = classify_official_descriptor(desc)
        upper_desc = desc.upper()
        if "FAT.CARTAO" in upper_desc or "FATURA CARTAO" in upper_desc:
            m_card = re.search(r"FINAL\s+(\d{4})", upper_desc)
            sub_final = m_card.group(1) if m_card else None
            kind, tipo, nat, cat, subcat, vendor = (
                "bill_payment",
                "pagamento_fatura",
                None,
                None,
                None,
                f"Pagamento Fatura Santander {sub_final or ''}".strip(),
            )
        elif val > 0:
            kind, tipo, nat = "income", "receita", "RENDIMENTO"
            sub_final = None
            cat, subcat, vendor = info.categoria, info.subcategoria, info.popular_vendor
        else:
            sub_final = None
            nat = (
                "IMPOSTO_TRIBUTO"
                if "IOF" in upper_desc
                else "TARIFA_ENCARGO"
                if "JUROS" in upper_desc or info.categoria == "Finanças/Impostos"
                else "COMPRA"
            )
            kind, tipo = "expense", "compra"
            cat, subcat, vendor = info.categoria, info.subcategoria, info.popular_vendor

        items.append(
            OfficialStatementItem(
                doc_key=doc_key,
                row_index=row_idx,
                target="profile:santander",
                date=dt_obj.isoformat(),
                purchase_date_hint=hint_dt.isoformat(),
                statement_month=competencia,
                amount=f"{val:.2f}",
                kind=kind,
                description=desc,
                popular_vendor=vendor,
                platform=info.platform,
                categoria=cat,
                subcategoria=subcat,
                natureza=nat,
                tipo=tipo,
                bank_ref=doc_ref,
                subcard_final=sub_final,
                brand_aliases=list(info.brand_aliases),
                context_hints=list(info.context_hints),
            )
        )

    return OfficialStatementDocument(
        doc_key=doc_key,
        filename=path.name,
        sha256=digest,
        kind="bank_statement",
        institution="Santander",
        account_profile_key="santander",
        period_start=date(stmt_year, stmt_month, 1).isoformat(),
        period_end=closing_dt.isoformat(),
        opening_balance=f"{opening_balance:.2f}",
        closing_balance=f"{closing_balance:.2f}",
        closing_as_of=closing_dt.isoformat(),
        items=items,
    )


def _parse_santander_card_invoice(path: Path, digest: str, pages: list[list[str]]) -> OfficialStatementDocument:
    lines = [line for page in pages for line in page]
    joined = "\n".join(lines)

    card_m = re.search(r"\b\d{4}\s+[X*]{4}\s+[X*]{4}\s+(\d{4})\b", joined)
    if not card_m:
        raise ValueError(f"Final do cartão principal Santander não encontrado em {path.name}.")
    main_card_final = card_m.group(1)

    idx_v = lines.index("Vencimento")
    d_s, m_s, y_s = lines[idx_v + 1].split("/")
    due_dt = date(int(y_s), int(m_s), int(d_s))
    competencia = f"{due_dt.year:04d}-{due_dt.month:02d}"
    doc_key = f"fatura_santander_{main_card_final}_{due_dt.year:04d}{due_dt.month:02d}"
    target = f"card:santander:{main_card_final}"

    idx_saldo = lines.index("(=) Saldo Desta Fatura")
    total_amount = parse_brl_decimal(lines[idx_saldo + 1])

    start_idx = lines.index("Detalhamento da Fatura") + 1
    end_idx = lines.index("Resumo da Fatura")
    block = lines[start_idx:end_idx]

    additional_finals: list[str] = []
    current_subcard = main_card_final
    items: list[OfficialStatementItem] = []
    row_idx = 0
    refund_counters: dict[tuple[str, str, str], int] = {}

    idx = 0
    while idx < len(block):
        cur = block[idx]
        m_sub = re.search(r"\b\d{4}\s+[X*]{4}\s+[X*]{4}\s+(\d{4})\b", cur)
        if m_sub:
            current_subcard = m_sub.group(1)
            if current_subcard != main_card_final and current_subcard not in additional_finals:
                additional_finals.append(current_subcard)
            idx += 1
            continue
        if cur == "VALOR TOTAL":
            idx += 3
            continue
        m_item = re.match(r"^(\d{2}/\d{2})\s+(.+)$", cur)
        if m_item:
            dt_ddmm = m_item.group(1)
            desc = re.sub(r"\s+", " ", m_item.group(2)).strip()
            inst_num, inst_tot = None, None
            if idx + 2 < len(block) and re.match(r"^\d{2}/\d{2}$", block[idx + 1]) and _BRL_RE.match(block[idx + 2]):
                inst_num, inst_tot = map(int, block[idx + 1].split("/"))
                raw_val = parse_brl_decimal(block[idx + 2])
                desc = f"{desc} {block[idx + 1]}"
                idx += 3
            elif idx + 1 < len(block) and _BRL_RE.match(block[idx + 1]):
                raw_val = parse_brl_decimal(block[idx + 1])
                idx += 2
            else:
                idx += 1
                continue

            item_dt, prior_cycle = _infer_statement_item_date(dt_ddmm, due_dt.year, due_dt.month)
            # On Santander card invoice: positive raw_val is expense, negative raw_val is payment/refund
            signed_amt = -raw_val
            upper_desc = desc.upper()
            is_prior_payment = "DEB AUTOM DE FATURA" in upper_desc or "PAGAMENTO DE FATURA" in upper_desc
            info = classify_official_descriptor(desc)

            if is_prior_payment:
                items.append(
                    OfficialStatementItem(
                        doc_key=doc_key,
                        row_index=0,
                        target=target,
                        subcard_final=current_subcard,
                        date=item_dt.isoformat(),
                        purchase_date_hint=item_dt.isoformat(),
                        statement_month=competencia,
                        amount=f"{signed_amt:.2f}",
                        kind="bill_payment_credit",
                        description=desc,
                        popular_vendor="Pagamento Fatura Anterior Santander",
                        platform=None,
                        categoria=None,
                        subcategoria=None,
                        natureza=None,
                        tipo="pagamento_fatura",
                        skip_insert=True,
                    )
                )
                continue

            row_idx += 1
            is_refund = signed_amt > 0
            if is_refund:
                rkey = (item_dt.isoformat(), desc, f"{signed_amt:.2f}")
                refund_counters[rkey] = refund_counters.get(rkey, 0) + 1
            nat = (
                "ESTORNO"
                if is_refund
                else "IMPOSTO_TRIBUTO"
                if "IOF" in upper_desc
                else "TARIFA_ENCARGO"
                if info.categoria == "Finanças/Impostos"
                else "COMPRA"
            )
            items.append(
                OfficialStatementItem(
                    doc_key=doc_key,
                    row_index=row_idx,
                    target=target,
                    subcard_final=current_subcard,
                    date=item_dt.isoformat(),
                    purchase_date_hint=item_dt.isoformat(),
                    statement_month=competencia,
                    amount=f"{signed_amt:.2f}",
                    kind="refund" if is_refund else "expense",
                    description=desc,
                    popular_vendor=info.popular_vendor,
                    platform=info.platform,
                    categoria=info.categoria,
                    subcategoria=info.subcategoria,
                    natureza=nat,
                    tipo="estorno" if is_refund else "compra",
                    installment_number=inst_num,
                    installment_total=inst_tot,
                    prior_cycle_installment=prior_cycle and bool(inst_tot),
                    brand_aliases=list(info.brand_aliases),
                    context_hints=list(info.context_hints),
                )
            )
            continue
        idx += 1

    computed_net = sum(Decimal(it.amount) for it in items if not it.skip_insert)
    if computed_net != -total_amount:
        raise ValueError(
            f"Divergência matemática na fatura Santander {path.name}: "
            f"soma dos itens={-computed_net:.2f} != total={total_amount:.2f}"
        )

    return OfficialStatementDocument(
        doc_key=doc_key,
        filename=path.name,
        sha256=digest,
        kind="card_statement",
        institution="Santander",
        card_final=main_card_final,
        additional_finals=additional_finals,
        competencia=competencia,
        due_date=due_dt.isoformat(),
        total_amount=f"{total_amount:.2f}",
        status="aberta",
        items=items,
    )


def parse_official_pdf(path: Path, passwords: Iterable[str] = ()) -> OfficialStatementDocument:
    digest, pages = _open_pdf_lines(path, passwords)
    joined = "\n".join(line for page in pages for line in page)
    if "Resumo da fatura em R$" in joined and "Total desta fatura" in joined:
        return _parse_itau_card_invoice(path, digest, pages)
    if "extrato conta / lançamentos" in joined and "período de visualização:" in joined:
        return _parse_itau_bank_statement(path, digest, pages)
    if "EXTRATO CONSOLIDADO" in joined and "Conta Corrente" in joined and "Movimentação" in joined:
        return _parse_santander_bank_statement(path, digest, pages)
    if "SANTANDER" in joined and "Detalhamento da Fatura" in joined and "Resumo da Fatura" in joined:
        return _parse_santander_card_invoice(path, digest, pages)
    raise ValueError(f"Formato de comprovante oficial não reconhecido em '{path.name}'.")


def parse_official_pdf_batch(
    paths: Iterable[Path],
    *,
    cpf: str | None = None,
) -> list[OfficialStatementDocument]:
    resolved_paths = [Path(p).resolve() for p in paths]
    passwords: list[str] = []
    if cpf:
        passwords.append(cpf)
    passwords.extend(_extract_in_memory_cpf_candidates(resolved_paths))

    docs_by_sha: dict[str, OfficialStatementDocument] = {}
    for path in resolved_paths:
        parsed = parse_official_pdf(path, passwords)
        docs_by_sha[parsed.sha256] = parsed

    docs = list(docs_by_sha.values())
    correlate_parsed_statements(docs)
    return docs


def correlate_parsed_statements(docs: list[OfficialStatementDocument]) -> None:
    """Link bank-statement bill payments to card invoices in the same batch."""
    bank_payments: list[tuple[str, OfficialStatementItem]] = []
    for doc in docs:
        if doc.kind == "bank_statement":
            for item in doc.items:
                if item.kind == "bill_payment":
                    bank_payments.append((doc.institution, item))

    for doc in docs:
        if doc.kind != "card_statement" or not doc.total_amount or not doc.due_date:
            continue
        expected_debit = -Decimal(doc.total_amount)
        due_dt = date.fromisoformat(doc.due_date)
        for inst, pay_item in bank_payments:
            if inst != doc.institution:
                continue
            if Decimal(pay_item.amount) != expected_debit:
                continue
            pay_dt = date.fromisoformat(pay_item.date)
            if abs((pay_dt - due_dt).days) <= 10:
                doc.paid_date = pay_item.date
                doc.status = "paga"
                break
