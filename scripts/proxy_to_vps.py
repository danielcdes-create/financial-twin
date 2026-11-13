import asyncio
import os

REMOTE_HOST = os.environ.get("VPS_BACKEND_HOST", "100.101.23.111")
REMOTE_PORT = int(os.environ.get("VPS_BACKEND_PORT", "8010"))
LOCAL_HOST = os.environ.get("LOCAL_PROXY_HOST", "127.0.0.1")
LOCAL_PORT = int(os.environ.get("LOCAL_PROXY_PORT", "8010"))


async def pipe(reader, writer):
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


async def handle(local_r, local_w):
    try:
        remote_r, remote_w = await asyncio.open_connection(REMOTE_HOST, REMOTE_PORT)
    except Exception:
        local_w.close()
        return
    await asyncio.gather(pipe(local_r, remote_w), pipe(remote_r, local_w))


async def main():
    server = await asyncio.start_server(handle, LOCAL_HOST, LOCAL_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
