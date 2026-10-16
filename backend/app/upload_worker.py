from functools import partial
import anyio
from fastapi import HTTPException
from app.reference_cache import classification_cache


def process_upload(function, args):
    try:
        return function(*args)
    finally:
        # Invalidate on actual completion, including after an HTTP disconnect.
        classification_cache.invalidate()


async def run_upload(request, function, *args):
    # Separate limiter: slow parsing/AI requests cannot exhaust the HTTP read pool.
    limiter = getattr(request.app.state, 'upload_limiter', None)
    if limiter is None:
        limiter = request.app.state.upload_limiter = anyio.CapacityLimiter(2)
    try:
        limiter.acquire_nowait()
    except anyio.WouldBlock:
        raise HTTPException(503, 'Há dois arquivos em processamento. O arquivo continua na fila; tente novamente.', headers={'Retry-After': '5'})
    try:
        # The dedicated token is held until the thread completes, even if the client leaves.
        worker_limiter = getattr(request.app.state, 'upload_thread_limiter', None)
        if worker_limiter is None:
            worker_limiter = request.app.state.upload_thread_limiter = anyio.CapacityLimiter(2)
        return await anyio.to_thread.run_sync(partial(process_upload, function, args), limiter=worker_limiter)
    finally:
        limiter.release()


def persisted_event(db, event_id, result=None):
    event = db.raw_events.find_one({'event_id': event_id})
    if not event or event.get('processing_status') in (None, 'pending'):
        raise HTTPException(503, 'Evento recebido, mas o processamento ainda não foi confirmado. Tente novamente com o mesmo evento.')
    if event.get('processing_status')=='rejected':
        raise HTTPException(422,'A captura exige correção antes de concluir. Preserve o arquivo original.')
    return {**(result or {}), 'event_id': event_id, 'status': event['processing_status'], 'persisted': True}
