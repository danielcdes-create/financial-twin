"""Local loopback-to-Tailscale HTTPS relay for the Financial Twin desktop client."""
import asyncio
import errno
import os
import re
import ssl
import sys

REMOTE_HOST = os.environ.get("VPS_BACKEND_HOST", "ubuntu-s-1vcpu-1gb-nyc1.tail1093e7.ts.net")
REMOTE_PORT = int(os.environ.get("VPS_BACKEND_PORT", "8443"))
USE_TLS = os.environ.get("VPS_BACKEND_TLS", "1") != "0"
LOCAL_HOST = os.environ.get("LOCAL_PROXY_HOST", "127.0.0.1")
LOCAL_PORT = int(os.environ.get("LOCAL_PROXY_PORT", "8000"))
MAX_HEADER_BYTES = 65536
CONNECT_TIMEOUT_SECONDS = 10.0

_HOP_HEADERS_RE = re.compile(rb"(?im)^(?:host|connection|keep-alive|proxy-connection):\s*[^\r\n]*\r\n")
_ORIGIN_RE = re.compile(rb"(?im)^origin:\s*([^\r\n]+)")
_LOOPBACK_ORIGIN_RE = re.compile(r"^https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?$", re.IGNORECASE)
DEFAULT_CORS_ORIGIN = "http://127.0.0.1:19006"


def _build_tls_context() -> ssl.SSLContext | None:
    if not USE_TLS:
        return None
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


TLS_CONTEXT = _build_tls_context()


def _extract_cors_origin(raw: bytes) -> str:
    match = _ORIGIN_RE.search(raw)
    if match:
        candidate = match.group(1).decode("latin-1", errors="ignore").strip()
        if _LOOPBACK_ORIGIN_RE.fullmatch(candidate):
            return candidate
    return DEFAULT_CORS_ORIGIN


def _cors_headers_bytes(origin: str) -> bytes:
    return (
        f"Access-Control-Allow-Origin: {origin}\r\n"
        "Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS\r\n"
        "Access-Control-Allow-Headers: Authorization, Content-Type, X-Client-Tab, X-Client-Action, X-Trace-Id\r\n"
        "Vary: Origin\r\n"
    ).encode("ascii")


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
    except Exception:
        return
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
    except Exception:
        pass


async def _write_synthetic_response(
    writer: asyncio.StreamWriter,
    status_line: str,
    origin: str,
    body: bytes = b"",
    content_type: str | None = "application/json",
) -> None:
    headers = bytearray()
    headers.extend(f"HTTP/1.1 {status_line}\r\n".encode("ascii"))
    if content_type:
        headers.extend(f"Content-Type: {content_type}\r\n".encode("ascii"))
    headers.extend(_cors_headers_bytes(origin))
    headers.extend(f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii"))
    try:
        writer.write(bytes(headers) + body)
        await writer.drain()
    except Exception:
        pass


def _rewrite_request_head(head: bytes) -> bytes:
    lines = head.split(b"\r\n", 1)
    if len(lines) != 2:
        return head
    request_line, rest = lines
    rest = _HOP_HEADERS_RE.sub(b"", rest)
    authority = f"{REMOTE_HOST}:{REMOTE_PORT}".encode("ascii", errors="ignore")
    injected = b"Host: " + authority + b"\r\nConnection: close\r\n"
    return request_line + b"\r\n" + injected + rest


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, half_close_eof: bool = False) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if half_close_eof and writer.can_write_eof():
            try:
                writer.write_eof()
            except Exception:
                pass
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def handle(local_r: asyncio.StreamReader, local_w: asyncio.StreamWriter) -> None:
    buffer = bytearray()
    is_options = False
    try:
        while b"\r\n\r\n" not in buffer:
            chunk = await asyncio.wait_for(local_r.read(16384), timeout=CONNECT_TIMEOUT_SECONDS)
            if not chunk:
                await _close_writer(local_w)
                return
            buffer.extend(chunk)
            if len(buffer) > MAX_HEADER_BYTES:
                origin = _extract_cors_origin(bytes(buffer[:MAX_HEADER_BYTES]))
                await _write_synthetic_response(
                    local_w,
                    "431 Request Header Fields Too Large",
                    origin,
                    b'{"detail":"Cabecalhos HTTP excedem o limite permitido pelo relay local."}',
                )
                await _close_writer(local_w)
                return
        split_at = buffer.index(b"\r\n\r\n") + 4
        raw_head = bytes(buffer[:split_at])
        is_options = raw_head.lstrip().upper().startswith(b"OPTIONS ")
        rewritten_head = _rewrite_request_head(raw_head)
        initial_body = bytes(buffer[split_at:])

        remote_r, remote_w = await asyncio.wait_for(
            asyncio.open_connection(
                REMOTE_HOST,
                REMOTE_PORT,
                ssl=TLS_CONTEXT,
                server_hostname=REMOTE_HOST if TLS_CONTEXT is not None else None,
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except Exception:
        origin = _extract_cors_origin(bytes(buffer))
        if is_options:
            await _write_synthetic_response(
                local_w,
                "204 No Content",
                origin,
                b"",
                content_type=None,
            )
        else:
            await _write_synthetic_response(
                local_w,
                "502 Bad Gateway",
                origin,
                b'{"detail":"Upstream VPS indisponivel via Tailscale TLS."}',
            )
        await _close_writer(local_w)
        return

    try:
        remote_w.write(rewritten_head)
        if initial_body:
            remote_w.write(initial_body)
        await remote_w.drain()
    except Exception:
        await _close_writer(remote_w)
        await _close_writer(local_w)
        return

    client_to_remote_task = asyncio.create_task(pipe(local_r, remote_w, half_close_eof=True))
    remote_to_client_task = asyncio.create_task(pipe(remote_r, local_w, half_close_eof=False))
    try:
        await remote_to_client_task
    finally:
        if not client_to_remote_task.done():
            client_to_remote_task.cancel()
        try:
            await client_to_remote_task
        except (asyncio.CancelledError, Exception):
            pass
        await _close_writer(remote_w)
        await _close_writer(local_w)


async def main() -> None:
    if LOCAL_HOST not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("LOCAL_PROXY_HOST deve permanecer restrito ao loopback local.")
    try:
        server = await asyncio.start_server(handle, LOCAL_HOST, LOCAL_PORT)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            print(f"Proxy local já em execução em {LOCAL_HOST}:{LOCAL_PORT}; encerrando instância duplicada.", flush=True)
            return
        raise
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
