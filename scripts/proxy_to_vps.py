import asyncio

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
        remote_r, remote_w = await asyncio.open_connection('198.199.65.103', 8000)
    except Exception:
        local_w.close()
        return
    await asyncio.gather(pipe(local_r, remote_w), pipe(remote_r, local_w))

async def main():
    server = await asyncio.start_server(handle, '127.0.0.1', 8000)
    async with server:
        await server.serve_forever()

asyncio.run(main())
