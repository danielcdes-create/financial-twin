"""Concilia comprovantes bancários e faturas oficiais (PDF) contra o MongoDB.

Lê comprovantes oficiais Itaú e Santander diretamente dos PDFs locais (desbloqueando
PDFs protegidos por CPF em memória quando necessário), concilia lançamentos com nomes
populares já cadastrados pelo usuário, preserva categorias validadas e insere de forma
idempotente apenas o que estiver ausente (incluindo encargos de PgConta/PIX no cartão).

Modo padrão: DRY-RUN seguro (não altera o banco; gera relatório em `data/`).
Para efetivar no banco com backup automático prévio, adicione `--apply`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from bson import json_util
from pymongo import MongoClient

from app.atomic import AtomicUnavailable, mongo_transaction, session_database
from app.config import get_settings
from app.main import configured_mongodb_uri
from app.movement_insert_lock import lock_movement_insert
from app.official_reconciler import (
    apply_reconciliation_plan,
    build_reconciliation_plan,
    format_markdown_report,
)
from app.official_statement_parsers import OfficialStatementDocument, parse_official_pdf_batch

AUTO_DISCOVERY_PATTERNS = (
    "Fatura_Itau*.pdf",
    "itau_extrato*.pdf",
    "ComprovanteSantander*.pdf",
    "Fatura_*MASTER*.pdf",
    "Fatura_*SANTANDER*.pdf",
)

TOUCHED_COLLECTIONS = (
    "transactions",
    "provisional_transactions",
    "raw_events",
    "merchants",
    "accounts",
    "account_profiles",
    "account_balance_snapshots",
    "credit_card_bills",
    "obligations",
    "official_statement_reconciliations",
    "capture_batch_locks",
    "audit_log",
)


def _discover_pdfs(pdf_args: list[str] | None, pdf_dir: str | None) -> list[Path]:
    found: list[Path] = []
    seen_resolved: set[Path] = set()

    def _add(candidate: Path) -> None:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file() and resolved.suffix.lower() == ".pdf" and resolved not in seen_resolved:
            seen_resolved.add(resolved)
            found.append(resolved)

    for raw in pdf_args or []:
        p = Path(raw).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Arquivo PDF não encontrado: {p}")
        _add(p)

    if pdf_dir:
        d = Path(pdf_dir).expanduser()
        if not d.is_dir():
            raise NotADirectoryError(f"Diretório de PDFs não encontrado: {d}")
        for candidate in sorted(d.glob("*.pdf")):
            _add(candidate)

    if found:
        return found

    search_dirs = [REPO_ROOT / "data" / "comprovantes", Path.home() / "Downloads"]
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for pattern in AUTO_DISCOVERY_PATTERNS:
            for candidate in sorted(directory.glob(pattern)):
                _add(candidate)

    if not found:
        raise FileNotFoundError(
            "Nenhum PDF oficial informado ou localizado automaticamente. "
            "Passe `--pdf <arquivo1.pdf> <arquivo2.pdf>` ou `--pdf-dir <pasta>`."
        )
    return found


def _write_pre_apply_backup(db: Any, report_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = report_dir / f"backup-before-reconcile-{stamp}.jsonl"
    existing = set(db.list_collection_names())
    with backup_path.open("w", encoding="utf-8") as fh:
        for coll_name in TOUCHED_COLLECTIONS:
            if coll_name not in existing:
                continue
            for row in db[coll_name].find({}):
                envelope = {"collection": coll_name, "document": row}
                fh.write(json_util.dumps(envelope, json_options=json_util.CANONICAL_JSON_OPTIONS) + "\n")
    return backup_path


def _ensure_collections_outside_tx(db: Any) -> None:
    existing = set(db.list_collection_names())
    for coll_name in TOUCHED_COLLECTIONS:
        if coll_name not in existing:
            db.create_collection(coll_name)


def _summarize_plan(docs: list[OfficialStatementDocument], plans: list[Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for p in plans:
        counts[p.status] = counts.get(p.status, 0) + 1
    return {
        "documents": [
            {
                "doc_key": d.doc_key,
                "filename": d.filename,
                "kind": d.kind,
                "institution": d.institution,
                "competencia": d.competencia,
                "total_amount": d.total_amount,
                "status": d.status,
                "paid_date": d.paid_date,
                "closing_balance": d.closing_balance,
                "closing_as_of": d.closing_as_of,
                "active_items": sum(1 for it in d.items if not it.skip_insert),
            }
            for d in docs
        ],
        "plan_counts": {
            "already_reconciled": counts.get("already_reconciled", 0),
            "matched_exact": counts.get("matched_exact", 0),
            "matched_principal": counts.get("matched_principal", 0),
            "missing": counts.get("missing", 0),
            "skipped_prior_payment": counts.get("skipped_prior_payment", 0),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Concilia comprovantes e faturas oficiais (PDF) contra o MongoDB."
    )
    parser.add_argument("--pdf", nargs="+", help="Caminhos para os PDFs oficiais.")
    parser.add_argument("--pdf-dir", help="Diretório contendo os PDFs oficiais.")
    parser.add_argument(
        "--cpf",
        default=os.environ.get("FIN_PDF_CPF"),
        help="CPF (opcional) caso algum PDF esteja criptografado sem extrato aberto no lote.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(REPO_ROOT / "data"),
        help="Diretório local (ignorado pelo Git) para salvar relatórios e backup.",
    )
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Apenas valida e extrai os PDFs localmente sem conectar ao MongoDB.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica as conciliações e inserções no MongoDB (padrão: dry-run seguro).",
    )
    parser.add_argument(
        "--force-balances",
        action="store_true",
        help="Atualiza snapshots de saldo mesmo se houver snapshot mais recente no banco.",
    )
    parser.add_argument(
        "--allow-standalone",
        action="store_true",
        help="Permite execução sem replica set caso o MongoDB local seja standalone.",
    )
    args = parser.parse_args(argv)

    pdf_paths = _discover_pdfs(args.pdf, args.pdf_dir)
    docs = parse_official_pdf_batch(pdf_paths, cpf=args.cpf)

    report_dir = Path(args.report_dir).expanduser().resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    if args.parse_only:
        output = {
            "mode": "parse_only",
            "pdf_count": len(docs),
            "documents": [d.to_dict() for d in docs],
        }
        json_path = report_dir / "reconciliacao-comprovantes-parse.json"
        json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "mode": "parse_only",
                    "pdf_count": len(docs),
                    "active_items": sum(sum(1 for it in d.items if not it.skip_insert) for d in docs),
                    "report_json": str(json_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    os.chdir(REPO_ROOT)
    get_settings.cache_clear()
    settings = get_settings()
    uri = configured_mongodb_uri(settings)

    with MongoClient(uri, serverSelectionTimeoutMS=8000) as client:
        db = client[settings.mongodb_database]
        plans, targets = build_reconciliation_plan(db, docs)

        md_path = report_dir / "reconciliacao-comprovantes-relatorio.md"
        json_path = report_dir / "reconciliacao-comprovantes-relatorio.json"

        result_payload: dict[str, Any] = {
            "mode": "apply" if args.apply else "dry_run",
            "database": settings.mongodb_database,
            **_summarize_plan(docs, plans),
            "report_markdown": str(md_path),
            "report_json": str(json_path),
        }

        if args.apply:
            backup_path = _write_pre_apply_backup(db, report_dir)
            result_payload["backup_jsonl"] = str(backup_path)
            _ensure_collections_outside_tx(db)
            try:
                with mongo_transaction(db) as session:
                    writing = session_database(db, session)
                    lock_movement_insert(writing, session)
                    plans, targets = build_reconciliation_plan(writing, docs)
                    apply_summary = apply_reconciliation_plan(
                        writing,
                        docs,
                        plans,
                        targets,
                        force_balances=args.force_balances,
                    )
            except AtomicUnavailable:
                if not args.allow_standalone:
                    raise
                lock_movement_insert(db)
                plans, targets = build_reconciliation_plan(db, docs)
                apply_summary = apply_reconciliation_plan(
                    db,
                    docs,
                    plans,
                    targets,
                    force_balances=args.force_balances,
                )
            result_payload.update(_summarize_plan(docs, plans))
            result_payload["applied"] = apply_summary

        md_path.write_text(format_markdown_report(docs, plans, targets), encoding="utf-8")
        detailed_json = {
            **result_payload,
            "plans": [
                {
                    "item_key": p.item.item_key,
                    "date": p.item.date,
                    "purchase_date_hint": p.item.purchase_date_hint,
                    "target": p.item.target,
                    "official_description": p.item.description,
                    "popular_vendor": p.item.popular_vendor,
                    "official_amount": p.item.amount,
                    "principal_amount": p.item.principal_amount,
                    "fee_amount": p.item.fee_amount,
                    "status": p.status,
                    "score": p.score,
                    "reason": p.reason,
                    "matched_identity": p.candidate.identity if p.candidate else None,
                    "matched_source": p.candidate.source if p.candidate else None,
                    "matched_label": p.candidate.display_label if p.candidate else None,
                    "matched_date": p.candidate.date.isoformat() if p.candidate else None,
                    "matched_amount": str(p.candidate.amount) if p.candidate else None,
                    "insert_missing_fee": p.insert_missing_fee,
                }
                for p in plans
            ],
        }
        json_path.write_text(json.dumps(detailed_json, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
