"""Operational scripts, local TLS/HTTP relay, migrations, backup/restore, and release gate tests."""
from __future__ import annotations

import asyncio
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
import runpy
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

# Enforce synthetic inactive MongoDB & empty AI credentials before any app imports.
SYNTHETIC_ENV = {
    "MONGODB_URI": "mongodb://127.0.0.1:27099/synthetic_inactive",
    "MONGODB_DIRECT_HOSTS": "",
    "MONGODB_DATABASE": "financial_twin_test_unit",
    "XAI_API_KEY": "",
    "FIN_ALLOW_REMOTE_TEST_DATABASE": "",
}
for _env_key, _env_val in SYNTHETIC_ENV.items():
    os.environ[_env_key] = _env_val

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
SCRIPTS_DIR = REPO_ROOT / "scripts"
BACKEND_SCRIPTS_DIR = BACKEND_DIR / "scripts"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from bson import ObjectId, json_util  # noqa: E402
from bson.decimal128 import Decimal128  # noqa: E402
from pymongo import DeleteMany, DeleteOne, ReplaceOne, UpdateOne  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.official_statement_parsers import OfficialStatementDocument, OfficialStatementItem  # noqa: E402


def _load_module(module_name: str, file_path: Path, env_overrides: dict[str, str] | None = None):
    merged_env = {**SYNTHETIC_ENV, **(env_overrides or {})}
    with patch.dict(os.environ, merged_env, clear=False):
        get_settings.cache_clear()
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _parse_http_response(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        raise AssertionError(f"Incomplete HTTP response: {raw!r}")
    lines = head.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    status_code = int(parts[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status_code, headers, body


def _unused_loopback_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _matches_filter(doc: dict, query: dict | None) -> bool:
    if not query:
        return True
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches_filter(doc, branch) for branch in condition):
                return False
            continue
        exists = True
        current = doc
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                exists = False
                current = None
                break
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op == "$exists":
                    if exists != bool(expected):
                        return False
                elif op == "$ne":
                    if exists and current == expected:
                        return False
                elif op == "$in":
                    if not exists or current not in expected:
                        return False
                elif op == "$nin":
                    if exists and current in expected:
                        return False
                else:
                    raise NotImplementedError(f"Unsupported operator in fake filter: {op}")
        else:
            if not exists and condition is not None:
                return False
            if current != condition:
                return False
    return True


def _project_doc(doc: dict, projection: dict | None) -> dict:
    copied = copy.deepcopy(doc)
    if not projection:
        return copied
    include_keys = [k for k, v in projection.items() if v and k != "_id"]
    if not include_keys:
        return copied
    result: dict = {}
    if projection.get("_id", 1):
        if "_id" in copied:
            result["_id"] = copied["_id"]
    for key in include_keys:
        parts = key.split(".")
        src = copied
        ok = True
        for part in parts:
            if isinstance(src, dict) and part in src:
                src = src[part]
            else:
                ok = False
                break
        if ok:
            dst = result
            for part in parts[:-1]:
                dst = dst.setdefault(part, {})
            dst[parts[-1]] = src
    return result


class FakeCursor(list):
    def sort(self, key_or_list, direction: int = 1):
        if isinstance(key_or_list, str):
            specs = [(key_or_list, direction)]
        else:
            specs = list(key_or_list)
        items = list(self)
        for field, dir_val in reversed(specs):
            items.sort(key=lambda row: str(row.get(field, "")), reverse=(dir_val < 0))
        return FakeCursor(items)


class FakeCollection:
    def __init__(self, name: str, docs: list[dict] | None = None, options: dict | None = None):
        self.name = name
        self.docs: list[dict] = [copy.deepcopy(d) for d in (docs or [])]
        self.options: dict = copy.deepcopy(options or {})
        self.indexes: list[dict] = [{"v": 2, "key": {"_id": 1}, "name": "_id_"}]
        self.write_calls: list[tuple] = []

    def find(self, query: dict | None = None, projection: dict | None = None):
        matched = [_project_doc(d, projection) for d in self.docs if _matches_filter(d, query)]
        return FakeCursor(matched)

    def find_one(self, query: dict | None = None, projection: dict | None = None):
        for d in self.docs:
            if _matches_filter(d, query):
                return _project_doc(d, projection)
        return None

    def insert_one(self, doc: dict, **_kwargs):
        self.write_calls.append(("insert_one", copy.deepcopy(doc)))
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def insert_many(self, docs: list[dict], **_kwargs):
        ids = [self.insert_one(d).inserted_id for d in docs]
        return SimpleNamespace(inserted_ids=ids)

    def count_documents(self, query: dict | None = None):
        return sum(1 for d in self.docs if _matches_filter(d, query))

    def delete_one(self, query: dict, **_kwargs):
        self.write_calls.append(("delete_one", copy.deepcopy(query)))
        for idx, d in enumerate(self.docs):
            if _matches_filter(d, query):
                del self.docs[idx]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, query: dict, **_kwargs):
        self.write_calls.append(("delete_many", copy.deepcopy(query)))
        kept = [d for d in self.docs if not _matches_filter(d, query)]
        deleted = len(self.docs) - len(kept)
        self.docs = kept
        return SimpleNamespace(deleted_count=deleted)

    def update_one(self, query: dict, update: dict, upsert: bool = False, **_kwargs):
        self.write_calls.append(("update_one", copy.deepcopy(query), copy.deepcopy(update), upsert))
        for d in self.docs:
            if _matches_filter(d, query):
                if "$set" in update:
                    d.update(copy.deepcopy(update["$set"]))
                if "$unset" in update:
                    for k in update["$unset"]:
                        d.pop(k, None)
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            new_doc = {k: v for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}
            if "$setOnInsert" in update:
                new_doc.update(copy.deepcopy(update["$setOnInsert"]))
            if "$set" in update:
                new_doc.update(copy.deepcopy(update["$set"]))
            new_doc.setdefault("_id", ObjectId())
            self.docs.append(new_doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=new_doc["_id"])
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    def update_many(self, query: dict, update: dict, **_kwargs):
        self.write_calls.append(("update_many", copy.deepcopy(query), copy.deepcopy(update)))
        modified = 0
        for d in self.docs:
            if _matches_filter(d, query):
                if "$set" in update:
                    d.update(copy.deepcopy(update["$set"]))
                if "$unset" in update:
                    for k in update["$unset"]:
                        d.pop(k, None)
                modified += 1
        return SimpleNamespace(matched_count=modified, modified_count=modified)

    def bulk_write(self, operations: list, **_kwargs):
        self.write_calls.append(("bulk_write", len(operations)))
        modified = 0
        deleted = 0
        upserted = 0
        for op in operations:
            if isinstance(op, ReplaceOne):
                filt = op._filter
                replacement = copy.deepcopy(op._doc)
                found = False
                for idx, d in enumerate(self.docs):
                    if _matches_filter(d, filt):
                        self.docs[idx] = replacement
                        modified += 1
                        found = True
                        break
                if not found and op._upsert:
                    self.docs.append(replacement)
                    upserted += 1
            elif isinstance(op, UpdateOne):
                res = self.update_one(op._filter, op._doc, upsert=bool(op._upsert))
                modified += res.modified_count
            elif isinstance(op, DeleteOne):
                deleted += self.delete_one(op._filter).deleted_count
            elif isinstance(op, DeleteMany):
                deleted += self.delete_many(op._filter).deleted_count
            else:
                raise NotImplementedError(f"Unsupported bulk operation: {type(op)!r}")
        return SimpleNamespace(modified_count=modified, deleted_count=deleted, upserted_count=upserted)

    def create_index(self, keys, **kwargs):
        if isinstance(keys, str):
            key_dict = {keys: 1}
        else:
            key_dict = dict(keys)
        name = kwargs.get("name") or "_".join(f"{k}_{v}" for k, v in key_dict.items())
        entry = {"v": 2, "key": key_dict, "name": name}
        for k, v in kwargs.items():
            if k != "name":
                entry[k] = v
        if not any(ix["name"] == name for ix in self.indexes):
            self.indexes.append(entry)
        return name

    def list_indexes(self):
        return copy.deepcopy(self.indexes)


class FakeDatabase:
    def __init__(self, name: str):
        self.name = name
        self._collections: dict[str, FakeCollection] = {}
        self.commands: list = []

    def create_collection(self, name: str, **options):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, options=options)
        elif options:
            self._collections[name].options.update(copy.deepcopy(options))
        return self._collections[name]

    def list_collection_names(self) -> list[str]:
        return list(self._collections.keys())

    def list_collections(self, filter: dict | None = None):
        target_name = (filter or {}).get("name")
        items = []
        for name, coll in self._collections.items():
            if target_name is not None and name != target_name:
                continue
            items.append({"name": name, "options": copy.deepcopy(coll.options)})
        return iter(items)

    def command(self, cmd, **kwargs):
        self.commands.append((copy.deepcopy(cmd), copy.deepcopy(kwargs)))
        return {"ok": 1}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(name)
        return self._collections[name]

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


class FakeMongoClient:
    def __init__(self):
        self._dbs: dict[str, FakeDatabase] = {}
        self.closed = False

    def __getitem__(self, name: str) -> FakeDatabase:
        if name not in self._dbs:
            self._dbs[name] = FakeDatabase(name)
        return self._dbs[name]

    def list_database_names(self) -> list[str]:
        return [name for name, db in self._dbs.items() if db.list_collection_names()]

    def drop_database(self, name: str) -> None:
        self._dbs.pop(name, None)

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class SyntheticEnvTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._env_patcher = patch.dict(os.environ, SYNTHETIC_ENV, clear=False)
        self._env_patcher.start()
        get_settings.cache_clear()

    def tearDown(self):
        get_settings.cache_clear()
        self._env_patcher.stop()
        super().tearDown()


# ---------------------------------------------------------------------------
# 1. scripts/proxy_to_vps.py (P0)
# ---------------------------------------------------------------------------
class ProxyToVpsRelayTests(SyntheticEnvTestCase):
    SCRIPT_PATH = SCRIPTS_DIR / "proxy_to_vps.py"

    def _load_proxy(self, **env):
        defaults = {
            "VPS_BACKEND_HOST": "127.0.0.1",
            "VPS_BACKEND_PORT": "18443",
            "VPS_BACKEND_TLS": "0",
            "LOCAL_PROXY_HOST": "127.0.0.1",
            "LOCAL_PROXY_PORT": "0",
        }
        defaults.update(env)
        return _load_module(f"proxy_to_vps_{id(env)}_{time.monotonic_ns()}", self.SCRIPT_PATH, defaults)

    def test_rewrite_request_head_rewrites_host_and_sets_connection_close(self):
        proxy = self._load_proxy(VPS_BACKEND_HOST="vps.tailnet.ts.net", VPS_BACKEND_PORT="8443")
        raw = (
            b"GET /v1/movements?limit=10 HTTP/1.1\r\n"
            b"host: 127.0.0.1:8000\r\n"
            b"Connection: keep-alive\r\n"
            b"Authorization: Bearer synthetic-token\r\n"
            b"Origin: http://127.0.0.1:19006\r\n\r\n"
        )
        rewritten = proxy._rewrite_request_head(raw)
        self.assertTrue(rewritten.startswith(b"GET /v1/movements?limit=10 HTTP/1.1\r\n"))
        self.assertIn(b"Host: vps.tailnet.ts.net:8443\r\n", rewritten)
        self.assertIn(b"Connection: close\r\n", rewritten)
        self.assertNotIn(b"127.0.0.1:8000", rewritten)
        self.assertNotIn(b"keep-alive", rewritten.lower())
        self.assertIn(b"Authorization: Bearer synthetic-token\r\n", rewritten)
        self.assertEqual(proxy._rewrite_request_head(b"MALFORMED_NO_CRLF"), b"MALFORMED_NO_CRLF")

    def test_rewrite_request_head_strips_keep_alive_header(self):
        # Exposes BUG-06: Hop-by-hop Keep-Alive header must be stripped alongside Connection.
        proxy = self._load_proxy(VPS_BACKEND_HOST="vps.tailnet.ts.net", VPS_BACKEND_PORT="8443")
        raw = (
            b"GET /health HTTP/1.1\r\n"
            b"Host: 127.0.0.1:8000\r\n"
            b"Connection: keep-alive\r\n"
            b"Keep-Alive: timeout=5, max=100\r\n"
            b"X-Client-Tab: home\r\n\r\n"
        )
        rewritten = proxy._rewrite_request_head(raw)
        self.assertNotIn(b"keep-alive:", rewritten.lower())
        self.assertIn(b"Connection: close\r\n", rewritten)
        self.assertIn(b"X-Client-Tab: home\r\n", rewritten)

    def test_loopback_guard_rejects_wildcard_bind_host(self):
        proxy = self._load_proxy(LOCAL_PROXY_HOST="0.0.0.0", LOCAL_PROXY_PORT="0")
        with self.assertRaises(RuntimeError):
            asyncio.run(proxy.main())

    def test_tls_context_enforces_tls12_and_fails_closed_against_non_tls_upstream(self):
        async def scenario():
            async def plain_upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                writer.write(b"NOT_TLS\r\n")
                await writer.drain()
                writer.close()

            upstream = await asyncio.start_server(plain_upstream, "127.0.0.1", 0)
            up_port = upstream.sockets[0].getsockname()[1]
            proxy = self._load_proxy(
                VPS_BACKEND_HOST="127.0.0.1",
                VPS_BACKEND_PORT=str(up_port),
                VPS_BACKEND_TLS="1",
            )
            self.assertIsInstance(proxy.TLS_CONTEXT, ssl.SSLContext)
            self.assertEqual(proxy.TLS_CONTEXT.minimum_version, ssl.TLSVersion.TLSv1_2)
            self.assertEqual(proxy.TLS_CONTEXT.verify_mode, ssl.CERT_REQUIRED)

            relay = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
            relay_port = relay.sockets[0].getsockname()[1]
            async with upstream, relay:
                reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
                writer.write(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n\r\n")
                await writer.drain()
                raw = await asyncio.wait_for(reader.read(), timeout=3.0)
                writer.close()
                status, _, _ = _parse_http_response(raw)
                self.assertEqual(status, 502)

        asyncio.run(scenario())

    def test_async_relay_forwards_request_body_and_upstream_response(self):
        async def scenario():
            received: dict[str, bytes] = {}

            async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                head = await reader.readuntil(b"\r\n\r\n")
                body = await reader.readexactly(16)
                received["head"] = head
                received["body"] = body
                payload = b'{"accepted":true}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode("ascii")
                    + payload
                )
                await writer.drain()
                writer.close()

            upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
            up_port = upstream.sockets[0].getsockname()[1]
            proxy = self._load_proxy(VPS_BACKEND_HOST="127.0.0.1", VPS_BACKEND_PORT=str(up_port), VPS_BACKEND_TLS="0")
            relay = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
            relay_port = relay.sockets[0].getsockname()[1]

            async with upstream, relay:
                reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
                writer.write(
                    b"POST /v1/events HTTP/1.1\r\n"
                    b"Host: 127.0.0.1:8000\r\n"
                    b"Content-Length: 16\r\n"
                    b"Connection: close\r\n\r\n"
                    b'{"synthetic":42}'
                )
                await writer.drain()
                raw = await asyncio.wait_for(reader.read(), timeout=3.0)
                writer.close()
                status, _, body = _parse_http_response(raw)
                self.assertEqual(status, 200)
                self.assertEqual(body, b'{"accepted":true}')
                self.assertEqual(received["body"], b'{"synthetic":42}')
                self.assertIn(f"Host: 127.0.0.1:{up_port}\r\n".encode("ascii"), received["head"])

        asyncio.run(scenario())

    def test_half_close_client_eof_still_receives_full_delayed_upstream_payload(self):
        # Exposes BUG-06: client write_eof() must not prematurely close remote_w transport
        # and truncate a delayed multi-chunk upstream HTTP 200 response.
        async def scenario():
            chunks = [b'{"items":[', b'"alpha","beta","gamma"', b'],"complete":true}']
            expected_body = b"".join(chunks)

            async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                try:
                    await reader.readuntil(b"\r\n\r\n")
                    await asyncio.sleep(0.08)
                    writer.write(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        + f"Content-Length: {len(expected_body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                    )
                    await writer.drain()
                    for part in chunks:
                        writer.write(part)
                        await writer.drain()
                        await asyncio.sleep(0.015)
                except ConnectionError:
                    pass
                finally:
                    writer.close()

            upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
            up_port = upstream.sockets[0].getsockname()[1]
            proxy = self._load_proxy(VPS_BACKEND_HOST="127.0.0.1", VPS_BACKEND_PORT=str(up_port), VPS_BACKEND_TLS="0")
            relay = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
            relay_port = relay.sockets[0].getsockname()[1]

            async with upstream, relay:
                reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
                writer.write(b"GET /v1/movements HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n\r\n")
                await writer.drain()
                writer.write_eof()
                raw = await asyncio.wait_for(reader.read(), timeout=3.0)
                writer.close()
                status, _, body = _parse_http_response(raw)
                self.assertEqual(status, 200)
                self.assertEqual(body, expected_body)

        asyncio.run(scenario())

    def test_keep_alive_browser_client_completes_handle_promptly(self):
        # Exposes BUG-06: browser client keeping client->proxy stream open (Connection: keep-alive)
        # must not cause handle() to hang indefinitely in pipe(local_r, remote_w) after upstream finishes.
        async def scenario():
            async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                await reader.readuntil(b"\r\n\r\n")
                payload = b'{"status":"ok"}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(payload)}\r\n".encode("ascii")
                    + b"Connection: close\r\n\r\n"
                    + payload
                )
                await writer.drain()
                writer.close()

            upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
            up_port = upstream.sockets[0].getsockname()[1]
            proxy = self._load_proxy(VPS_BACKEND_HOST="127.0.0.1", VPS_BACKEND_PORT=str(up_port), VPS_BACKEND_TLS="0")

            # Simulate TCP half-close / SelectorEventLoop semantics where closing local_w sends
            # server->client FIN without feeding client->server EOF until the browser closes its socket.
            local_r = asyncio.StreamReader()
            local_r.feed_data(
                b"GET /health HTTP/1.1\r\n"
                b"Host: 127.0.0.1:8000\r\n"
                b"Connection: keep-alive\r\n\r\n"
            )
            written = bytearray()

            class HalfDuplexLocalWriter:
                def __init__(self):
                    self.closed = False

                def write(self, data: bytes) -> None:
                    written.extend(data)

                async def drain(self) -> None:
                    return None

                def can_write_eof(self) -> bool:
                    return True

                def write_eof(self) -> None:
                    return None

                def close(self) -> None:
                    self.closed = True

                async def wait_closed(self) -> None:
                    return None

            local_w = HalfDuplexLocalWriter()
            async with upstream:
                await asyncio.wait_for(proxy.handle(local_r, local_w), timeout=1.5)
            status, _, body = _parse_http_response(bytes(written))
            self.assertEqual(status, 200)
            self.assertEqual(body, b'{"status":"ok"}')
            self.assertTrue(local_w.closed)

        asyncio.run(scenario())

    def test_oversized_headers_431_includes_cors_and_json_detail(self):
        # Exposes BUG-06: synthetic 431 response must include Access-Control-Allow-Origin
        # and JSON error detail so desktop web UI (http://127.0.0.1:19006) can read it.
        async def scenario():
            proxy = self._load_proxy(VPS_BACKEND_HOST="127.0.0.1", VPS_BACKEND_PORT=str(_unused_loopback_port()))
            relay = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
            relay_port = relay.sockets[0].getsockname()[1]
            async with relay:
                reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
                huge = b"a" * (proxy.MAX_HEADER_BYTES + 512)
                writer.write(
                    b"GET /v1/movements HTTP/1.1\r\n"
                    b"Host: 127.0.0.1:8000\r\n"
                    b"Origin: http://127.0.0.1:19006\r\n"
                    b"X-Huge: " + huge + b"\r\n\r\n"
                )
                try:
                    await writer.drain()
                except ConnectionError:
                    pass
                raw = await asyncio.wait_for(reader.read(), timeout=3.0)
                writer.close()
                status, headers, body = _parse_http_response(raw)
                self.assertEqual(status, 431)
                self.assertIn(headers.get("access-control-allow-origin"), {"http://127.0.0.1:19006", "*"})
                parsed = json.loads(body.decode("utf-8"))
                self.assertTrue(parsed.get("detail"))

        asyncio.run(scenario())

    def test_unreachable_upstream_502_includes_cors_and_json_detail(self):
        # Exposes BUG-06: synthetic 502 must include Access-Control-Allow-Origin for desktop UI.
        async def scenario():
            dead_port = _unused_loopback_port()
            proxy = self._load_proxy(
                VPS_BACKEND_HOST="127.0.0.1",
                VPS_BACKEND_PORT=str(dead_port),
                VPS_BACKEND_TLS="0",
            )
            proxy.CONNECT_TIMEOUT_SECONDS = 1.5
            relay = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
            relay_port = relay.sockets[0].getsockname()[1]

            async with relay:
                r, w = await asyncio.open_connection("127.0.0.1", relay_port)
                w.write(
                    b"GET /v1/movements HTTP/1.1\r\n"
                    b"Host: 127.0.0.1:8000\r\n"
                    b"Origin: http://127.0.0.1:19006\r\n\r\n"
                )
                await w.drain()
                raw = await asyncio.wait_for(r.read(), timeout=3.0)
                w.close()
                status, headers, body = _parse_http_response(raw)
                self.assertEqual(status, 502)
                self.assertIn(headers.get("access-control-allow-origin"), {"http://127.0.0.1:19006", "*"})
                parsed = json.loads(body.decode("utf-8"))
                self.assertTrue(parsed.get("detail"))

        asyncio.run(scenario())

    def test_options_preflight_when_upstream_down_does_not_mask_cors_errors(self):
        # Exposes BUG-06: OPTIONS preflight when upstream is down must return CORS preflight
        # headers without masking the subsequent request's 502 JSON error behind a CORS failure.
        async def scenario():
            dead_port = _unused_loopback_port()
            proxy = self._load_proxy(
                VPS_BACKEND_HOST="127.0.0.1",
                VPS_BACKEND_PORT=str(dead_port),
                VPS_BACKEND_TLS="0",
            )
            proxy.CONNECT_TIMEOUT_SECONDS = 1.5
            relay = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
            relay_port = relay.sockets[0].getsockname()[1]

            async with relay:
                pr_r, pr_w = await asyncio.open_connection("127.0.0.1", relay_port)
                pr_w.write(
                    b"OPTIONS /v1/movements HTTP/1.1\r\n"
                    b"Host: 127.0.0.1:8000\r\n"
                    b"Origin: http://127.0.0.1:19006\r\n"
                    b"Access-Control-Request-Method: GET\r\n"
                    b"Access-Control-Request-Headers: authorization,content-type,x-client-tab\r\n\r\n"
                )
                await pr_w.drain()
                pr_raw = await asyncio.wait_for(pr_r.read(), timeout=3.0)
                pr_w.close()
                pr_status, pr_headers, _ = _parse_http_response(pr_raw)
                self.assertIn(pr_status, (200, 204))
                self.assertIn(pr_headers.get("access-control-allow-origin"), {"http://127.0.0.1:19006", "*"})
                self.assertTrue(pr_headers.get("access-control-allow-methods"))
                self.assertTrue(pr_headers.get("access-control-allow-headers"))

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2. backend/scripts/reconcile_official_statements.py (P1)
# ---------------------------------------------------------------------------
class ReconcileOfficialStatementsCliTests(SyntheticEnvTestCase):
    SCRIPT_PATH = BACKEND_SCRIPTS_DIR / "reconcile_official_statements.py"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mod = _load_module("reconcile_official_statements_under_test", cls.SCRIPT_PATH)

    def test_discover_pdfs_missing_pdf_raises_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nao_existe.pdf"
            with self.assertRaises(FileNotFoundError):
                self.mod._discover_pdfs([str(missing)], None)

            empty_repo = Path(tmp) / "empty_repo"
            (empty_repo / "data" / "comprovantes").mkdir(parents=True)
            with patch.object(self.mod, "REPO_ROOT", empty_repo):
                with self.assertRaises(FileNotFoundError):
                    self.mod._discover_pdfs(None, None)

    def test_discover_pdfs_rejects_non_pdf_file_or_directory_in_pdf_args(self):
        # Exposes BUG-09: passing --pdf <non_pdf_file_or_dir> must raise ValueError
        # instead of silently falling through to auto-discovering data/.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decoy_repo = root / "repo"
            (decoy_repo / "data").mkdir(parents=True)
            (decoy_repo / "data" / "Fatura_Itau_decoy.pdf").write_bytes(b"%PDF-1.4\n")

            txt_file = root / "notas.txt"
            txt_file.write_text("nao e pdf", encoding="utf-8")
            folder = root / "subpasta"
            folder.mkdir()

            with patch.object(self.mod, "REPO_ROOT", decoy_repo):
                with self.assertRaises(ValueError):
                    self.mod._discover_pdfs([str(txt_file)], None)
                with self.assertRaises(ValueError):
                    self.mod._discover_pdfs([str(folder)], None)

    def test_discover_pdfs_pdf_dir_discovers_uppercase_and_lowercase_pdfs_together(self):
        # Exposes BUG-09: --pdf-dir must discover both uppercase FATURA_ITAU_01.PDF and
        # lowercase fatura_itau_02.pdf together, including on case-sensitive filesystems.
        with tempfile.TemporaryDirectory() as tmp:
            pdf_dir = Path(tmp) / "pdfs"
            pdf_dir.mkdir()
            upper = pdf_dir / "FATURA_ITAU_01.PDF"
            lower = pdf_dir / "fatura_itau_02.pdf"
            upper.write_bytes(b"%PDF-1.4 upper\n")
            lower.write_bytes(b"%PDF-1.4 lower\n")

            orig_glob = Path.glob

            def posix_case_sensitive_glob(path_self, pattern, *, case_sensitive=None):
                return orig_glob(
                    path_self,
                    pattern,
                    case_sensitive=True if case_sensitive is None else case_sensitive,
                )

            with patch.object(Path, "glob", posix_case_sensitive_glob):
                discovered = self.mod._discover_pdfs(None, str(pdf_dir))
            self.assertEqual({p.name for p in discovered}, {"FATURA_ITAU_01.PDF", "fatura_itau_02.pdf"})

    def test_touched_collections_and_backup_include_review_queue(self):
        # Exposes BUG-09: TOUCHED_COLLECTIONS and _write_pre_apply_backup must include review_queue.
        self.assertIn("review_queue", self.mod.TOUCHED_COLLECTIONS)
        db = FakeDatabase("financial_twin_test_reconcile_backup")
        tx_id = ObjectId()
        db.transactions.insert_one({"_id": tx_id, "valor": Decimal128("-45.90")})
        db.review_queue.insert_one({"review_id": "rev-1", "status": "pending"})

        with tempfile.TemporaryDirectory() as tmp:
            backup_path = self.mod._write_pre_apply_backup(db, Path(tmp))
            envelopes = [json_util.loads(line) for line in backup_path.read_text(encoding="utf-8").splitlines()]
            by_coll = {env["collection"]: env["document"] for env in envelopes}
            self.assertIn("transactions", by_coll)
            self.assertIn("review_queue", by_coll)
            self.assertEqual(by_coll["review_queue"]["review_id"], "rev-1")

    def test_pre_apply_backup_ensure_collections_and_summarize_plan(self):
        db = FakeDatabase("financial_twin_test_reconcile_helpers")
        tx_id = ObjectId()
        db.transactions.insert_one({"_id": tx_id, "valor": Decimal128("-19.90")})
        db.audit_log.insert_one({"event": "seed_event"})

        with tempfile.TemporaryDirectory() as tmp:
            backup_path = self.mod._write_pre_apply_backup(db, Path(tmp))
            raw_lines = backup_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(raw_lines), 2)
            self.assertIn('"$numberDecimal": "-19.90"', raw_lines[0])

        self.mod._ensure_collections_outside_tx(db)
        for coll_name in self.mod.TOUCHED_COLLECTIONS:
            self.assertIn(coll_name, db.list_collection_names())

        items = [
            OfficialStatementItem(
                doc_key="itau_2026_10",
                row_index=1,
                target="card:itau",
                date="2026-10-05",
                purchase_date_hint="2026-10-05",
                statement_month="2026-10",
                amount="-35.00",
                kind="expense",
                description="PADARIA ALFA",
                popular_vendor="Padaria Alfa",
                platform=None,
                categoria="Alimentação",
                subcategoria="Padaria",
                natureza="COMPRA",
                tipo="compra",
                skip_insert=False,
            ),
            OfficialStatementItem(
                doc_key="itau_2026_10",
                row_index=2,
                target="card:itau",
                date="2026-10-06",
                purchase_date_hint="2026-10-06",
                statement_month="2026-10",
                amount="500.00",
                kind="bill_payment",
                description="PAGAMENTO DE FATURA",
                popular_vendor="",
                platform=None,
                categoria=None,
                subcategoria=None,
                natureza=None,
                tipo="pagamento_fatura",
                skip_insert=True,
            ),
        ]
        doc = OfficialStatementDocument(
            doc_key="itau_2026_10",
            filename="Fatura_Itau_202610.pdf",
            sha256="a" * 64,
            kind="card_statement",
            institution="Itaú",
            competencia="2026-10",
            total_amount="35.00",
            status="fechada",
            items=items,
        )
        plans = [
            SimpleNamespace(status="matched_exact"),
            SimpleNamespace(status="missing"),
            SimpleNamespace(status="missing"),
            SimpleNamespace(status="ambiguous_skip"),
        ]
        summary = self.mod._summarize_plan([doc], plans)
        self.assertEqual(summary["documents"][0]["active_items"], 1)
        self.assertEqual(summary["plan_counts"]["matched_exact"], 1)
        self.assertEqual(summary["plan_counts"]["missing"], 2)
        self.assertEqual(summary["plan_counts"]["ambiguous_skip"], 1)
        self.assertEqual(summary["plan_counts"]["already_reconciled"], 0)


# ---------------------------------------------------------------------------
# 3. scripts/Build-OraclePackage.py (P1)
# ---------------------------------------------------------------------------
class BuildOraclePackageTests(SyntheticEnvTestCase):
    SCRIPT_PATH = SCRIPTS_DIR / "Build-OraclePackage.py"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mod = _load_module("build_oracle_package_under_test", cls.SCRIPT_PATH)

    def test_build_creates_archive_excludes_env_and_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "oracle-package.tar.gz"
            result = self.mod.build(out)
            self.assertTrue(out.is_file())
            self.assertEqual(result["sha256"], hashlib.sha256(out.read_bytes()).hexdigest())

            with tarfile.open(out, mode="r:gz") as archive:
                names = set(archive.getnames())
            self.assertIn("Dockerfile", names)
            self.assertIn("docker-compose.yml", names)
            self.assertIn("backend/run_server.py", names)
            self.assertIn("backend/app/main.py", names)
            self.assertFalse(any(name == ".env" or name.endswith("/.env") for name in names))

            with self.assertRaises(FileExistsError):
                self.mod.build(out)

    def test_build_is_deterministically_reproducible_across_timestamps(self):
        # Exposes BUG-12: consecutive builds across >1s wall-clock advance must produce
        # identical SHA-256 digests with mtime=0 on both gzip and tar headers.
        with tempfile.TemporaryDirectory() as tmp:
            out1 = Path(tmp) / "pkg-1.tar.gz"
            out2 = Path(tmp) / "pkg-2.tar.gz"
            res1 = self.mod.build(out1)
            time.sleep(1.1)
            res2 = self.mod.build(out2)

            self.assertEqual(res1["sha256"], res2["sha256"])
            raw1 = out1.read_bytes()
            self.assertEqual(raw1, out2.read_bytes())
            # Gzip header bytes 4..8 store the 32-bit little-endian mtime.
            self.assertEqual(raw1[4:8], b"\x00\x00\x00\x00")
            with tarfile.open(out1, mode="r:gz") as archive:
                self.assertTrue(all(member.mtime == 0 for member in archive.getmembers()))

    def test_build_includes_required_operational_tools_and_migrations(self):
        # Exposes BUG-12: deployment archive must include operational recovery/security tools
        # and backfill_descriptor_keys migration alongside root Docker files.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg-ops.tar.gz"
            self.mod.build(out)
            with tarfile.open(out, mode="r:gz") as archive:
                names = set(archive.getnames())
            required = {
                "backend/scripts/reset_account_password.py",
                "backend/scripts/configure_account_security.py",
                "backend/scripts/backup_restore.py",
                "backend/migrations/backfill_descriptor_keys.py",
                "Dockerfile",
                "docker-compose.yml",
            }
            self.assertTrue(required.issubset(names), f"Missing archive members: {sorted(required - names)}")
            self.assertFalse(any(name == ".env" or name.endswith("/.env") for name in names))

    def test_build_cleans_up_partial_output_if_archiving_raises_midstream(self):
        # Exposes BUG-12: mid-stream archiving failure must unlink partial output file.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "partial-pkg.tar.gz"
            real_addfile = tarfile.TarFile.addfile
            calls = 0

            def failing_addfile(tar_self, tarinfo, fileobj=None):
                nonlocal calls
                calls += 1
                if calls >= 2:
                    raise OSError("Simulated mid-stream write failure")
                return real_addfile(tar_self, tarinfo, fileobj)

            with patch.object(tarfile.TarFile, "addfile", failing_addfile):
                with self.assertRaises(OSError):
                    self.mod.build(out)
            self.assertFalse(out.exists(), "Partial archive file was left behind after mid-stream failure")


# ---------------------------------------------------------------------------
# 4. scripts/verify_apk_source.py, release_pointer.py & check_version_sync.py (P1)
# ---------------------------------------------------------------------------
class ReleaseVerificationAndVersionSyncTests(SyntheticEnvTestCase):
    VERIFY_SCRIPT = SCRIPTS_DIR / "verify_apk_source.py"
    POINTER_SCRIPT = SCRIPTS_DIR / "release_pointer.py"
    SYNC_SCRIPT = SCRIPTS_DIR / "check_version_sync.py"

    SECURITY_CRITICAL_SOURCES = {
        "components/AuthProvider.tsx",
        "components/AccountSecurity.tsx",
        "components/AppUpdate.tsx",
        "utils/biometricSession.ts",
        "utils/authState.ts",
        "utils/apiClient.ts",
    }

    NATIVE_RELATIVES = (
        "index.ts",
        "android/src/main/java/expo/modules/notificationcapture/NotificationCaptureModule.kt",
        "android/src/main/java/expo/modules/notificationcapture/NotificationCaptureService.kt",
        "android/src/main/java/expo/modules/notificationcapture/NotificationInbox.kt",
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.verify_mod = _load_module("verify_apk_source_under_test", cls.VERIFY_SCRIPT)
        cls.pointer_mod = _load_module("release_pointer_under_test", cls.POINTER_SCRIPT)
        cls.sync_mod = _load_module("check_version_sync_under_test", cls.SYNC_SCRIPT)

        cls._template_tmp = tempfile.TemporaryDirectory()
        base = Path(cls._template_tmp.name)
        cls.template_root = base / "repo"
        cls.template_build = base / "build_project"
        cls._init_synthetic_git_and_build(cls.template_root, cls.template_build)

    @classmethod
    def tearDownClass(cls):
        cls._template_tmp.cleanup()
        super().tearDownClass()

    @classmethod
    def _init_synthetic_git_and_build(cls, root: Path, build_project: Path) -> None:
        (root / "mobile").mkdir(parents=True)
        app_cfg = {"expo": {"version": "1.20.86", "android": {"versionCode": 132}}}
        (root / "mobile" / "app.json").write_text(json.dumps(app_cfg, indent=2), encoding="utf-8")

        all_js_sources = sorted(cls.verify_mod.REQUIRED_SOURCES | cls.SECURITY_CRITICAL_SOURCES)
        sources_list = ["node_modules/react/index.js"]
        sources_content = ["module.exports = {};\n"]

        for rel in all_js_sources:
            src_path = root / "mobile" / rel
            src_path.parent.mkdir(parents=True, exist_ok=True)
            content = f"// synthetic tracked source: {rel}\nexport const tag = '{rel}';\n"
            src_path.write_text(content, encoding="utf-8")
            build_copy = build_project / rel
            build_copy.parent.mkdir(parents=True, exist_ok=True)
            build_copy.write_text(content, encoding="utf-8")
            sources_list.append(build_copy.as_posix())
            sources_content.append(content)

        module_dir = Path("modules/notification-capture")
        for rel in cls.NATIVE_RELATIVES:
            content_bytes = f"// native source {rel}\n".encode("utf-8")
            for target_base in (
                root / "mobile" / module_dir,
                build_project / module_dir,
                build_project / "node_modules/expo-notification-capture",
            ):
                p = target_base / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content_bytes)

        (build_project / "app.json").write_text(json.dumps(app_cfg, indent=2), encoding="utf-8")
        bundle_bytes = b"__d(function(){ /* synthetic hermes/js bundle */ });\n"
        bundle_path = build_project / "android/app/build/generated/assets/react/release/index.android.bundle"
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(bundle_bytes)

        map_path = build_project / "android/app/build/generated/sourcemaps/react/release/index.android.bundle.map"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(
            json.dumps({"version": 3, "sources": sources_list, "sourcesContent": sources_content}),
            encoding="utf-8",
        )

        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "synthetic@example.invalid"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Synthetic Test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m", "synthetic baseline"], check=True, capture_output=True)

    def _clone_fixture(self, tmp_dir: str) -> tuple[Path, Path, Path]:
        base = Path(tmp_dir)
        root = base / "repo"
        build = base / "build"
        shutil.copytree(self.template_root, root)
        shutil.copytree(self.template_build, build)
        apk = base / "candidate.apk"
        bundle_bytes = (build / "android/app/build/generated/assets/react/release/index.android.bundle").read_bytes()
        with zipfile.ZipFile(apk, "w") as zf:
            zf.writestr("assets/index.android.bundle", bundle_bytes)
        return root, build, apk

    def test_verify_apk_source_required_sources_include_security_critical_files(self):
        # Exposes BUG-12: REQUIRED_SOURCES must enforce auth, biometric, updater, and API client sources.
        missing = self.SECURITY_CRITICAL_SOURCES.difference(self.verify_mod.REQUIRED_SOURCES)
        self.assertEqual(missing, set(), f"Security-critical files missing from REQUIRED_SOURCES: {sorted(missing)}")

    def test_verify_apk_source_accepts_valid_candidate_and_rejects_bundle_source_or_missing_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, build, apk = self._clone_fixture(tmp)
            verified = self.verify_mod.verify(apk, build, root)
            self.assertTrue(verified["clean_head"])
            self.assertEqual(verified["version_name"], "1.20.86")
            self.assertEqual(verified["version_code"], 132)

            # 1) Mismatched APK bundle bytes
            bad_apk = Path(tmp) / "tampered.apk"
            with zipfile.ZipFile(bad_apk, "w") as zf:
                zf.writestr("assets/index.android.bundle", b"tampered-bundle")
            with self.assertRaises(ValueError):
                self.verify_mod.verify(bad_apk, build, root)

            # 2) Modified tracked source in bundle source-map
            map_path = build / "android/app/build/generated/sourcemaps/react/release/index.android.bundle.map"
            original_map = json.loads(map_path.read_text(encoding="utf-8"))
            tampered_map = copy.deepcopy(original_map)
            tampered_map["sourcesContent"][1] = "// modified in bundle without commit\n"
            map_path.write_text(json.dumps(tampered_map), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.verify_mod.verify(apk, build, root)

            # 3) Missing REQUIRED_SOURCES entry in bundle source-map
            missing_map = copy.deepcopy(original_map)
            missing_map["sources"] = missing_map["sources"][:2]
            missing_map["sourcesContent"] = missing_map["sourcesContent"][:2]
            map_path.write_text(json.dumps(missing_map), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.verify_mod.verify(apk, build, root)

    def test_verify_apk_source_rejects_untracked_local_injected_source_even_when_allow_dirty(self):
        # Exposes BUG-12: an untracked local mobile/utils/injected.ts embedded in the bundle
        # source-map must be rejected even when allow_dirty=True.
        with tempfile.TemporaryDirectory() as tmp:
            root, build, apk = self._clone_fixture(tmp)
            injected_code = "export const injectedPayload = true;\n"
            injected_repo = root / "mobile" / "utils" / "injected.ts"
            injected_build = build / "utils" / "injected.ts"
            injected_repo.write_text(injected_code, encoding="utf-8")
            injected_build.write_text(injected_code, encoding="utf-8")

            map_path = build / "android/app/build/generated/sourcemaps/react/release/index.android.bundle.map"
            smap = json.loads(map_path.read_text(encoding="utf-8"))
            smap["sources"].append(injected_build.as_posix())
            smap["sourcesContent"].append(injected_code)
            map_path.write_text(json.dumps(smap), encoding="utf-8")

            with self.assertRaises(ValueError):
                self.verify_mod.verify(apk, build, root, allow_dirty=True)

    def test_release_pointer_validate_update_and_check_preserve_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            apk_bytes = b"PK\x03\x04synthetic-arm64-apk"
            apk_sha = hashlib.sha256(apk_bytes).hexdigest().upper()
            apk_path = base / "financial-twin-arm64.apk"
            apk_path.write_bytes(apk_bytes)
            manifest_path = base / "financial-twin-arm64.manifest.json"
            manifest_data = {
                "version_name": "1.20.87",
                "version_code": 133,
                "size_bytes": len(apk_bytes),
                "sha256": apk_sha,
            }
            manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")
            config_path = base / "app.json"
            config_path.write_text(
                json.dumps({"expo": {"version": "1.20.87", "android": {"versionCode": 133}}}),
                encoding="utf-8",
            )
            readme_path = base / "README.md"
            readme_path.write_text(
                "# Demo\n\n"
                "Release privado atual: **1.20.86 / versionCode 132**, "
                "[APK ARM64](mobile/artifacts/financial-twin-1.20.86.apk). "
                "SHA256: `AAAA`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.\n",
                encoding="utf-8",
            )

            validated = self.pointer_mod.validate(manifest_path, config_path)
            self.assertEqual(validated["version_code"], 133)
            with self.assertRaises(ValueError):
                self.pointer_mod.check(readme_path, validated)

            self.pointer_mod.update(readme_path, validated)
            self.pointer_mod.check(readme_path, validated)
            updated_text = readme_path.read_text(encoding="utf-8")
            self.assertIn("Release privado atual: **1.20.87 / versionCode 133**", updated_text)
            self.assertIn("Histórico preservado (registro anterior): **1.20.86 / versionCode 132**", updated_text)

    def test_release_pointer_rejects_manifest_path_not_ending_with_manifest_json(self):
        # Exposes BUG-12: validate() must reject manifest paths not ending with .manifest.json
        # instead of doing a substring .replace('.manifest.json', '.apk').
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            apk_bytes = b"PK\x03\x04apk-bytes"
            apk_sha = hashlib.sha256(apk_bytes).hexdigest().upper()
            # Substring trap: 'release.manifest.json.bak'.replace('.manifest.json', '.apk') -> 'release.apk.bak'
            (base / "release.apk.bak").write_bytes(apk_bytes)
            bad_manifest = base / "release.manifest.json.bak"
            bad_manifest.write_text(
                json.dumps(
                    {
                        "version_name": "1.20.86",
                        "version_code": 132,
                        "size_bytes": len(apk_bytes),
                        "sha256": apk_sha,
                    }
                ),
                encoding="utf-8",
            )
            config_path = base / "app.json"
            config_path.write_text(
                json.dumps({"expo": {"version": "1.20.86", "android": {"versionCode": 132}}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                self.pointer_mod.validate(bad_manifest, config_path)

    def test_check_version_sync_works_without_apk_and_verifies_manifest_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mobile" / "artifacts").mkdir(parents=True)
            (root / "mobile" / "app.json").write_text(
                json.dumps({"expo": {"version": "1.20.86", "android": {"versionCode": 132}}}),
                encoding="utf-8",
            )
            (root / "mobile" / "package.json").write_text(
                json.dumps({"name": "financial-twin", "version": "1.20.86"}),
                encoding="utf-8",
            )
            apk_bytes = b"PK\x03\x04arm64-synthetic"
            apk_sha = hashlib.sha256(apk_bytes).hexdigest().upper()
            pointer_line = self.pointer_mod.pointer(
                {"version_name": "1.20.86", "version_code": 132, "sha256": apk_sha}
            )
            (root / "README.md").write_text(f"# Financial Twin\n\n{pointer_line}\n", encoding="utf-8")

            # Without .apk / .manifest.json (CI environment with gitignored artifacts)
            res_ci = self.sync_mod.check_version_sync(root)
            self.assertEqual(res_ci, {"version": "1.20.86", "version_code": 132, "manifest_verified": False})

            # With matching .manifest.json + .apk on disk
            (root / "mobile" / "artifacts" / "financial-twin-arm64.apk").write_bytes(apk_bytes)
            (root / "mobile" / "artifacts" / "financial-twin-arm64.manifest.json").write_text(
                json.dumps(
                    {
                        "version_name": "1.20.86",
                        "version_code": 132,
                        "size_bytes": len(apk_bytes),
                        "sha256": apk_sha,
                    }
                ),
                encoding="utf-8",
            )
            res_local = self.sync_mod.check_version_sync(root)
            self.assertTrue(res_local["manifest_verified"])

            # Drift in package.json is caught
            (root / "mobile" / "package.json").write_text(
                json.dumps({"name": "financial-twin", "version": "1.20.85"}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                self.sync_mod.check_version_sync(root)


# ---------------------------------------------------------------------------
# 5. backend/scripts/backup_restore.py, serve_test_database.py & release_check.py (P0/P1)
# ---------------------------------------------------------------------------
class BackupRestoreServeAndReleaseCheckTests(SyntheticEnvTestCase):
    BACKUP_SCRIPT = BACKEND_SCRIPTS_DIR / "backup_restore.py"
    SERVE_SCRIPT = BACKEND_SCRIPTS_DIR / "serve_test_database.py"
    RELEASE_CHECK_SCRIPT = BACKEND_SCRIPTS_DIR / "release_check.py"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.backup_mod = _load_module("backup_restore_under_test", cls.BACKUP_SCRIPT)

    def test_backup_and_restore_roundtrip_remaps_media_and_guards_target_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            orig_media = base / "orig_media"
            orig_media.mkdir()
            f_asset = orig_media / "receipt.jpg"
            f_batch = orig_media / "statement.csv"
            f_event_media = orig_media / "voice.m4a"
            f_raw_payload = orig_media / "payload_doc.pdf"
            f_asset.write_bytes(b"jpeg-bytes")
            f_batch.write_bytes(b"csv-bytes")
            f_event_media.write_bytes(b"voice-bytes")
            f_raw_payload.write_bytes(b"pdf-bytes")
            missing_file = orig_media / "gone.bin"

            client = FakeMongoClient()
            src_db = client["financas_source_synthetic"]
            tx_id = ObjectId()
            src_db.transactions.create_index([("competencia", 1)], name="competencia_idx")
            src_db.transactions.insert_one(
                {
                    "_id": tx_id,
                    "valor": Decimal128("-123.45"),
                    "competencia": "2026-10",
                    "data_evento": datetime(2026, 10, 10, 15, tzinfo=timezone.utc),
                }
            )
            src_db.media_assets.insert_one({"sha256": "h1", "path": str(f_asset)})
            src_db.capture_batches.insert_one({"_id": "b1", "storage_key": str(f_batch)})
            src_db.raw_events.insert_one(
                {
                    "event_id": "ev-1",
                    "media_storage_key": str(f_event_media),
                    "raw_payload": {"storage_key": str(f_raw_payload)},
                }
            )
            src_db.raw_events.insert_one({"event_id": "ev-missing", "media_storage_key": str(missing_file)})

            backup_dir = base / "backup_out"
            manifest = self.backup_mod.backup(src_db, backup_dir)
            self.assertIn("transactions", manifest["collections"])
            self.assertTrue(manifest["files"][str(missing_file)].get("missing"))

            # Reject non-financial_twin_test_* database
            with self.assertRaises(ValueError):
                self.backup_mod.restore(client, "financas_prod", backup_dir, base / "media_bad")

            # Reject non-empty target test database
            non_empty = client["financial_twin_test_non_empty"]
            non_empty.transactions.insert_one({"_id": ObjectId()})
            with self.assertRaises(ValueError):
                self.backup_mod.restore(client, "financial_twin_test_non_empty", backup_dir, base / "media_ne")

            restored_media = base / "restored_media"
            result = self.backup_mod.restore(client, "financial_twin_test_restored", backup_dir, restored_media)
            self.assertEqual(result["restored"], "financial_twin_test_restored")
            self.assertEqual(result["missing_media"], 1)

            dst_db = client["financial_twin_test_restored"]
            restored_tx = dst_db.transactions.find_one({"_id": tx_id})
            self.assertEqual(restored_tx["valor"], Decimal128("-123.45"))
            self.assertTrue(any(ix["name"] == "competencia_idx" for ix in dst_db.transactions.list_indexes()))

            remapped_asset = Path(dst_db.media_assets.find_one({})["path"])
            remapped_batch = Path(dst_db.capture_batches.find_one({"_id": "b1"})["storage_key"])
            remapped_ev = dst_db.raw_events.find_one({"event_id": "ev-1"})
            remapped_ev_media = Path(remapped_ev["media_storage_key"])
            remapped_raw_key = Path(remapped_ev["raw_payload"]["storage_key"])

            for p, expected_bytes in (
                (remapped_asset, b"jpeg-bytes"),
                (remapped_batch, b"csv-bytes"),
                (remapped_ev_media, b"voice-bytes"),
                (remapped_raw_key, b"pdf-bytes"),
            ):
                self.assertEqual(p.parent.resolve(), restored_media.resolve())
                self.assertEqual(p.read_bytes(), expected_bytes)

    def test_restore_rejects_path_traversal_sha256_with_value_error(self):
        # Exposes BUG-07: path-traversal sha256 values ("../../evil") must be rejected
        # with ValueError before probing paths outside folder/media.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "nested" / "backup"
            (folder / "media").mkdir(parents=True)
            manifest = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "consistency": "test",
                "collections": {},
                "files": {"/original/secret.bin": {"sha256": "../../evil", "bytes": 4}},
            }
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            client = FakeMongoClient()
            with self.assertRaises(ValueError):
                self.backup_mod.restore(
                    client,
                    "financial_twin_test_traversal",
                    folder,
                    Path(tmp) / "restored_media",
                )

    def test_backup_restore_cli_restore_enforces_require_synthetic_target(self):
        # Exposes BUG-07: CLI restore mode must call require_synthetic_target before connecting.
        with tempfile.TemporaryDirectory() as tmp:
            remote_env = {
                **SYNTHETIC_ENV,
                "MONGODB_URI": "mongodb://remote-cluster.example.invalid:27017/financas",
                "FIN_ALLOW_REMOTE_TEST_DATABASE": "",
            }
            argv = [
                str(self.BACKUP_SCRIPT),
                "restore",
                "--directory",
                str(Path(tmp) / "bkp"),
                "--test-database",
                "financial_twin_test_cli",
                "--media-target",
                str(Path(tmp) / "media"),
            ]

            def forbid_remote_client(*_args, **_kwargs):
                raise AssertionError("MongoClient instantiated before require_synthetic_target check")

            with patch.dict(os.environ, remote_env, clear=False), patch.object(sys, "argv", argv), patch(
                "pymongo.MongoClient", side_effect=forbid_remote_client
            ):
                get_settings.cache_clear()
                with self.assertRaises(RuntimeError):
                    runpy.run_path(str(self.BACKUP_SCRIPT), run_name="__main__")

    def test_serve_test_database_rejects_non_test_database_name(self):
        argv = [str(self.SERVE_SCRIPT), "financas", "18081"]
        with patch.object(sys, "argv", argv), patch("uvicorn.run") as run_mock:
            with self.assertRaises(SystemExit):
                runpy.run_path(str(self.SERVE_SCRIPT), run_name="__main__")
            run_mock.assert_not_called()

    def test_serve_test_database_enforces_require_synthetic_target(self):
        # Exposes BUG-07: serve_test_database.py must enforce require_synthetic_target.
        remote_env = {
            **SYNTHETIC_ENV,
            "MONGODB_URI": "mongodb://remote-cluster.example.invalid:27017/financas",
            "FIN_ALLOW_REMOTE_TEST_DATABASE": "",
        }
        argv = [str(self.SERVE_SCRIPT), "financial_twin_test_ui", "18081"]
        with patch.dict(os.environ, remote_env, clear=False), patch.object(sys, "argv", argv), patch(
            "uvicorn.run"
        ) as run_mock, patch("pymongo.MongoClient"):
            get_settings.cache_clear()
            with self.assertRaises(RuntimeError):
                runpy.run_path(str(self.SERVE_SCRIPT), run_name="__main__")
            run_mock.assert_not_called()

    def test_release_check_before_and_after_pass_when_all_protected_routes_return_401(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            fake_client = FakeMongoClient()

            def fake_urlopen(req, timeout=30):
                url = req.full_url
                if url.endswith("/health"):
                    return io.BytesIO(json.dumps({"mongodb": "connected", "auth_required": True}).encode("utf-8"))
                if url.endswith("/openapi.json"):
                    schema = {
                        "paths": {
                            "/v1/insights/simulate": {},
                            "/v1/documents/extract": {
                                "post": {
                                    "requestBody": {
                                        "content": {
                                            "multipart/form-data": {
                                                "schema": {"$ref": "#/components/schemas/BodyExtract"}
                                            }
                                        }
                                    }
                                }
                            },
                        },
                        "components": {"schemas": {"BodyExtract": {"properties": {"captured_at": {}}}}},
                    }
                    return io.BytesIO(json.dumps(schema).encode("utf-8"))
                raise HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)

            fake_release = {
                "available": True,
                "version_code": 132,
                "version_name": "1.20.86",
                "sha256": "A" * 64,
                "size_bytes": 1234,
            }
            with patch("pymongo.MongoClient", return_value=fake_client):
                with patch.object(sys, "argv", [str(self.RELEASE_CHECK_SCRIPT), "before", str(folder)]):
                    runpy.run_path(str(self.RELEASE_CHECK_SCRIPT), run_name="__main__")
                with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch(
                    "app.release_manifest.verified_release", return_value=fake_release
                ), patch.object(sys, "argv", [str(self.RELEASE_CHECK_SCRIPT), "after", str(folder)]):
                    runpy.run_path(str(self.RELEASE_CHECK_SCRIPT), run_name="__main__")

            live = json.loads((folder / "live-release-results.json").read_text(encoding="utf-8"))
            self.assertTrue(live["financial_documents_unchanged"])
            self.assertEqual(live["protected_routes"]["/v1/app-update/apk"], 401)

    def test_release_check_handles_binary_200_on_protected_apk_route_cleanly(self):
        # Exposes BUG-07/BUG-12: binary non-JSON 200 payload on /v1/app-update/apk must raise
        # AssertionError('Protected route accepted unauthenticated request: /v1/app-update/apk')
        # instead of crashing with UnicodeDecodeError / JSONDecodeError.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            fake_client = FakeMongoClient()

            def fake_urlopen(req, timeout=30):
                url = req.full_url
                if url.endswith("/health"):
                    return io.BytesIO(json.dumps({"mongodb": "connected", "auth_required": True}).encode("utf-8"))
                if url.endswith("/v1/app-update"):
                    raise HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)
                if url.endswith("/v1/app-update/apk"):
                    # Simulate accidental unauthenticated binary APK download response (invalid UTF-8 + non-JSON)
                    return io.BytesIO(b"PK\x03\x04\xff\xd8\xff\xe0\x80\x95binary-apk-payload")
                raise HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)

            fake_release = {
                "available": True,
                "version_code": 132,
                "version_name": "1.20.86",
                "sha256": "A" * 64,
                "size_bytes": 1234,
            }
            with patch("pymongo.MongoClient", return_value=fake_client):
                with patch.object(sys, "argv", [str(self.RELEASE_CHECK_SCRIPT), "before", str(folder)]):
                    runpy.run_path(str(self.RELEASE_CHECK_SCRIPT), run_name="__main__")

                with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch(
                    "app.release_manifest.verified_release", return_value=fake_release
                ), patch.object(sys, "argv", [str(self.RELEASE_CHECK_SCRIPT), "after", str(folder)]):
                    with self.assertRaises(AssertionError) as ctx:
                        runpy.run_path(str(self.RELEASE_CHECK_SCRIPT), run_name="__main__")
                    self.assertEqual(
                        str(ctx.exception),
                        "Protected route accepted unauthenticated request: /v1/app-update/apk",
                    )


# ---------------------------------------------------------------------------
# 6. backend/migrations/*.py (P0/P1)
# ---------------------------------------------------------------------------
class MigrationsCliAndSafetyTests(SyntheticEnvTestCase):
    MIGRATION_SCRIPTS = (
        "add_transaction_nature.py",
        "add_transaction_recurrence.py",
        "add_transaction_review.py",
        "propagate_reviewed_recurrence.py",
        "backfill_descriptor_keys.py",
        "repair_audit_2026_09_26.py",
    )

    def test_all_six_migration_scripts_exit_zero_on_help_and_expose_apply_flag(self):
        # Exposes BUG-07: running `python backend/migrations/<script>.py --help` from financial-app root
        # must exit 0 without ModuleNotFoundError and support --apply dry-run safety for all 6 scripts.
        env = {**os.environ, **SYNTHETIC_ENV}
        env.pop("PYTHONPATH", None)
        failures = []
        for script_name in self.MIGRATION_SCRIPTS:
            proc = subprocess.run(
                [sys.executable, f"backend/migrations/{script_name}", "--help"],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.returncode != 0 or "ModuleNotFoundError" in combined or "--apply" not in combined:
                failures.append(f"{script_name} (exit={proc.returncode}): {combined.strip()[:240]}")
        self.assertEqual(failures, [], "Migration CLI --help / --apply failures:\n" + "\n".join(failures))

    def test_validator_and_recurrence_migrations_default_to_dry_run_without_writes(self):
        # Exposes BUG-07: running migration scripts without --apply must not mutate collections or validators.
        for script_name in (
            "add_transaction_recurrence.py",
            "add_transaction_review.py",
            "propagate_reviewed_recurrence.py",
        ):
            with self.subTest(script=script_name):
                client = FakeMongoClient()
                db = client["financial_twin_test_unit"]
                db.create_collection("transactions", validator={"$jsonSchema": {"bsonType": "object", "properties": {}}})
                src_id = ObjectId()
                cand_id = ObjectId()
                db.transactions.docs.extend(
                    [
                        {
                            "_id": src_id,
                            "descricao_original": "NETFLIX.COM",
                            "categoria": "Assinaturas",
                            "subcategoria": "Streaming",
                            "recorrencia": {
                                "tipo": "recorrente_fixa",
                                "periodicidade": "mensal",
                                "source": "mobile_manual_review",
                            },
                        },
                        {"_id": cand_id, "descricao_original": "NETFLIX.COM", "categoria": "Outros"},
                    ]
                )
                with patch("pymongo.MongoClient", return_value=client), patch.object(
                    sys, "argv", [str(MIGRATIONS_DIR / script_name)]
                ):
                    mod = _load_module(
                        f"mig_{script_name.removesuffix('.py')}",
                        MIGRATIONS_DIR / script_name,
                    )
                    mod.main()
                self.assertEqual(db.commands, [], f"{script_name} issued DB command during dry-run")
                self.assertEqual(db.transactions.write_calls, [], f"{script_name} mutated transactions during dry-run")
                self.assertEqual(db.categories.write_calls, [], f"{script_name} mutated categories during dry-run")

    def test_backfill_descriptor_keys_dry_run_and_apply_upsert(self):
        mod = _load_module("backfill_descriptor_keys_test", MIGRATIONS_DIR / "backfill_descriptor_keys.py")
        db = FakeDatabase("financial_twin_test_descriptor")
        tx1 = {"_id": ObjectId(), "descricao_original": "IFD*PADARIA CENTRAL"}
        db.transactions.insert_one(tx1)

        dry = mod.backfill(db, apply=False)
        self.assertEqual(dry, {"scanned": 1, "changed": 1, "applied": False})
        self.assertEqual(db.transaction_descriptor_index.count_documents({}), 0)

        applied = mod.backfill(db, apply=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(db.transaction_descriptor_index.count_documents({}), 1)
        self.assertTrue(db.schema_migrations.find_one({"_id": "descriptor_index_v1"})["complete"])

    def test_backfill_descriptor_keys_prunes_orphan_ids_on_apply(self):
        # Exposes BUG-07: backfill(db, apply=True) must prune orphan _ids from
        # transaction_descriptor_index that no longer exist in db.transactions.
        mod = _load_module("backfill_descriptor_keys_prune_test", MIGRATIONS_DIR / "backfill_descriptor_keys.py")
        db = FakeDatabase("financial_twin_test_descriptor_prune")
        keep_id = ObjectId()
        orphan_id = ObjectId()
        db.transactions.insert_one({"_id": keep_id, "descricao_original": "UBER *TRIP"})
        db.transaction_descriptor_index.insert_one({"_id": keep_id, "key": mod.canonical_key("UBER *TRIP")})
        db.transaction_descriptor_index.insert_one({"_id": orphan_id, "key": "orphan_deleted_tx"})

        mod.backfill(db, apply=True)
        remaining_ids = {doc["_id"] for doc in db.transaction_descriptor_index.find({})}
        self.assertEqual(remaining_ids, {keep_id})

    def test_repair_audit_2026_09_26_plan_excludes_validated_reviews_from_platform_only(self):
        # Exposes BUG-07: repair_audit_2026_09_26.plan(db) must exclude status_revisao == 'validado'
        # rows from platform_only so validated user reviews are never overwritten or aborted.
        mod = _load_module("repair_audit_test", MIGRATIONS_DIR / "repair_audit_2026_09_26.py")
        db = FakeDatabase("financial_twin_test_repair_audit")
        ifood_id = ObjectId()
        db.merchants.insert_one({"_id": ifood_id, "nome_padrao": "iFood"})

        validated_tx = {
            "_id": ObjectId(),
            "merchant_id": ifood_id,
            "platform_id": ifood_id,
            "estabelecimento_original": "IFD*LANCHES",
            "status_revisao": "validado",
        }
        pending_tx = {
            "_id": ObjectId(),
            "merchant_id": ifood_id,
            "platform_id": ifood_id,
            "estabelecimento_original": "IFD*PIZZARIA",
            "status_revisao": "pendente",
        }
        club_tx = {
            "_id": ObjectId(),
            "merchant_id": ifood_id,
            "platform_id": ifood_id,
            "estabelecimento_original": "IFOOD CLUB MENSAL",
        }
        db.transactions.insert_many([validated_tx, pending_tx, club_tx])

        platform_only, direct_service, *_ = mod.plan(db)
        self.assertEqual([d["_id"] for d in direct_service], [club_tx["_id"]])
        self.assertEqual([d["_id"] for d in platform_only], [pending_tx["_id"]])


# ---------------------------------------------------------------------------
# 7. Integration dispatcher static & contract checks (Part 1 verification)
# ---------------------------------------------------------------------------
class IntegrationDispatcherRemediationTests(SyntheticEnvTestCase):
    def test_check_all_registers_all_fourteen_integration_suites(self):
        source = (BACKEND_SCRIPTS_DIR / "check_all.py").read_text(encoding="utf-8")
        self.assertIn("('check_domain_account_fixes.py','domain-account-fixes.json')", source.replace(", ", ","))
        self.assertIn("('check_domain_ledger_fixes.py','domain-ledger-fixes.json')", source.replace(", ", ","))
        check_scripts = sorted(
            p.name for p in BACKEND_SCRIPTS_DIR.glob("check_*.py") if p.name != "check_all.py"
        )
        self.assertEqual(len(check_scripts), 14)
        for script_name in check_scripts:
            self.assertIn(script_name, source)

    def test_check_android_recovery_isolated_uri_allows_custom_synthetic_replica_set(self):
        mod = _load_module("check_android_recovery_unit", BACKEND_SCRIPTS_DIR / "check_android_recovery.py")
        custom_uri = "mongodb://127.0.0.1:27017/?replicaSet=customSyntheticRs"
        self.assertEqual(mod.isolated_uri(custom_uri), custom_uri)
        with self.assertRaises(ValueError):
            mod.isolated_uri("mongodb://remote.example.invalid:27017/?replicaSet=customSyntheticRs")
        with self.assertRaises(ValueError):
            mod.isolated_uri("mongodb://127.0.0.1:27017/?replicaSet=")

    def test_check_domain_account_fixes_enforces_synthetic_target_and_drops_db_on_failure(self):
        mod = _load_module("check_domain_account_fixes_unit", BACKEND_SCRIPTS_DIR / "check_domain_account_fixes.py")
        with tempfile.TemporaryDirectory() as tmp:
            # 1) Remote URI via configured_mongodb_uri is blocked by require_synthetic_target
            remote_env = {
                **SYNTHETIC_ENV,
                "MONGODB_URI": "mongodb://remote.example.invalid:27017/?replicaSet=customRs",
            }
            with patch.dict(os.environ, remote_env, clear=False):
                get_settings.cache_clear()
                with self.assertRaises(RuntimeError):
                    mod.run(Path(tmp) / "out.json")

            # 2) Custom non-financialTwinTest replicaSet is accepted and temporary DB is dropped in finally
            dropped: list[str] = []
            mock_client = MagicMock()
            mock_client.admin.command.return_value = {"setName": "customSyntheticRs", "isWritablePrimary": True}
            mock_client.list_database_names.return_value = []
            mock_db = MagicMock()
            mock_db.create_collection.side_effect = RuntimeError("stop after replica-set check")

            def get_db(name: str):
                mock_db.name = name
                return mock_db

            mock_client.__getitem__.side_effect = get_db
            mock_client.drop_database.side_effect = lambda name: dropped.append(name)

            local_env = {
                **SYNTHETIC_ENV,
                "MONGODB_URI": "mongodb://127.0.0.1:27017/?replicaSet=customSyntheticRs",
            }
            with patch.dict(os.environ, local_env, clear=False), patch.object(
                mod, "MongoClient", return_value=mock_client
            ):
                get_settings.cache_clear()
                with self.assertRaises(RuntimeError):
                    mod.run(Path(tmp))
            self.assertEqual(len(dropped), 1)
            self.assertTrue(dropped[0].startswith("financial_twin_test_domain_account_"))
            self.assertTrue((Path(tmp) / "domain-account-fixes.json").is_file())

    def test_publish_release_ps1_avoids_ps7_only_utf8nobom_encoding(self):
        script_text = (REPO_ROOT / "mobile" / "scripts" / "publish-release.ps1").read_text(encoding="utf-8")
        self.assertNotIn("utf8NoBOM", script_text)
        self.assertNotIn("[IO.File]::Move($temporary,$Target,$true)", script_text)
        self.assertIn("UTF8Encoding", script_text)


if __name__ == "__main__":
    unittest.main()
