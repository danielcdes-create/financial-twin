"""
Módulo de Telemetria e Observabilidade de Performance para o Backend (FastAPI + PyMongo).
Salve em: backend/app/telemetry.py

Funcionalidades:
1. Rastreamento por requisição via ContextVars (correlaciona X-Trace-Id, X-Client-Action e X-Client-Tab vindos do app mobile).
2. Listener nativo do PyMongo (CommandListener) que mede cada operação no banco (find, count, distinct, aggregate, update, commitTransaction) sem precisar alterar cada chamada de banco.
3. Detecção automática de N+1 queries, leituras pesadas e overhead de mongo_snapshot.
4. Injeção de cabeçalhos HTTP `Server-Timing` e `X-Perf-Metrics` em cada resposta para que o cliente mobile saiba exatamente quanto tempo foi Rede vs. Python vs. MongoDB.
5. Coleta de eventos do app mobile (`POST /v1/telemetry/events`) e endpoint de diagnóstico (`GET /v1/telemetry/report`) com percentis P50/P95/P99 e evolução histórica para ciclos de melhoria contínua (Kaizen).
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, Field
from pymongo import monitoring

from app.auth import require_session

logger = logging.getLogger("financial_twin.telemetry")

SLOW_REQUEST_THRESHOLD_MS = float(os.getenv("PERF_SLOW_REQUEST_MS", "300"))
SLOW_QUERY_THRESHOLD_MS = float(os.getenv("PERF_SLOW_QUERY_MS", "50"))
N_PLUS_ONE_THRESHOLD = int(os.getenv("PERF_N_PLUS_ONE_COUNT", "4"))
TELEMETRY_LOG_PATH = Path(os.getenv("PERF_LOG_PATH", "logs/perf_telemetry.jsonl"))


@dataclass
class DbCommandMetric:
    command_name: str
    collection: str
    duration_ms: float
    status: Literal["ok", "failed"]
    request_id: int
    docs_returned: int | None = None


@dataclass
class RequestTrace:
    trace_id: str
    method: str
    path: str
    client_action: str = ""
    client_tab: str = ""
    started_at_ns: int = field(default_factory=time.perf_counter_ns)
    db_commands: list[DbCommandMetric] = field(default_factory=list)
    custom_spans: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_db_ms(self) -> float:
        return round(sum(cmd.duration_ms for cmd in self.db_commands), 2)

    @property
    def db_call_count(self) -> int:
        return len(self.db_commands)

    def n_plus_one_alerts(self) -> list[str]:
        counts: dict[str, int] = defaultdict(int)
        for cmd in self.db_commands:
            if cmd.command_name in {"find", "count", "distinct", "aggregate", "update", "insert"}:
                counts[f"{cmd.collection}.{cmd.command_name}"] += 1
        return [
            f"{signature} ({count}x)"
            for signature, count in counts.items()
            if count >= N_PLUS_ONE_THRESHOLD
        ]


_current_trace: ContextVar[RequestTrace | None] = ContextVar("_current_trace", default=None)


class SpanTimer:
    """Context manager para medir blocos internos de código Python (ex.: enriquecimento, cache, parsing)."""

    def __init__(self, name: str, **metadata: Any) -> None:
        self.name = name
        self.metadata = metadata
        self.start_ns = 0

    def __enter__(self) -> SpanTimer:
        self.start_ns = time.perf_counter_ns()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration_ms = round((time.perf_counter_ns() - self.start_ns) / 1_000_000, 2)
        trace = _current_trace.get()
        if trace is not None:
            trace.custom_spans.append({
                "span": self.name,
                "duration_ms": duration_ms,
                "status": "error" if exc_type else "ok",
                **self.metadata,
            })


class MongoPerformanceListener(monitoring.CommandListener):
    """Intercepta todos os comandos PyMongo e associa à requisição HTTP ativa via ContextVar."""

    _MONITORED_COMMANDS = {
        "find",
        "aggregate",
        "count",
        "distinct",
        "insert",
        "update",
        "delete",
        "findAndModify",
        "getMore",
        "commitTransaction",
        "abortTransaction",
    }

    def __init__(self) -> None:
        self._collections_by_req_id: dict[int, str] = {}
        self._lock = Lock()

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        if event.command_name not in self._MONITORED_COMMANDS:
            return
        trace = _current_trace.get()
        if trace is None:
            return
        collection = str(event.command.get(event.command_name) or "session")
        with self._lock:
            self._collections_by_req_id[event.request_id] = collection

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        if event.command_name not in self._MONITORED_COMMANDS:
            return
        trace = _current_trace.get()
        if trace is None:
            return
        with self._lock:
            collection = self._collections_by_req_id.pop(event.request_id, "unknown")
        duration_ms = round(event.duration_micros / 1000.0, 2)
        docs_returned = None
        cursor = event.reply.get("cursor")
        if isinstance(cursor, dict):
            batch = cursor.get("firstBatch") or cursor.get("nextBatch")
            if isinstance(batch, list):
                docs_returned = len(batch)
        trace.db_commands.append(
            DbCommandMetric(
                command_name=event.command_name,
                collection=collection,
                duration_ms=duration_ms,
                status="ok",
                request_id=event.request_id,
                docs_returned=docs_returned,
            )
        )

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        if event.command_name not in self._MONITORED_COMMANDS:
            return
        trace = _current_trace.get()
        if trace is None:
            return
        with self._lock:
            collection = self._collections_by_req_id.pop(event.request_id, "unknown")
        duration_ms = round(event.duration_micros / 1000.0, 2)
        trace.db_commands.append(
            DbCommandMetric(
                command_name=event.command_name,
                collection=collection,
                duration_ms=duration_ms,
                status="failed",
                request_id=event.request_id,
            )
        )


class TelemetryAggregator:
    """Mantém janela móvel em memória e grava JSONL estruturado para acompanhamento histórico."""

    def __init__(self, max_samples_per_key: int = 500) -> None:
        self.max_samples = max_samples_per_key
        self._lock = Lock()
        self._backend_routes: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self.max_samples)
        )
        self._client_events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self.max_samples)
        )
        TELEMETRY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def record_backend(self, record: dict[str, Any]) -> None:
        key = f"{record['method']} {record['route']}"
        with self._lock:
            self._backend_routes[key].append(record)
        self._append_jsonl({"source": "backend", **record})

    def record_client_batch(self, events: list[dict[str, Any]]) -> int:
        with self._lock:
            for ev in events:
                key = f"{ev.get('category', 'unknown')}:{ev.get('name', 'unknown')}"
                self._client_events[key].append(ev)
        for ev in events:
            self._append_jsonl({"source": "mobile", **ev})
        return len(events)

    def _append_jsonl(self, payload: dict[str, Any]) -> None:
        try:
            with TELEMETRY_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            logger.debug("Telemetry JSONL write skipped: %s", exc)

    @staticmethod
    def _percentiles(values: list[float]) -> dict[str, float]:
        if not values:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0, "avg_ms": 0.0}
        ordered = sorted(values)
        n = len(ordered)
        p50 = ordered[int(0.50 * (n - 1))]
        p95 = ordered[int(0.95 * (n - 1))]
        p99 = ordered[int(0.99 * (n - 1))]
        return {
            "count": n,
            "avg_ms": round(statistics.fmean(ordered), 2),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "max_ms": round(ordered[-1], 2),
        }

    def build_report(self) -> dict[str, Any]:
        with self._lock:
            backend_summary = {}
            for route, samples in self._backend_routes.items():
                items = list(samples)
                totals = [s["total_ms"] for s in items]
                db_times = [s["db_ms"] for s in items]
                db_calls = [s["db_calls"] for s in items]
                n_plus_one_hits = sum(1 for s in items if s.get("n_plus_one"))
                backend_summary[route] = {
                    "latency": self._percentiles(totals),
                    "db_latency": self._percentiles(db_times),
                    "avg_db_calls": round(statistics.fmean(db_calls), 1) if db_calls else 0.0,
                    "max_db_calls": max(db_calls, default=0),
                    "n_plus_one_occurrences": n_plus_one_hits,
                    "slow_requests": sum(1 for t in totals if t >= SLOW_REQUEST_THRESHOLD_MS),
                }

            client_summary = {}
            for key, samples in self._client_events.items():
                items = list(samples)
                durations = [float(s.get("duration_ms", 0.0)) for s in items if s.get("duration_ms") is not None]
                failures = sum(1 for s in items if s.get("status") not in ("ok", None))
                client_summary[key] = {
                    **self._percentiles(durations),
                    "error_or_abort_count": failures,
                }

        ranked_backend = dict(
            sorted(
                backend_summary.items(),
                key=lambda item: item[1]["latency"]["p95_ms"],
                reverse=True,
            )
        )
        ranked_client = dict(
            sorted(
                client_summary.items(),
                key=lambda item: item[1]["p95_ms"],
                reverse=True,
            )
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "thresholds": {
                "slow_request_ms": SLOW_REQUEST_THRESHOLD_MS,
                "slow_query_ms": SLOW_QUERY_THRESHOLD_MS,
                "n_plus_one_count": N_PLUS_ONE_THRESHOLD,
            },
            "backend_routes_by_p95": ranked_backend,
            "mobile_events_by_p95": ranked_client,
        }


aggregator = TelemetryAggregator()
_mongo_listener_registered = False
_mongo_listener_lock = Lock()


def register_mongo_telemetry() -> None:
    """Registra o listener global do PyMongo uma única vez no startup."""
    global _mongo_listener_registered
    with _mongo_listener_lock:
        if not _mongo_listener_registered:
            monitoring.register(MongoPerformanceListener())
            _mongo_listener_registered = True


router = APIRouter(prefix="/v1/telemetry", dependencies=[Depends(require_session)])


class ClientTelemetryEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str
    category: Literal[
        "navigation",
        "transition",
        "action",
        "data_fetch",
        "data_capture",
        "data_mutation",
        "queue_sync",
    ]
    name: str = Field(min_length=1, max_length=160)
    duration_ms: float | None = None
    status: Literal["ok", "error", "aborted", "blocked", "cache_hit", "deduped"] = "ok"
    tab: str | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClientTelemetryBatch(BaseModel):
    events: list[ClientTelemetryEvent] = Field(min_length=1, max_length=200)


@router.post("/events")
def ingest_client_telemetry(batch: ClientTelemetryBatch):
    count = aggregator.record_client_batch([ev.model_dump(mode="json") for ev in batch.events])
    return {"accepted": count}


@router.get("/report")
def get_telemetry_report():
    """Retorna o pareto de gargalos (Backend + Mobile) com P50, P95, P99 e alertas de N+1."""
    return aggregator.build_report()


def install_telemetry(app: FastAPI) -> None:
    """
    Instala o middleware HTTP, o CommandListener do PyMongo e as rotas de telemetria no FastAPI.
    Basta chamar `install_telemetry(app)` em `backend/app/main.py`.
    """
    register_mongo_telemetry()
    app.include_router(router)

    @app.middleware("http")
    async def performance_telemetry_middleware(request: Request, call_next):
        if request.url.path in {"/live", "/v1/telemetry/events"}:
            return await call_next(request)

        trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        client_action = request.headers.get("X-Client-Action") or ""
        client_tab = request.headers.get("X-Client-Tab") or ""

        trace = RequestTrace(
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            client_action=client_action,
            client_tab=client_tab,
        )
        token = _current_trace.set(trace)
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            total_ms = round((time.perf_counter_ns() - trace.started_at_ns) / 1_000_000, 2)
            db_ms = trace.total_db_ms
            app_ms = round(max(0.0, total_ms - db_ms), 2)
            n_plus_one = trace.n_plus_one_alerts()
            slow_queries = [
                {
                    "op": f"{c.collection}.{c.command_name}",
                    "ms": c.duration_ms,
                    "docs": c.docs_returned,
                }
                for c in trace.db_commands
                if c.duration_ms >= SLOW_QUERY_THRESHOLD_MS
            ]

            route_template = getattr(request.scope.get("route"), "path", request.url.path)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id,
                "method": request.method,
                "route": route_template,
                "path": request.url.path,
                "query": str(request.url.query or ""),
                "status_code": status_code,
                "client_tab": client_tab,
                "client_action": client_action,
                "total_ms": total_ms,
                "db_ms": db_ms,
                "app_ms": app_ms,
                "db_calls": trace.db_call_count,
                "n_plus_one": n_plus_one,
                "slow_queries": slow_queries,
                "spans": trace.custom_spans,
            }
            aggregator.record_backend(record)
            _current_trace.reset(token)

            if total_ms >= SLOW_REQUEST_THRESHOLD_MS or n_plus_one or slow_queries:
                logger.warning(
                    "PERF_BOTTLENECK trace=%s %s %s total=%.1fms (db=%.1fms/%dq, app=%.1fms) tab=%s action=%s n+1=%s slow_q=%s",
                    trace_id[:8],
                    request.method,
                    request.url.path,
                    total_ms,
                    db_ms,
                    trace.db_call_count,
                    app_ms,
                    client_tab or "-",
                    client_action or "-",
                    n_plus_one,
                    slow_queries,
                )

            if "response" in locals():
                response.headers["X-Trace-Id"] = trace_id
                response.headers["Server-Timing"] = (
                    f'total;dur={total_ms}, '
                    f'db;dur={db_ms};desc="{trace.db_call_count} queries", '
                    f'app;dur={app_ms}'
                )
                response.headers["X-Perf-Metrics"] = json.dumps(
                    {
                        "total_ms": total_ms,
                        "db_ms": db_ms,
                        "app_ms": app_ms,
                        "db_calls": trace.db_call_count,
                        "n_plus_one": n_plus_one,
                    },
                    separators=(",", ":"),
                )