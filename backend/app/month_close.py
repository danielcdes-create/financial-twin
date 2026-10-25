"""Persistent monthly checklist. Financial source documents are read-only.

Checkmarks are owner attestations, not inferred bank reconciliation. Monthly
balance references are kept here, independently of today's account positions.
"""
from app.commitment_estimates import effective_commitments

import calendar
import copy
import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from bson import json_util
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from app.account_profiles import account_profiles, legacy_accounts_by_profile, effective_capture_account_key
from app.atomic import mongo_snapshot, mongo_transaction, session_database
from app.cards import card_item
from app.economic_policy import amount, effects
from app.category_filter import matches_category, UNCATEGORIZED
from app.financial_time import date_filter, local_date, civil_date, today
from app.movement_insert_lock import lock_movement_insert
from app.auth import require_session
from app.commitments import schedule_from, historical_occurrence_conflicts
from app.future_commitments import effective_documents
from app.mutation_identity import durable_mutation
from app.economic_period import economic_rows
from contextlib import nullcontext
from app.transfer_legs import resolved_transfer_legs
try:
    from app.telemetry import SpanTimer
except Exception:
    def SpanTimer(*_args, **_kwargs):
        return nullcontext()

router = APIRouter(prefix="/v1/month-close", dependencies=[Depends(require_session)])


def database(request: Request):
    if request.app.state.db is None:
        raise HTTPException(503, "Banco indisponível.")
    return request.app.state.db


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=0)
    request_id:UUID|None=None


class ItemUpdate(Version):
    expected_source_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["pending", "done", "not_applicable"]
    note: str = Field(default="", max_length=2000)


class BalancesUpdate(Version):
    opening: Decimal = Field(ge=Decimal("-999999999.99"), le=Decimal("999999999.99"), decimal_places=2, allow_inf_nan=False)
    closing: Decimal = Field(ge=Decimal("-999999999.99"), le=Decimal("999999999.99"), decimal_places=2, allow_inf_nan=False)


class CustomItem(Version):
    request_id: UUID
    group_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=3, max_length=180)


def bounds(month):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "Informe o mês no formato AAAA-MM.")
    year, number = map(int, month.split("-"))
    if not 1900 <= year <= 2200:
        raise HTTPException(422, "Escolha um ano entre 1900 e 2200.")
    return date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1])


def digest(*values):
    return hashlib.sha256(json_util.dumps(values, sort_keys=True).encode()).hexdigest()


def money(value):
    return format(value, ".2f")


def in_period(value, start, end, *, civil=False):
    if not value:
        return False
    try:
        day = value if isinstance(value, date) and not isinstance(value, datetime) else (civil_date(value) if civil else local_date(value))
        return start <= day <= end
    except (ValueError, TypeError):
        return False


def stored_state(db, month):
    return db.account_profiles.find_one({"_id": "month-close:" + month})


def financial_context(db, month):
    with SpanTimer("month_close.financial_context", period=month):
        return _financial_context_impl(db, month)


def _financial_context_impl(db, month):
    start, end = bounds(month)
    window = date_filter(start, end)
    transactions = list(db.transactions.find({"data_evento": window}).sort("_id", 1))
    captures = list(db.provisional_transactions.find({"data_evento": window,
                      "status": {"$in": ["confirmed", "provisional", "enriched"]}}).sort("_id", 1))
    confirmed = [row for row in captures if row.get("status") == "confirmed"]
    economic_transactions = economic_rows(db.transactions, start, end, dated_rows=transactions)
    economic_confirmed = economic_rows(db.provisional_transactions, start, end, {"status": "confirmed"}, dated_rows=confirmed)
    all_confirmed = list({str(row.get("_id")): row for row in [*confirmed, *economic_confirmed]}.values())
    original_accounts = list(db.accounts.find({}).sort("_id", 1))
    checking_accounts = [row for row in original_accounts if row.get("tipo") == "conta_corrente"]
    legacy_by_profile = legacy_accounts_by_profile(db, rows=checking_accounts)
    known_tx_by_id = {str(row["_id"]): row for row in [*transactions, *economic_transactions] if row.get("_id") is not None}
    transfer_legs = resolved_transfer_legs(db, all_confirmed, legacy_by_profile, posted_by_id=known_tx_by_id)
    all_profile_docs = list(db.account_profiles.find({}))
    profiles = account_profiles(db, stored_rows=all_profile_docs)
    profile_meta = {row["_id"]: row for row in all_profile_docs if row.get("entity_type") in {"card", "bill"}}
    all_bills = list(db.credit_card_bills.find({}).sort("_id", 1))
    all_obligations = effective_documents(db, "obligation", db.obligations.find({}).sort("_id", 1))
    schedules = []
    for commitment in effective_commitments(
        db,
        db.commitments.find({}).sort("_id", 1),
        as_of=end,
        legacy_map=legacy_by_profile,
        preloaded_transactions=list(known_tx_by_id.values()) or None,
        preloaded_captures=all_confirmed or None,
    ):
        rows = [row for row in schedule_from(commitment, start, 32) if row["due_date"] <= end.isoformat()]
        links = [link for link in commitment.get("linked_transactions", [])
                 if str(link.get("period_key", "")).startswith(month)
                 or str(link.get("paid_date", "")).startswith(month)
                 or str(link.get("due_date", "")).startswith(month)
                 or link.get("transaction_id") in {str(tx["_id"]) for tx in transactions}
                 or link.get("provisional_id") in {tx.get("provisional_id") for tx in captures}]
        if rows or links or in_period(commitment.get("purchase_date"), start, end, civil=True):
            schedules.append({"id": str(commitment["_id"]), "name": commitment.get("name"),
                "kind": commitment.get("kind"), "amount": str(commitment.get("amount")),
                "frequency": commitment.get("frequency"),
                "status": commitment.get("status"), "category": commitment.get("category"),
                "account_id": commitment.get("account_id"), "card_id": commitment.get("card_id"),
                "notes": commitment.get("notes"), "amount_estimate": commitment.get("amount_estimate"), "rows": rows, "links": links,
                "payment_occurrence_requires_review": bool(historical_occurrence_conflicts(commitment))})
    for plan in effective_documents(db, "installment", db.installment_plans.find({}).sort("_id", 1)):
        rows = [row for row in plan.get("installments", []) if row.get("competencia") == month
                or in_period(row.get("data_vencimento"), start, end, civil=True)]
        if rows:
            schedules.append({"id": str(plan["_id"]), "status": plan.get("status"), "rows": rows,
                              "needs_review": plan.get("future_correction_needs_review", False)})
    # Payments may settle an invoice from an earlier competence.
    transaction_ids = {row["_id"] for row in transactions}
    reconciliations = list(db.reconciliations.find({}).sort("_id", 1))
    payment_targets = set()
    for link in reconciliations:
        endpoints = [link.get("origem", {}), link.get("destino", {})]
        if link.get("transaction_id") in transaction_ids or any(e.get("entity") == "transactions" and e.get("id") in transaction_ids for e in endpoints):
            payment_targets.update(e.get("id") for e in endpoints if e.get("entity") in {"obligations", "credit_card_bills"})
            if link.get("obligation_id"):
                payment_targets.add(link["obligation_id"])
    paid_bill_ids = {row.get("origem_id") for row in all_obligations if row["_id"] in payment_targets and row.get("tipo") == "fatura_cartao"}
    bills = [row for row in all_bills if row.get("competencia") == month or in_period(row.get("data_vencimento"), start, end, civil=True)
             or in_period(row.get("data_pagamento"), start, end) or row["_id"] in payment_targets | paid_bill_ids]
    bill_ids = {row["_id"] for row in bills}
    obligations = [row for row in all_obligations if row.get("origem_id") in bill_ids
                   or row.get("competencia") == month or in_period(row.get("data_vencimento"), start, end, civil=True)]
    item_ids = {item["transaction_id"] for row in bills for item in row.get("items", []) if item.get("transaction_id")}
    missing_item_ids = [tid for tid in item_ids if str(tid) not in known_tx_by_id]
    if missing_item_ids:
        for row in db.transactions.find({"_id": {"$in": missing_item_ids}}).sort("_id", 1):
            known_tx_by_id[str(row["_id"])] = row
    invoice_transactions = sorted(
        (known_tx_by_id[str(tid)] for tid in item_ids if str(tid) in known_tx_by_id),
        key=lambda row: row.get("_id"),
    ) if item_ids else []
    linked_ids = bill_ids | {row["_id"] for row in obligations} | transaction_ids | item_ids
    links = [row for row in reconciliations if row.get("obligation_id") in linked_ids or row.get("transaction_id") in linked_ids
             or any((row.get(endpoint) or {}).get("id") in linked_ids for endpoint in ("origem", "destino"))]
    reviews = list(db.review_queue.find({"status": "pending"}).sort("_id", 1))
    event_ids = {row.get("event_id") for row in captures if row.get("event_id")}
    period_reviews = [row for row in reviews if row.get("event_id") in event_ids or row.get("candidate_transaction_id") in transaction_ids
                      or in_period(row.get("created_at"), start, end)]
    totals = {"income": Decimal(0), "expenses": Decimal(0)}
    unknown = unassigned = transfer_pending = missing_category = 0
    known_account_ids = {row["_id"] for row in original_accounts}
    known_profile_keys = {row["key"] for row in profiles}
    known_card_ids = {row["_id"] for row in original_accounts if row.get("tipo") == "cartao_credito"}
    economic_ids = {str(row["_id"]) for row in economic_transactions}
    for captured, row in [(False, row) for row in economic_transactions] + [(True, row) for row in economic_confirmed]:
        if captured and str(row.get("linked_transaction_id")) in economic_ids:
            continue
        effect = effects(row, captured)
        for key in totals:
            totals[key] += effect[key]
    classification_rows = [(False, row) for row in {str(row["_id"]): row for row in [*transactions, *economic_transactions]}.values()] + [(True, row) for row in all_confirmed]
    for captured, row in classification_rows:
        if captured and str(row.get("linked_transaction_id")) in economic_ids | {str(value) for value in transaction_ids}:
            continue
        effect = effects(row, captured)
        unknown += effect["role"] == "unknown"
        missing_category += effect["role"] in {"expense", "refund", "income"} and matches_category(row.get("categoria_sugerida") if captured else row.get("categoria"),UNCATEGORIZED)
        transfer = row.get("transfer") or {}
        if transfer:
            legs = transfer_legs[str(row.get("_id"))]
            transfer_pending += (any(leg["status"] != "valid" for leg in legs.values())
                or transfer.get("reconciliation_status") != "conciliada"
                or transfer.get("from_account_key") not in known_profile_keys or transfer.get("to_account_key") not in known_profile_keys)
        elif captured:
            unassigned += row.get("account_profile_id") not in known_profile_keys and row.get("account_id") not in known_account_ids and row.get("card_id") not in {str(value) for value in known_card_ids}
        else:
            unassigned += row.get("account_id") not in known_account_ids and not row.get("bill_id") and row.get("account_profile_id") not in known_profile_keys
    return {"start": start, "end": end, "transactions": transactions, "captures": captures, "confirmed": confirmed,
            "economic_transactions": economic_transactions, "economic_confirmed": economic_confirmed, "transfer_legs": transfer_legs,
            "profiles": profiles, "accounts": original_accounts, "legacy_by_profile": legacy_by_profile, "profile_meta": profile_meta, "bills": bills,
            "obligations": obligations, "schedules": schedules, "invoice_transactions": invoice_transactions, "links": links,
            "period_reviews": period_reviews, "undated_reviews": len(reviews) - len(period_reviews), "totals": totals,
            "unknown": unknown, "unassigned": unassigned, "transfer_pending": transfer_pending, "missing_category": missing_category}


GENERAL = [
    ("scope", "Confirmar todas as contas, cartões e meios de pagamento usados", "Inclua contas encerradas no mês, carteiras, dinheiro e cartões adicionais. Justifique itens sem uso."),
    ("capture", "Importar extratos e resolver capturas e revisões", "Compare o período completo; confirme, descarte ou vincule evidências sem duplicar movimentações."),
    ("classification", "Conferir conta, natureza e classificação dos lançamentos", "Receitas, despesas, estornos, tarifas e transferências precisam estar corretamente identificados."),
    ("transfers", "Conciliar transferências entre contas", "Confira origem, destino, valor e as duas pontas. Transferências próprias não são receita nem despesa."),
    ("duplicates", "Conferir duplicidades e movimentos ausentes", "Compare extratos, faturas, comprovantes e capturas; uma evidência não é uma nova transação."),
    ("commitments", "Conferir parcelas, assinaturas, empréstimos e outros compromissos", "Compare valores previstos, débitos efetivos, juros e pagamentos vinculados; mantenha o saldo futuro devido."),
    ("assets", "Conferir investimentos, criptoativos e outros saldos", "Registre posições, rendimentos, taxas e variação de valor nas observações quando aplicável."),
    ("summary", "Revisar receitas, gastos, dívidas e comprovantes do mês", "Pagamentos de fatura liquidam dívidas; não some novamente as compras. Registre eventuais exceções e fontes consultadas."),
]
ACCOUNT = [
    ("statement", "Extrato completo do mês", "Extrato de todo o período, incluindo Pix, boletos, débito, saques e créditos."),
    ("balances", "Saldos de abertura e encerramento conferidos", "Informe o saldo antes do primeiro dia e o saldo ao final do último dia, obtidos no extrato."),
    ("movements", "Entradas e saídas conferidas com o extrato", "Compare cada lançamento, data e valor; confira receitas e débitos automáticos."),
    ("transfers", "Transferências e saques conferidos", "Verifique as duas pontas de transferências e o destino de saques/depósitos em dinheiro."),
    ("charges", "Tarifas, juros, impostos e rendimentos conferidos", "Confira custos bancários, IOF e rendimentos; posições de investimento podem variar sem um débito/crédito."),
    ("cards", "Débitos de faturas e compromissos vinculados", "Compare pagamentos de cartão, parcelas e assinaturas com a conta pagadora, sem duplicar despesas."),
]
CARD = [
    ("statement", "Fatura completa e todas as páginas disponíveis", "Inclua a competência do mês e faturas anteriores pagas ou vencidas neste período."),
    ("items", "Compras, parcelas, estornos e encargos conferidos", "Compare itens, comprovantes, parcelas contratadas, juros, tarifas e ajustes com a fatura."),
    ("total", "Total, valor pago e saldo em aberto conferidos", "Compare o documento do cartão com a fatura e a obrigação cadastradas."),
    ("dates", "Fechamento, vencimento e limite conferidos", "Registre nas observações datas e limites informados pelo emissor quando precisarem de acompanhamento."),
    ("payments", "Pagamentos conciliados com a conta pagadora", "Confira cada débito, pagamento parcial e eventual saldo anterior. Fatura paga não comprova automaticamente todas as compras."),
    ("future", "Parcelas futuras e assinaturas conferidas", "Confira número de parcelas, renovações, cobranças duplicadas e valores que ainda serão devidos."),
]


def view(db, month, state=None):
    with SpanTimer("month_close.view", period=month):
        return _view_impl(db, month, state=state)


def _view_impl(db, month, state=None):
    state = stored_state(db, month) if state is None else state
    state = state or {"revision": 0, "items": {}, "balances": {}, "custom": [], "status": "open"}
    ctx = financial_context(db, month)
    groups = []
    checks = state.get("items", {})
    balances = state.get("balances", {})
    fingerprints = []

    def group(identity, title, kind, requirements, source, *, activity=0, detail=None, blocks=None, account_balance=None, item_sources=None):
        source_token = digest(source)
        fingerprints.append((identity, digest(source_token, item_sources) if item_sources else source_token))
        items = []
        definitions = [(key, label, help_text, False) for key, label, help_text in requirements]
        definitions += [(item["id"], item["title"], "Pendência adicional definida por você.", True)
                        for item in state.get("custom", []) if item["group_id"] == identity]
        for key, label, help_text, custom in definitions:
            item_id = identity + ":" + key
            saved = checks.get(item_id, {})
            required = (blocks or {}).get(key, [])
            item_token = digest(source_token, account_balance) if key == "balances" else source_token
            if item_sources and key in item_sources:
                item_token = digest(source_token, item_sources[key])
            status = saved.get("status", "pending")
            stale = status != "pending" and saved.get("source_token") != item_token
            if stale:
                status = "pending"
            completed = status in {"done", "not_applicable"} and not required
            can_na = not required and (custom or key not in {"statement", "balances", "total"} or not activity)
            items.append({"id": item_id, "title": label, "help": help_text, "status": status, "note": saved.get("note", ""),
                          "needs_review": stale, "completed": completed, "can_not_apply": can_na,
                          "blockers": required, "source_token": item_token, "custom": custom})
        groups.append({"id": identity, "title": title, "kind": kind, "items": items, "detail": detail,
                       "balance": account_balance, "completed": sum(item["completed"] for item in items), "total": len(items)})

    unresolved = sum(row.get("status") != "confirmed" for row in ctx["captures"])
    legacy = ctx["legacy_by_profile"]
    opening_gaps = []
    for profile in ctx["profiles"]:
        original_id = (legacy.get(profile["key"]) or {}).get("_id")
        activity = [row for row in ctx["transactions"] + ctx["confirmed"]
                    if row.get("account_profile_id") == profile["key"]
                    or (original_id is not None and row.get("account_id") == original_id and not row.get("account_profile_id"))
                    or profile["key"] in {(row.get("transfer") or {}).get("from_account_key"),
                                          (row.get("transfer") or {}).get("to_account_key")}]
        if profile.get("data_abertura") and any(local_date(row["data_evento"]).isoformat() < profile["data_abertura"] for row in activity):
            opening_gaps.append(f'{profile.get("apelido") or profile["instituicao"]}: há movimentos anteriores à data de abertura. Revise o cadastro da conta.')
    general_blocks = {
        "capture": ([f'{unresolved} captura(s) do mês ainda aguardam confirmação ou descarte.'] if unresolved else [])
                   + ([f'{len(ctx["period_reviews"])} revisão(ões) relacionadas ao mês aguardam decisão.'] if ctx["period_reviews"] else []),
        "classification": ([f'{ctx["unknown"]} movimentação(ões) sem natureza econômica definida.'] if ctx["unknown"] else [])
                          + ([f'{ctx["unassigned"]} movimentação(ões) sem conta/cartão identificável.'] if ctx["unassigned"] else [])
                          + ([f'{ctx["missing_category"]} movimentação(ões) sem categoria.'] if ctx["missing_category"] else []),
        "scope": ["Há faturas no período sem cartão cadastrado. Confira os vínculos antes de concluir."]
                 if any(row.get("card_account_id") not in {account["_id"] for account in ctx["accounts"] if account.get("tipo") == "cartao_credito"} for row in ctx["bills"]) else [],
        "transfers": [f'{ctx["transfer_pending"]} transferência(s) do mês ainda precisam de conciliação.'] if ctx["transfer_pending"] else [],
        "commitments": ["Há pagamento histórico vinculado à próxima cobrança ou a uma posterior. Confira o histórico e a agenda do compromisso."]
            if any(row.get("payment_occurrence_requires_review") for row in ctx["schedules"]) else [],
    }
    general_blocks["scope"].extend(opening_gaps)
    financial_source = [ctx[key] for key in ("transactions", "captures", "economic_transactions", "economic_confirmed", "transfer_legs", "bills", "obligations", "invoice_transactions", "links", "period_reviews")]
    group("general", "Conferência geral", "general", GENERAL, financial_source, blocks=general_blocks, item_sources={"commitments": ctx["schedules"]},
          detail=f'{len(ctx["transactions"]) + len(ctx["confirmed"])} movimentações; {ctx["undated_reviews"]} revisão(ões) adicional(is) fora do recorte ou com período indefinido.')

    legacy = ctx["legacy_by_profile"]
    represented = {row["_id"] for row in legacy.values()}
    specs = []
    for profile in ctx["profiles"]:
        opened = profile.get("data_abertura")
        original = legacy.get(profile["key"])
        original_id = original["_id"] if original else None
        has_activity = any(row.get("account_profile_id") == profile["key"]
            or (original_id is not None and row.get("account_id") == original_id and not row.get("account_profile_id"))
            or profile["key"] in {(row.get("transfer") or {}).get("from_account_key"), (row.get("transfer") or {}).get("to_account_key")}
            for row in ctx["transactions"] + ctx["confirmed"])
        identity = "account:" + profile["key"]
        has_history = (identity in balances or any(item.startswith(identity + ":") for item in checks)
                       or any(item.get("group_id") == identity for item in state.get("custom", [])))
        if opened and date.fromisoformat(opened) > ctx["end"] and not has_activity and not has_history:
            continue
        specs.append(("account:" + profile["key"], profile.get("apelido") or profile["instituicao"], profile["key"],
                      original["_id"] if original else None, profile.get("tipo", "conta_corrente")))
    for original in ctx["accounts"]:
        if original.get("tipo") != "cartao_credito" and original["_id"] not in represented:
            suffix = str(original.get("conta") or original["_id"])[-4:]
            specs.append(("legacy:" + str(original["_id"]), f'{original.get("instituicao", "Conta importada")} · {suffix}', None, original["_id"], original.get("tipo", "outra")))
    transaction_ids = {str(row["_id"]) for row in ctx["transactions"]}
    for identity, title, key, original_id, kind in specs:
        rows = [row for row in ctx["transactions"] if (original_id is not None and row.get("account_id") == original_id) or (key and row.get("account_profile_id") == key)]
        flow = sum((amount(row.get("valor")) or Decimal(0) for row in rows), Decimal(0))
        sources = list(rows)
        for captured in ctx["confirmed"]:
            transfer = captured.get("transfer") or {}
            magnitude = abs(amount(captured.get("valor")) or Decimal(0))
            if key and transfer and key in {transfer.get("from_account_key"), transfer.get("to_account_key")}:
                sources.append(captured)
                for field, link, sign in (("from_account_key", "source_transaction_id", -1), ("to_account_key", "destination_transaction_id", 1)):
                    if transfer.get(field) == key and ctx["transfer_legs"][str(captured.get("_id"))][link]["status"] == "unlinked":
                        flow += magnitude * sign
            elif not transfer and ((key and effective_capture_account_key(captured, legacy) == key)
                    or (not key and not captured.get("account_profile_id") and original_id is not None and captured.get("account_id") == original_id)):
                sources.append(captured)
                # A confirmed capture explicitly linked to a posted ledger row is evidence.
                if str(captured.get("linked_transaction_id")) not in transaction_ids:
                    flow += amount(captured.get("valor")) or Decimal(0)
        stored_balances = balances.get(identity, {})
        opening = Decimal(stored_balances["opening"]) if stored_balances else None
        closing = Decimal(stored_balances["closing"]) if stored_balances else None
        difference = closing - (opening + flow) if opening is not None else None
        cash = kind not in {"investimento", "criptoativos", "financiamento"}
        balance = {"opening": money(opening) if opening is not None else None, "closing": money(closing) if closing is not None else None,
                   "flow": money(flow), "expected": money(opening + flow) if opening is not None else None,
                   "difference": money(difference) if difference is not None else None, "cash_account": cash}
        blocks = {"balances": ["Informe os dois saldos do extrato para conferir a diferença."] if opening is None else
                  [f'Diferença de R$ {money(difference)} entre o saldo final informado e abertura + movimentos cadastrados.'] if cash and difference else []}
        # Accounts unused in this month can be explicitly excluded with a reason.
        if not sources and not stored_balances:
            blocks["balances"] = []
        group(identity, title, "account", ACCOUNT, [identity, sources], activity=len(sources), blocks=blocks,
              detail=f'{len(sources)} movimentação(ões) do mês. Saldos abaixo são referências deste fechamento, não o saldo atual da conta.', account_balance=balance)

    for original in ctx["accounts"]:
        if original.get("tipo") != "cartao_credito":
            continue
        meta = ctx["profile_meta"].get(f'card-profile:{original["_id"]}', {})
        card = card_item(original, meta)
        rows = [row for row in ctx["bills"] if row.get("card_account_id") == original["_id"]]
        activity = [row for row in ctx["transactions"] if row.get("account_id") == original["_id"]]
        captures = [row for row in ctx["confirmed"] if row.get("account_id") == original["_id"]
                    or row.get("card_id") == str(original["_id"])]
        if card["deleted"] and not rows and not activity and not captures:
            continue
        ids = {row["_id"] for row in rows}
        obligations = [row for row in ctx["obligations"] if row.get("tipo") == "fatura_cartao" and row.get("origem_id") in ids]
        item_ids = {item.get("transaction_id") for row in rows for item in row.get("items", [])}
        purchases = [row for row in ctx["invoice_transactions"] if row["_id"] in item_ids]
        links = [row for row in ctx["links"] if row.get("obligation_id") in {o["_id"] for o in obligations}
                 or any((row.get(endpoint) or {}).get("id") in ids | {o["_id"] for o in obligations} for endpoint in ("origem", "destino"))]
        gaps = []
        for bill in rows:
            total = amount(bill.get("valor_total")) or Decimal(0)
            paid = amount(bill.get("valor_pago", bill.get("pagamentos"))) or Decimal(0)
            balance = amount(bill.get("saldo_aberto"))
            peers = [o for o in obligations if o.get("origem_id") == bill["_id"]]
            if balance is None or paid + balance != total:
                gaps.append(f'Fatura {bill["competencia"]}: total, pagamento e saldo divergem.')
            if len(peers) != 1 or amount(peers[0].get("valor_original")) != total or amount(peers[0].get("valor_pago")) != paid or amount(peers[0].get("saldo_aberto")) != balance:
                gaps.append(f'Fatura {bill["competencia"]}: confira a obrigação vinculada.')
        label = card["apelido"] or card["instituicao"]
        if card["cartao_final"]:
            label += " · " + card["cartao_final"]
        group("card:" + card["id"], label, "card", CARD, [rows, obligations, purchases, activity, links, captures], activity=len(rows) + len(activity) + len(captures),
              detail=f'{len(rows)} fatura(s) relacionada(s) ao mês; {len(captures)} captura(s) confirmada(s) para conferir' + (" · cartão excluído da lista, com histórico no período." if card["deleted"] else "."), blocks={"total": gaps})

    count = sum(group["total"] for group in groups)
    completed = sum(group["completed"] for group in groups)
    source_token = digest(fingerprints)
    return {"month": month, "start": ctx["start"].isoformat(), "end": ctx["end"].isoformat(), "revision": state["revision"],
            "status": state.get("status", "open"), "closed_at": state.get("closed_at"),
            "source_changed": state.get("status") == "closed" and state.get("closed_source_token") != source_token,
            "source_token": source_token, "groups": groups, "completed": completed, "total": count,
            "progress": round(100 * completed / count) if count else 0,
            "can_close": completed == count and ctx["end"] < today(), "month_ended": ctx["end"] < today(),
            "totals": {key: money(value) for key, value in ctx["totals"].items()},
            "economic_period_basis": "competencia_declarada; sem competencia: data_evento_brasilia", "cash_period_basis": "data_evento_brasilia",
            "last_close": state.get("closed_snapshot"),
            "attestation": "As marcações registram sua conferência. Completude dos documentos e vínculos exigem comparação com os extratos e faturas originais."}


def writable(db, month, body):
    bounds(month)
    lock_movement_insert(db)
    state = stored_state(db, month)
    revision = state.get("revision", 0) if state else 0
    if revision != body.expected_revision:
        raise HTTPException(409, "O checklist mudou. Atualize antes de salvar; a edição não foi aplicada.")
    return copy.deepcopy(state or {"revision": 0, "items": {}, "balances": {}, "custom": [], "status": "open"})


def save(db, month, before, after, event):
    now = datetime.now(timezone.utc)
    after.update({"revision": before["revision"] + 1, "updated_at": now, "entity_type": "month_close", "key": "month-close:" + month})
    after.pop("_id", None)
    db.account_profiles.update_one({"_id": "month-close:" + month}, {"$set": after}, upsert=True)
    db.audit_log.insert_one({"event": event, "month": month, "before": before, "after": copy.deepcopy(after), "created_at": now})
    return view(db, month, after)


def ensure_open(state):
    if state.get("status") == "closed":
        raise HTTPException(409, "Reabra o checklist antes de alterar uma conferência concluída.")


def find_group(result, group_id):
    group = next((group for group in result["groups"] if group["id"] == group_id), None)
    if group is None:
        raise HTTPException(404, "Conta, cartão ou seção não encontrado neste mês.")
    return group


@router.get("/{month}")
def get_checklist(month: str, db=Depends(database)):
    with mongo_snapshot(db) as (reading, consistency):
        return {**view(reading, month), "read_consistency": consistency}


@router.patch("/{month}/items/{item_id:path}")
@durable_mutation('month_item',('month','item_id'))
def update_item(month: str, item_id: str, body: ItemUpdate, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        state = writable(writing, month, body)
        ensure_open(state)
        result = view(writing, month, state)
        item = next((item for group in result["groups"] for item in group["items"] if item["id"] == item_id), None)
        if item is None:
            raise HTTPException(404, "Item não encontrado neste mês.")
        if body.status != "pending" and body.expected_source_token != item["source_token"]:
            raise HTTPException(409, "Os dados financeiros mudaram. Atualize e confira o item novamente antes de marcar.")
        if body.status != "pending" and item["blockers"]:
            raise HTTPException(422, "Resolva as pendências: " + " ".join(item["blockers"]))
        if body.status == "not_applicable" and (not item["can_not_apply"] or len(body.note) < 5):
            raise HTTPException(422, "Este item precisa ser conferido ou ter uma justificativa válida de não aplicação.")
        group = next(group for group in result["groups"] if item in group["items"])
        if body.status == "done" and item_id.endswith(":balances"):
            if group["balance"]["opening"] is None:
                raise HTTPException(422, "Informe os saldos de abertura e encerramento antes de marcar a conferência.")
            if not group["balance"]["cash_account"] and Decimal(group["balance"]["difference"]) and len(body.note) < 5:
                raise HTTPException(422, "Explique a variação de valor da posição de investimento nas observações.")
        after = copy.deepcopy(state)
        after["items"][item_id] = {"status": body.status, "note": body.note, "source_token": item["source_token"], "checked_at": datetime.now(timezone.utc)}
        return save(writing, month, state, after, "month_close_item_changed")


@router.put("/{month}/balances/{group_id:path}")
@durable_mutation('month_balances',('month','group_id'))
def update_balances(month: str, group_id: str, body: BalancesUpdate, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        state = writable(writing, month, body)
        ensure_open(state)
        group = find_group(view(writing, month, state), group_id)
        if group["kind"] != "account":
            raise HTTPException(422, "Saldos de extrato pertencem a uma conta.")
        after = copy.deepcopy(state)
        after["balances"][group_id] = {"opening": money(body.opening), "closing": money(body.closing)}
        return save(writing, month, state, after, "month_close_balances_changed")


@router.post("/{month}/custom", status_code=201)
@durable_mutation('month_custom',('month',))
def add_custom(month: str, body: CustomItem, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        state = writable(writing, month, body)
        ensure_open(state)
        find_group(view(writing, month, state), body.group_id)
        key = "custom-" + body.request_id.hex
        if any(item["id"] == key for item in state["custom"]):
            raise HTTPException(409, "Esta pendência já foi adicionada. Atualize a lista.")
        if len(state["custom"]) >= 100:
            raise HTTPException(422, "Limite de 100 pendências adicionais por mês.")
        after = copy.deepcopy(state)
        after["custom"].append({"id": key, "group_id": body.group_id, "title": body.title})
        return save(writing, month, state, after, "month_close_item_added")


@router.post("/{month}/close")
@durable_mutation('month_close',('month',))
def close_month(month: str, body: Version, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        state = writable(writing, month, body)
        ensure_open(state)
        result = view(writing, month, state)
        if not result["can_close"]:
            raise HTTPException(422, "Conclua todas as conferências e pendências. O mês também precisa ter terminado.")
        after = copy.deepcopy(state)
        closed_at = datetime.now(timezone.utc).isoformat()
        after.update({"status": "closed", "closed_at": closed_at, "closed_source_token": result["source_token"],
                      "closed_snapshot": {"closed_at": closed_at, "start": result["start"], "end": result["end"],
                        "totals": result["totals"], "completed": result["completed"], "total": result["total"], "groups": result["groups"]}})
        return save(writing, month, state, after, "month_close_completed")


@router.post("/{month}/reopen")
@durable_mutation('month_reopen',('month',))
def reopen_month(month: str, body: Version, db=Depends(database)):
    with mongo_transaction(db) as session:
        writing = session_database(db, session)
        state = writable(writing, month, body)
        if state.get("status") != "closed":
            raise HTTPException(409, "Este checklist já está aberto.")
        after = copy.deepcopy(state)
        after.update({"status": "open", "closed_at": None})
        return save(writing, month, state, after, "month_close_reopened")
