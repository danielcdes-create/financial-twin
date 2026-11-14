"""Bound expensive interactive AI work without queueing unbounded threads."""
from functools import partial
import anyio
from fastapi import HTTPException


from app.upload_worker import _shared_memory_limiter


async def run_ai(request, function, *args):
    limiter=getattr(request.app.state,'interactive_ai_limiter',None)
    if limiter is None:
        limiter=request.app.state.interactive_ai_limiter=anyio.CapacityLimiter(2)
    mem_limiter=_shared_memory_limiter(request.app.state)
    try:
        limiter.acquire_nowait()
    except anyio.WouldBlock:
        raise HTTPException(429,'Há duas análises em andamento. Seus dados continuam na tela; tente novamente em instantes.',headers={'Retry-After':'5'})
    try:
        try:
            mem_limiter.acquire_nowait()
        except anyio.WouldBlock:
            raise HTTPException(429,'Há análises pesadas em andamento. Seus dados continuam na tela; tente novamente em instantes.',headers={'Retry-After':'5'})
        try:
            worker=getattr(request.app.state,'interactive_ai_worker',None)
            if worker is None:
                worker=request.app.state.interactive_ai_worker=anyio.CapacityLimiter(2)
            return await anyio.to_thread.run_sync(partial(function,*args),limiter=worker)
        finally:
            mem_limiter.release()
    finally:
        limiter.release()
