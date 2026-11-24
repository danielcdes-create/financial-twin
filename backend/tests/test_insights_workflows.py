"""Comprehensive FastAPI TestClient suite for backend/app/insights.py."""
import copy
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from app import insights
from app.auth import require_session
from test_ingestion import Collection, FakeDB


class WorkflowCollection(Collection):
    def insert_one(self, doc, **kw):
        item = copy.deepcopy(doc)
        item.setdefault("_id", ObjectId())
        if any(existing.get("_id") == item["_id"] for existing in self.docs):
            raise DuplicateKeyError(f"Duplicate _id: {item['_id']!r}")
        if item.get("event_id") and self.find_one({"event_id": item["event_id"]}):
            raise DuplicateKeyError(f"Duplicate event_id: {item['event_id']!r}")
        doc.setdefault("_id", item["_id"])
        self.docs.append(item)
        return type("InsertOneResult", (), {"inserted_id": item["_id"]})()


class WorkflowDB(FakeDB):
    def __missing__(self, key):
        self[key] = WorkflowCollection()
        return self[key]


class InsightsWorkflowsTests(unittest.TestCase):
    def setUp(self):
        self.today_patch = patch("app.insights.today", return_value=date(2026, 10, 20))
        self.today_patch.start()
        self.addCleanup(self.today_patch.stop)

        self.db = WorkflowDB()
        self.app = FastAPI()
        self.app.include_router(insights.router)
        self.app.state.db = self.db
        self.app.dependency_overrides[require_session] = lambda: {"session_id": "test-session"}
        self.client = TestClient(self.app)

    def _seed_ledger_expense(
        self,
        valor: str = "-50.00",
        dt: datetime = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc),
        *,
        categoria: str = "Alimentação",
        descricao: str = "SUPERMERCADO",
        migration_reason: str | None = "despesa_real",
        extra: dict | None = None,
    ) -> dict:
        oid = ObjectId()
        doc = {
            "_id": oid,
            "data_evento": dt,
            "competencia": dt.strftime("%Y-%m"),
            "valor": Decimal128(valor),
            "moeda": "BRL",
            "fluxo": "saida" if Decimal(valor) < 0 else "entrada",
            "tipo": "compra",
            "descricao_original": descricao,
            "categoria": categoria,
        }
        if migration_reason:
            doc["classification"] = {"migration_reason": migration_reason}
        if extra:
            doc.update(extra)
        self.db.transactions.insert_one(doc)
        return self.db.transactions.find_one({"_id": oid})

    def test_closure_period_validation_gaps_and_preview_truncation(self):
        # Period > 367 days and inverted range -> 422
        r_long = self.client.get("/v1/insights/closure?start=2025-01-01&end=2026-01-05")
        self.assertEqual(r_long.status_code, 422)
        r_inv = self.client.get("/v1/insights/closure?start=2026-10-20&end=2026-10-01")
        self.assertEqual(r_inv.status_code, 422)

        # Seed: 1 unknown-role ledger row, 1pending review, 1 confirmed capture missing account_profile_id,
        # plus 103 valid confirmed expenses => 105 total rows (>100 => preview_truncated=True)
        self._seed_ledger_expense(
            "-15.00",
            datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
            migration_reason=None,
        )
        self.db.provisional_transactions.insert_one({
            "_id": ObjectId(),
            "provisional_id": "cap-no-acc",
            "status": "confirmed",
            "data_evento": datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
            "valor": Decimal128("-25.00"),
            "fluxo": "saida",
            "tipo": "compra",
            "merchant_raw": "PADARIA SEM CONTA",
            "categoria_sugerida": "Alimentação",
        })
        self.db.review_queue.insert_one({"_id": ObjectId(), "review_id": "rev-1", "status": "pending"})

        for i in range(103):
            self._seed_ledger_expense(
                "-10.00",
                datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
                descricao=f"COMPRA {i}",
            )

        res = self.client.get("/v1/insights/closure?start=2026-10-01&end=2026-10-31")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["movement_count"], 105)
        self.assertTrue(body["preview_truncated"])
        self.assertEqual(len(body["trail_preview"]), 100)
        self.assertEqual(body["expenses"], "1055.00")

        joined_gaps = " | ".join(body["gaps"])
        self.assertIn("capturas confirmadas sem conta atribuída", joined_gaps)
        self.assertIn("efeito econômico não determinado", joined_gaps)
        self.assertIn("revisões aguardam decisão", joined_gaps)
        self.assertIn("Existem contas sem referência de saldo.", joined_gaps)

    def test_trail_graph_edges_missing_refs_and_error_codes(self):
        # Invalid ref -> 422; missing movement -> 404
        self.assertEqual(self.client.get("/v1/insights/trail?reference=invalid:123").status_code, 422)
        self.assertEqual(self.client.get("/v1/insights/trail?reference=ledger:not-an-oid").status_code, 422)
        self.assertEqual(self.client.get(f"/v1/insights/trail?reference=ledger:{ObjectId()}").status_code, 404)
        self.assertEqual(self.client.get("/v1/insights/trail?reference=capture:missing-prov").status_code, 404)

        src_rec_id = ObjectId()
        missing_bill_id = ObjectId()
        obl_id = ObjectId()
        missing_plan_id = ObjectId()

        self.db.source_records.insert_one({"_id": src_rec_id, "source_workbook": "extrato.xlsx"})
        self.db.raw_events.insert_one({"_id": ObjectId(), "event_id": "evt-origin", "source_type": "receipt"})
        self.db.raw_events.insert_one({"_id": ObjectId(), "event_id": "evt-support", "source_type": "android_notification"})
        self.db.obligations.insert_one({"_id": obl_id, "tipo": "boleto", "saldo_aberto": Decimal128("0.00")})

        tx = self._seed_ledger_expense(
            "-120.00",
            extra={
                "event_id": "evt-origin",
                "source": {"source_record_id": src_rec_id, "identificador_original": "r1", "fonte": "import"},
                "bill_id": missing_bill_id,  # Deliberately missing to verify missing ref reporting
            },
        )
        self.db.transaction_evidence.insert_one({
            "_id": ObjectId(),
            "transaction_id": tx["_id"],
            "event_id": "evt-support",
            "decision": "confirm_match",
        })
        self.db.transaction_evidence.insert_one({
            "_id": ObjectId(),
            "transaction_id": tx["_id"],
            "event_id": "evt-reversed",
            "status": "reversed",
        })
        self.db.commitments.insert_one({
            "_id": ObjectId(),
            "commitment_id": "comm-1",
            "name": "Internet Fibra",
            "linked_transactions": [{"transaction_id": str(tx["_id"])}],
        })
        self.db.reconciliations.insert_one({
            "_id": ObjectId(),
            "tipo": "pagamento_obrigacao",
            "origem": {"entity": "transactions", "id": tx["_id"]},
            "destino": {"entity": "installment_plans", "id": missing_plan_id},
            "obligation_id": obl_id,
        })

        resp = self.client.get(f"/v1/insights/trail?reference=ledger:{tx['_id']}")
        self.assertEqual(resp.status_code, 200)
        trail_data = resp.json()
        self.assertEqual(trail_data["economic_role"], "expense")

        node_kinds = {n["kind"] for n in trail_data["nodes"]}
        self.assertTrue({"ledger", "raw_event", "source_record", "evidence", "commitment", "reconciliation", "obligations"}.issubset(node_kinds))
        # Reversed evidence must not appear
        self.assertFalse(any(n["data"].get("event_id") == "evt-reversed" for n in trail_data["nodes"]))

        missing_kinds = {m["kind"] for m in trail_data["missing"]}
        self.assertEqual(missing_kinds, {"bill", "installment_plans"})

    def test_undo_evidence_reopen_synthesize_idempotency_and_all_conflict_guards(self):
        # 404 when active evidence does not exist
        r_404 = self.client.post(
            "/v1/insights/evidence/missing-evt/undo",
            json={"request_id": str(uuid4()), "expected_revision": 1, "reason": "Vínculo incorreto"},
        )
        self.assertEqual(r_404.status_code, 404)

        # Case A: Existing draft in provisional_transactions is reopened + idempotent replay + payload conflict
        tx_a = self._seed_ledger_expense("-40.00")
        self.db.raw_events.insert_one({
            "_id": ObjectId(),
            "event_id": "evt-a",
            "captured_at": datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
            "processing_status": "matched",
            "parsed_payload": {"amount": "40.00", "direction": "expense", "merchant_raw": "PADARIA A"},
        })
        self.db.provisional_transactions.insert_one({
            "_id": ObjectId(),
            "provisional_id": "prov-a",
            "event_id": "evt-a",
            "status": "enriched",
            "revision": 2,
            "valor": Decimal128("-40.00"),
        })
        self.db.transaction_evidence.insert_one({
            "_id": ObjectId(),
            "event_id": "evt-a",
            "transaction_id": tx_a["_id"],
            "revision": 1,
        })

        # Revision mismatch -> 409
        r_rev = self.client.post(
            "/v1/insights/evidence/evt-a/undo",
            json={"request_id": str(uuid4()), "expected_revision": 9, "reason": "Revisão defasada"},
        )
        self.assertEqual(r_rev.status_code, 409)

        req_a = str(uuid4())
        r_ok = self.client.post(
            "/v1/insights/evidence/evt-a/undo",
            json={"request_id": req_a, "expected_revision": 1, "reason": "Desfazer conciliação manual"},
        )
        self.assertEqual(r_ok.status_code, 200)
        self.assertEqual(r_ok.json(), {"event_id": "evt-a", "status": "reopened", "provisional_id": "prov-a"})
        self.assertEqual(self.db.provisional_transactions.find_one({"provisional_id": "prov-a"})["status"], "provisional")
        self.assertEqual(self.db.provisional_transactions.find_one({"provisional_id": "prov-a"})["revision"], 3)
        self.assertEqual(self.db.transaction_evidence.find_one({"event_id": "evt-a"})["status"], "reversed")

        # Idempotent replay with identical request_id + event_id + reason
        r_replay = self.client.post(
            "/v1/insights/evidence/evt-a/undo",
            json={"request_id": req_a, "expected_revision": 1, "reason": "Desfazer conciliação manual"},
        )
        self.assertEqual(r_replay.status_code, 200)
        self.assertEqual(r_replay.json(), r_ok.json())

        # Reusing request_id with different reason -> 409
        r_mismatch = self.client.post(
            "/v1/insights/evidence/evt-a/undo",
            json={"request_id": req_a, "expected_revision": 1, "reason": "Outro motivo diferente"},
        )
        self.assertEqual(r_mismatch.status_code, 409)

        # Case B: Synthesizes draft from raw_events when provisional_transactions has no row
        tx_b = self._seed_ledger_expense("-65.90")
        self.db.raw_events.insert_one({
            "_id": ObjectId(),
            "event_id": "evt-synth",
            "captured_at": datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc),
            "processing_status": "matched",
            "parsed_payload": {"amount": "65.90", "direction": "expense", "merchant_raw": "FARMACIA SINTETICA"},
        })
        self.db.transaction_evidence.insert_one({
            "_id": ObjectId(),
            "event_id": "evt-synth",
            "transaction_id": tx_b["_id"],
            "revision": 1,
        })
        req_b = str(uuid4())
        r_synth = self.client.post(
            "/v1/insights/evidence/evt-synth/undo",
            json={"request_id": req_b, "expected_revision": 1, "reason": "Reabrir a partir do evento bruto"},
        )
        self.assertEqual(r_synth.status_code, 200)
        synth_doc = self.db.provisional_transactions.find_one({"provisional_id": req_b})
        self.assertIsNotNone(synth_doc)
        self.assertEqual(synth_doc["valor"].to_decimal(), Decimal("-65.90"))
        self.assertEqual(synth_doc["status"], "provisional")

        # Case C: Guardrails -> deleted movement (409), linked commitment (409), missing raw_event (409)
        tx_del = ObjectId()
        self.db.deleted_movements.insert_one({"_id": f"ledger:{tx_del}"})
        self.db.transaction_evidence.insert_one({"_id": ObjectId(), "event_id": "evt-del", "transaction_id": tx_del, "revision": 1})
        self.assertEqual(
            self.client.post(
                "/v1/insights/evidence/evt-del/undo",
                json={"request_id": str(uuid4()), "expected_revision": 1, "reason": "Tentativa em excluído"},
            ).status_code,
            409,
        )

        tx_comm = self._seed_ledger_expense("-80.00")
        self.db.commitments.insert_one({
            "_id": ObjectId(),
            "commitment_id": "c-guard",
            "linked_transactions": [{"transaction_id": str(tx_comm["_id"])}],
        })
        self.db.transaction_evidence.insert_one({"_id": ObjectId(), "event_id": "evt-comm", "transaction_id": tx_comm["_id"], "revision": 1})
        self.assertEqual(
            self.client.post(
                "/v1/insights/evidence/evt-comm/undo",
                json={"request_id": str(uuid4()), "expected_revision": 1, "reason": "Tentativa com compromisso"},
            ).status_code,
            409,
        )

        tx_noraw = self._seed_ledger_expense("-30.00")
        self.db.transaction_evidence.insert_one({"_id": ObjectId(), "event_id": "evt-noraw", "transaction_id": tx_noraw["_id"], "revision": 1})
        self.assertEqual(
            self.client.post(
                "/v1/insights/evidence/evt-noraw/undo",
                json={"request_id": str(uuid4()), "expected_revision": 1, "reason": "Sem raw_event"},
            ).status_code,
            409,
        )

    def test_financial_cases_pagination_resolution_refund_claim_immutability_and_dossier(self):
        charge = self._seed_ledger_expense("-99.90", descricao="COBRANCA INDEVIDA STREAMING")
        refund_tx = self._seed_ledger_expense(
            "99.90",
            descricao="ESTORNO STREAMING",
            migration_reason="estorno",
            extra={"tipo": "estorno", "natureza": "ESTORNO"},
        )

        # Create 3 cases to verify cursor pagination + idempotent replay
        case_ids = []
        for idx in range(3):
            cid = str(uuid4())
            case_ids.append(cid)
            payload = {
                "request_id": cid,
                "title": f"Cobrança pós-cancelamento #{idx + 1}",
                "kind": "post_cancellation",
                "expected_amount": "99.90",
                "expected_date": "2026-10-15",
                "source_refs": [f"ledger:{charge['_id']}"],
                "notes": "Protocolo aberto",
            }
            created = self.client.post("/v1/insights/cases", json=payload)
            self.assertEqual(created.status_code, 201)
            if idx == 0:
                # Idempotent replay
                replay = self.client.post("/v1/insights/cases", json=payload)
                self.assertEqual(replay.status_code, 201)
                self.assertEqual(replay.json()["id"], cid)
                # Hash mismatch on same request_id -> 409
                conflict = self.client.post("/v1/insights/cases", json={**payload, "expected_amount": "50.00"})
                self.assertEqual(conflict.status_code, 409)

        # Cursor pagination: limit=2 -> Page 1 (2 items) -> Page 2 (1 item)
        p1 = self.client.get("/v1/insights/cases?limit=2").json()
        self.assertEqual(len(p1["items"]), 2)
        self.assertTrue(p1["has_more"])
        p2 = self.client.get(f"/v1/insights/cases?limit=2&cursor={p1['next_cursor']}").json()
        self.assertEqual(len(p2["items"]), 1)
        self.assertFalse(p2["has_more"])

        target_case = case_ids[0]
        second_case = case_ids[1]

        # Resolve without refund_ref -> 422
        r_no_ref = self.client.patch(
            f"/v1/insights/cases/{target_case}",
            json={"revision": 1, "status": "resolved", "notes": "Sem crédito"},
        )
        self.assertEqual(r_no_ref.status_code, 422)

        # Resolve pointing to an expense (non-refund) -> 422
        r_not_refund = self.client.patch(
            f"/v1/insights/cases/{target_case}",
            json={"revision": 1, "status": "resolved", "refund_ref": f"ledger:{charge['_id']}"},
        )
        self.assertEqual(r_not_refund.status_code, 422)

        # Resolve with valid positive refund -> 200
        refund_ref = f"ledger:{refund_tx['_id']}"
        r_resolved = self.client.patch(
            f"/v1/insights/cases/{target_case}",
            json={"revision": 1, "status": "resolved", "refund_ref": refund_ref, "notes": "Estornado na fatura"},
        )
        self.assertEqual(r_resolved.status_code, 200)
        self.assertEqual(r_resolved.json()["recovered_amount"], "99.90")
        self.assertEqual(r_resolved.json()["revision"], 2)

        # Second case attempting to claim the same refund_ref -> 409
        r_double_claim = self.client.patch(
            f"/v1/insights/cases/{second_case}",
            json={"revision": 1, "status": "resolved", "refund_ref": refund_ref},
        )
        self.assertEqual(r_double_claim.status_code, 409)

        # Resolved case: idempotent no-op with identical fields returns 200, mutation returns 409
        r_noop = self.client.patch(
            f"/v1/insights/cases/{target_case}",
            json={"revision": 2, "status": "resolved", "refund_ref": refund_ref, "notes": "Estornado na fatura"},
        )
        self.assertEqual(r_noop.status_code, 200)

        r_immutable = self.client.patch(
            f"/v1/insights/cases/{target_case}",
            json={"revision": 2, "status": "open", "refund_ref": refund_ref, "notes": "Reabrir"},
        )
        self.assertEqual(r_immutable.status_code, 409)

        # Dossier includes markdown, sources, history, and reports missing sources if a ref disappears
        self.db.transactions.delete_one({"_id": charge["_id"]})
        dos = self.client.get(f"/v1/insights/cases/{target_case}/dossier")
        self.assertEqual(dos.status_code, 200)
        dos_body = dos.json()
        self.assertFalse(dos_body["sent_externally"])
        self.assertIn("Valor recuperado comprovado: R$ 99.90", dos_body["markdown"])
        self.assertIn(refund_ref, dos_body["markdown"])
        self.assertEqual(dos_body["missing"], [f"ledger:{charge['_id']}"])
        self.assertIn("Fontes ausentes:", dos_body["markdown"])

    def test_guardian_detections_covers_all_four_anomaly_kinds(self):
        tx_cancel = self._seed_ledger_expense("-39.90", datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc), descricao="ACADEMIA CANCELADA")
        tx_p1 = self._seed_ledger_expense("-100.00", datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc), descricao="CURSO 1/2")
        tx_p2 = self._seed_ledger_expense("-100.00", datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc), descricao="CURSO 2/2")
        tx_p3 = self._seed_ledger_expense("-100.00", datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc), descricao="CURSO EXTRA")
        tx_spike = self._seed_ledger_expense("-65.00", datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc), descricao="PLANO FIXO")

        # 1. post_cancellation
        self.db.commitments.insert_one({
            "_id": ObjectId(),
            "commitment_id": "c-cancel",
            "name": "Academia Cancelada",
            "kind": "assinatura",
            "status": "cancelado",
            "cancelled_at": datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
            "amount": "39.90",
            "linked_transactions": [{"transaction_id": str(tx_cancel["_id"]), "paid_date": "2026-10-12"}],
        })
        # 2. extra_installment (installment_count=2, 3 linked payments)
        self.db.commitments.insert_one({
            "_id": ObjectId(),
            "commitment_id": "c-extra",
            "name": "Curso 2x",
            "kind": "parcelamento",
            "status": "ativo",
            "installment_count": 2,
            "amount": "100.00",
            "linked_transactions": [
                {"transaction_id": str(tx_p1["_id"]), "paid_date": "2026-08-10"},
                {"transaction_id": str(tx_p2["_id"]), "paid_date": "2026-09-10"},
                {"transaction_id": str(tx_p3["_id"]), "paid_date": "2026-10-10"},
            ],
        })
        # 3. unexpected_amount (fixed 50.00, charged 65.00)
        self.db.commitments.insert_one({
            "_id": ObjectId(),
            "commitment_id": "c-spike",
            "name": "Plano Fixo",
            "kind": "assinatura",
            "status": "ativo",
            "amount": "50.00",
            "linked_transactions": [{"transaction_id": str(tx_spike["_id"]), "paid_date": "2026-10-14"}],
        })
        # 4. overdue missing_refund (today is patched to 2026-10-20)
        self.db.financial_cases.insert_one({
            "_id": "case-overdue-1",
            "title": "Reembolso Atrasado",
            "status": "awaiting_refund",
            "expected_date": "2026-10-10",
        })

        resp = self.client.get("/v1/insights/guardian-detections?start=2026-10-01&end=2026-10-31")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["writes_performed"])
        kinds = {item["kind"] for item in data["items"]}
        self.assertEqual(kinds, {"post_cancellation", "extra_installment", "unexpected_amount", "missing_refund"})

    def test_saving_experiments_lifecycle_lock_validation_and_daily_rate_report(self):
        # Coherent period validation -> 422 (overlapping baseline & experiment, or >91 days)
        bad_overlap = self.client.post(
            "/v1/insights/experiments",
            json={
                "request_id": str(uuid4()),
                "title": "Reduzir Delivery",
                "category": "Alimentação",
                "protocol": "Cozinhar em casa nos dias úteis",
                "baseline_start": "2026-09-01",
                "baseline_end": "2026-10-05",
                "start": "2026-10-01",
                "end": "2026-10-15",
                "target_reduction_pct": "25",
            },
        )
        self.assertEqual(bad_overlap.status_code, 422)

        bad_len = self.client.post(
            "/v1/insights/experiments",
            json={
                "request_id": str(uuid4()),
                "title": "Experimento longo demais",
                "category": "Alimentação",
                "protocol": "Cozinhar em casa nos dias úteis",
                "baseline_start": "2026-08-01",
                "baseline_end": "2026-08-31",
                "start": "2026-09-01",
                "end": "2026-12-15",
                "target_reduction_pct": "25",
            },
        )
        self.assertEqual(bad_len.status_code, 422)

        # Future experiment: cannot be completed before start date (today=2026-10-20, start=2026-11-01)
        future_exp_id = str(uuid4())
        future_payload = {
            "request_id": future_exp_id,
            "title": "Experimento Novembro",
            "category": "Alimentação",
            "protocol": "Sem pedidos por aplicativo",
            "baseline_start": "2026-10-01",
            "baseline_end": "2026-10-15",
            "start": "2026-11-01",
            "end": "2026-11-15",
            "target_reduction_pct": "30",
        }
        r_future = self.client.post("/v1/insights/experiments", json=future_payload)
        self.assertEqual(r_future.status_code, 201)
        # Idempotent replay on same request_id succeeds even while experiment_locks holds 'owner'
        self.assertEqual(self.client.post("/v1/insights/experiments", json=future_payload).status_code, 201)

        # Second active experiment with different request_id -> 409 lock conflict
        r_locked = self.client.post(
            "/v1/insights/experiments",
            json={**future_payload, "request_id": str(uuid4())},
        )
        self.assertEqual(r_locked.status_code, 409)

        # Completing before start date -> 422
        r_early = self.client.patch(
            f"/v1/insights/experiments/{future_exp_id}",
            json={"revision": 1, "status": "completed", "satisfaction": 4},
        )
        self.assertEqual(r_early.status_code, 422)

        # Cancelling releases experiment_locks -> 200, and second patch -> 409 already closed
        r_cancel = self.client.patch(
            f"/v1/insights/experiments/{future_exp_id}",
            json={"revision": 1, "status": "cancelled", "confounders": "Reagendado"},
        )
        self.assertEqual(r_cancel.status_code, 200)
        self.assertEqual(self.db.experiment_locks.docs, [])
        self.assertEqual(
            self.client.patch(
                f"/v1/insights/experiments/{future_exp_id}",
                json={"revision": 2, "status": "cancelled"},
            ).status_code,
            409,
        )

        # Active October experiment + report calculation:
        # Baseline 2026-09-01..2026-09-10 (10 days): R$ 200.00 in Alimentação => R$ 20.00/day
        # Experiment 2026-10-01..2026-10-10 (10 days): R$ 150.00 in Alimentação (+ R$ 80.00 in Transporte ignored)
        self._seed_ledger_expense("-120.00", datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc), categoria="Alimentação")
        self._seed_ledger_expense("-80.00", datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc), categoria="Alimentação")
        self._seed_ledger_expense("-150.00", datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc), categoria="Alimentação")
        self._seed_ledger_expense("-80.00", datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc), categoria="Transporte")

        exp_id = str(uuid4())
        r_create = self.client.post(
            "/v1/insights/experiments",
            json={
                "request_id": exp_id,
                "title": "Redução Alimentação Outubro",
                "category": "Alimentação",
                "protocol": "Marmitas planejadas",
                "baseline_start": "2026-09-01",
                "baseline_end": "2026-09-10",
                "start": "2026-10-01",
                "end": "2026-10-10",
                "target_reduction_pct": "20",
            },
        )
        self.assertEqual(r_create.status_code, 201)

        listed = self.client.get("/v1/insights/experiments?limit=10").json()
        self.assertEqual(listed["total"], 2)

        report = self.client.get(f"/v1/insights/experiments/{exp_id}/report")
        self.assertEqual(report.status_code, 200)
        rep = report.json()
        self.assertEqual(rep["baseline_spending"], "200.00")
        self.assertEqual(rep["observed_spending"], "150.00")
        self.assertEqual(rep["baseline_days"], 10)
        self.assertEqual(rep["observed_days"], 10)
        self.assertEqual(rep["expected_at_baseline_daily_rate"], "200.00")
        self.assertEqual(rep["observed_difference"], "50.00")
        self.assertEqual(rep["observed_reduction_pct"], "25.00")

        # Complete experiment -> releases lock
        r_done = self.client.patch(
            f"/v1/insights/experiments/{exp_id}",
            json={"revision": 1, "status": "completed", "satisfaction": 5},
        )
        self.assertEqual(r_done.status_code, 200)
        self.assertEqual(r_done.json()["status"], "completed")
        self.assertEqual(self.db.experiment_locks.docs, [])


if __name__ == "__main__":
    unittest.main()
