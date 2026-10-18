"""Content-addressed media with recoverable Mongo/file state."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4
from pymongo.errors import DuplicateKeyError
from app.transaction_service import stored_instant

ORPHAN_RETENTION = timedelta(days=7)


def media_root():
    root=Path(os.getenv("MEDIA_STORAGE_PATH","data/media")).resolve()
    root.mkdir(parents=True,exist_ok=True)
    return root


def store_media(db, data:bytes, digest:str, suffix:str)->Path:
    if sha256(data).hexdigest()!=digest:
        raise ValueError("O checksum do arquivo não confere.")
    path=media_root()/digest
    now=datetime.now(timezone.utc)
    try:
        db.media_assets.update_one({"sha256":digest},{"$setOnInsert":{"sha256":digest,"path":str(path),"status":"media_pending","created_at":now}},upsert=True)
    except DuplicateKeyError:
        # Another instance registered the same content at the same time.
        pass
    if not path.exists() or sha256(path.read_bytes()).hexdigest()!=digest:
        temporary=path.with_name(f".{digest}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
    db.media_assets.update_one({"sha256":digest},{"$set":{"path":str(path),"status":"media_ready","verified_at":now}})
    return path


def reconcile_media(db, limit:int=500)->dict:
    checked=missing=orphan=0
    now=datetime.now(timezone.utc)
    cutoff=now-timedelta(hours=1)
    purge_cutoff=now-ORPHAN_RETENTION
    state=db.workflow_state.find_one({"_id":"media_reconcile"}) or {}
    after=state.get("last_id")
    query={"_id":{"$gt":after}} if after else {}
    assets=list(db.media_assets.find(query).sort("_id",1).limit(limit))
    if not assets and after:
        assets=list(db.media_assets.find({}).sort("_id",1).limit(limit))
    for asset in assets:
        if asset.get("status")=="purged":
            continue
        checked+=1
        path=Path(asset["path"])
        healthy=path.is_file() and sha256(path.read_bytes()).hexdigest()==asset["sha256"]
        reference={"$or":[{"raw_payload.sha256":asset["sha256"]},{"raw_payload.file_sha256":asset["sha256"]}]}
        event=db.raw_events.find_one(reference,{"event_id":1})
        batch=db.capture_batches.find_one({"sha256":asset["sha256"]},{"_id":1})
        if not healthy:
            missing+=1
            db.media_assets.update_one({"_id":asset["_id"]},{"$set":{"status":"media_missing","checked_at":now}})
            db.raw_events.update_many(reference,{"$set":{"media_status":"media_missing"}})
            db.capture_batches.update_many({"sha256":asset["sha256"]},{"$set":{"media_status":"media_missing"}})
        elif not event and not batch and stored_instant(asset.get("created_at",cutoff))<cutoff:
            orphan+=1
            if asset.get("status")=="orphaned" and stored_instant(asset.get("checked_at") or asset.get("created_at",now))<purge_cutoff:
                path.unlink(missing_ok=True)
                db.media_assets.update_one({"_id":asset["_id"]},{"$set":{"status":"purged","purged_at":now,"checked_at":now}})
            else:
                db.media_assets.update_one({"_id":asset["_id"]},{"$set":{"status":"orphaned","checked_at":now}})
        elif event:
            db.capture_batches.update_many({"sha256":asset["sha256"]},{"$set":{"media_status":"media_ready"}})
            db.raw_events.update_many(reference,{"$set":{"media_status":"media_ready","media_storage_key":str(path)}})
            db.media_assets.update_one({"_id":asset["_id"]},{"$set":{"status":"media_ready","checked_at":now}})
        elif batch:
            db.capture_batches.update_many({"sha256":asset["sha256"]},{"$set":{"media_status":"media_ready"}})
            db.media_assets.update_one({"_id":asset["_id"]},{"$set":{"status":"media_ready","checked_at":now}})
    if assets:
        db.workflow_state.update_one({"_id":"media_reconcile"},{"$set":{"last_id":assets[-1]["_id"],"checked_at":now}},upsert=True)
    return {"checked":checked,"missing":missing,"orphaned":orphan,"limited":checked==limit}
