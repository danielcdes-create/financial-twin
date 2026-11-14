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

_HOST_RE = re.compile(rb"(?im)^host:\s*[^\r\n]*\r\n")
_CONN_RE = re.compile(rb"(?im)^connection:\s*[^\r\n]*\r\n")


def _build_tls_context() -> ssl.SSLContext | None:
    if not USE_TLS:
        return None
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


TLS_CONTEXT = _build_tls_context()


def _rewrite_request_head(head: bytes) -> bytes:
    lines = head.split(b"\r\n", 1)
    if len(lines) != 2:
        return head
    request_line, rest = lines
    rest = _HOST_RE.sub(b"", rest)
    rest = _CONN_RE.sub(b"", rest)
    authority = f"{REMOTE_HOST}:{REMOTE_PORT}".encode("ascii", errors="ignore")
    injected = b"Host: " + authority + b"\r\nConnection: close\r\n"
    return request_line + b"\r\n" + injected + rest


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(local_r: asyncio.StreamReader, local_w: asyncio.StreamWriter) -> None:
    buffer = bytearray()
    try:
        while b"\r\n\r\n" not in buffer:
            chunk = await asyncio.wait_for(local_r.read(16384), timeout=CONNECT_TIMEOUT_SECONDS)
            if not chunk:
                local_w.close()
                return
            buffer.extend(chunk)
            if len(buffer) > MAX_HEADER_BYTES:
                local_w.write(b"HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: close\r\n\r\n")
                await local_w.drain()
                local_w.close()
                return
        split_at = buffer.index(b"\r\n\r\n") + 4
        rewritten_head = _rewrite_request_head(bytes(buffer[:split_at]))
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
        try:
            local_w.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"detail\":\"Upstream VPS indisponivel via Tailscale TLS.\"}")
            await local_w.drain()
        except Exception:
            pass
        try:
            local_w.close()
        except Exception:
            pass
        return

    try:
        remote_w.write(rewritten_head)
        if initial_body:
            remote_w.write(initial_body)
        await remote_w.drain()
    except Exception:
        remote_w.close()
        local_w.close()
        return

    await asyncio.gather(pipe(local_r, remote_w), pipe(remote_r, local_w))


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
