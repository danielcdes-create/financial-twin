"""Replay confirmed commands before testing versions of their new projection."""
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, OperationFailure
from app.atomic import mongo_transaction, session_database
from app.source_tokens import source_token


def durable_mutation(kind, target_names, body_name='body'):
    def decorate(function):
        parameters=signature(function)
        @wraps(function)
        def invoke(*args,**kwargs):
            bound=parameters.bind(*args,**kwargs)
            body=bound.arguments[body_name]
            request_id=getattr(body,'request_id',None)
            if request_id is None:return function(*args,**kwargs)
            db=bound.arguments['db']
            target={name:str(bound.arguments[name]) for name in target_names}
            payload=body.model_dump(mode='json')
            digest=source_token({'kind':kind,'target':target,'payload':payload})
            identity='mutation:'+str(request_id)
            def replay(reading):
                command=reading.batch_commands.find_one({'_id':identity})
                if not command:return None
                if command.get('digest')!=digest:
                    raise HTTPException(409,'Esta tentativa já foi usada com outros dados. Preserve o rascunho e confira a versão atual.')
                return command['result']
            try:
                with mongo_transaction(db) as session:
                    writing=session_database(db,session)
                    previous=replay(writing)
                    if previous is not None:return previous
                    bound.arguments['db']=writing
                    result=function(*bound.args,**bound.kwargs)
                    result={**result,'persisted':True,'request_id':str(request_id)}
                    if 'provisional_id' in target:result['provisional_id']=target['provisional_id']
                    if getattr(body,'action',None) is not None:result['action']=body.action
                    writing.batch_commands.insert_one({'_id':identity,'kind':kind,'target':target,'digest':digest,
                        'status':'completed','result':result,'created_at':datetime.now(timezone.utc)})
                    return result
            except HTTPException as exc:
                pending_review = getattr(exc, '_batch_command_needs_review', None)
                if pending_review is not None:
                    db.batch_commands.update_one({'_id': pending_review['_id']}, {'$set': pending_review}) if db.batch_commands.find_one({'_id': pending_review['_id']}) else db.batch_commands.insert_one(pending_review)
                raise
            except (DuplicateKeyError,OperationFailure) as exc:
                if isinstance(exc,OperationFailure) and not (exc.code in {11000,24,112,244,251} or exc.has_error_label('TransientTransactionError') or exc.has_error_label('UnknownTransactionCommitResult')):
                    raise
                previous=replay(db)
                if previous is not None:return previous
                raise HTTPException(409,'A operação está concorrendo com outra gravação. Reenvie esta mesma tentativa antes de iniciar outra.',headers={'X-Operation-Pending':'1'}) from exc
        return invoke
    return decorate
