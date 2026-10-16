"""Event loop for the single-process local Windows HTTP server."""
import asyncio


def windows_server_loop():
    # Proactor's AcceptEx can stop accepting after WinError 64 while the
    # process remains alive. The local server does not use async subprocesses.
    return asyncio.SelectorEventLoop()
