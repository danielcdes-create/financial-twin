"""Lossless BSON JSON backup. Restore is restricted to EMPTY test databases.

Never prints credentials. Not a cross-service point-in-time snapshot: stop writes
before a production recovery backup; manifest records this limitation.
"""
import argparse, gzip, hashlib, json, shutil, sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson import json_util
from pymongo import MongoClient
from app.config import get_settings
from app.main import configured_mongodb_uri

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def backup(db,target):
    target.mkdir(parents=True,exist_ok=False);manifest={'created_at':datetime.now(timezone.utc).isoformat(),'consistency':'sequential; quiesce application writes for recovery','collections':{},'files':{}}
    for info in db.list_collections():
        name=info['name']
        if name.startswith('system.'):continue
        path=target/(name+'.jsonl.gz');count=0
        with gzip.open(path,'wt',encoding='utf8') as output:
            for row in db[name].find({}):output.write(json_util.dumps(row,json_options=json_util.CANONICAL_JSON_OPTIONS)+'\n');count+=1
        manifest['collections'][name]={'count':count,'sha256':digest(path),'options':json_util.dumps(info.get('options',{}),json_options=json_util.CANONICAL_JSON_OPTIONS),'indexes':json_util.dumps(list(db[name].list_indexes()),json_options=json_util.CANONICAL_JSON_OPTIONS)}
    media=target/'media';media.mkdir()
    paths=set()
    for batch in db.capture_batches.find({},{'storage_key':1}):
        if batch.get('storage_key'):paths.add(batch['storage_key'])
    for asset in db.media_assets.find({},{'path':1}):
        if asset.get('path'):paths.add(asset['path'])
    for event in db.raw_events.find({},{'media_storage_key':1,'raw_payload.storage_key':1}):
        for path in (event.get('media_storage_key'),(event.get('raw_payload') or {}).get('storage_key')):
            if path:paths.add(path)
    for original in sorted(paths):
        path=Path(original)
        if not path.is_file():manifest['files'][original]={'missing':True};continue
        sha=digest(path);dest=media/sha
        if not dest.exists():shutil.copyfile(path,dest)
        manifest['files'][original]={'sha256':sha,'bytes':dest.stat().st_size}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8');return manifest

def restore(client,name,folder,media_target):
    if not name.startswith('financial_twin_test_'):raise ValueError('Restore permitido somente em financial_twin_test_*.')
    db=client[name]
    if db.list_collection_names():raise ValueError('O banco de destino deve estar vazio. Nenhuma coleção será apagada.')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
    # Verify every byte before the first write.
    for key,item in manifest['collections'].items():
        if digest(folder/(key+'.jsonl.gz'))!=item['sha256']:raise ValueError('Checksum inválido: '+key)
    for item in manifest['files'].values():
        if not item.get('missing') and digest(folder/'media'/item['sha256'])!=item['sha256']:raise ValueError('Checksum de mídia inválido.')
    media_target.mkdir(parents=True,exist_ok=False)
    mapping={}
    for original,item in manifest['files'].items():
        if item.get('missing'):continue
        dest=media_target/item['sha256'];shutil.copyfile(folder/'media'/item['sha256'],dest);mapping[original]=str(dest.resolve())
    for key,item in manifest['collections'].items():
        db.create_collection(key,**json_util.loads(item['options']))
        with gzip.open(folder/(key+'.jsonl.gz'),'rt',encoding='utf8') as source:
            for line in source:
                row=json_util.loads(line)
                if key=='media_assets' and row.get('path') in mapping:row['path']=mapping[row['path']]
                if key=='capture_batches' and row.get('storage_key') in mapping:row['storage_key']=mapping[row['storage_key']]
                if key=='raw_events':
                    if row.get('media_storage_key') in mapping:row['media_storage_key']=mapping[row['media_storage_key']]
                    raw=row.get('raw_payload') or {}
                    if raw.get('storage_key') in mapping:raw['storage_key']=mapping[raw['storage_key']]
                db[key].insert_one(row)
        for index in json_util.loads(item['indexes']):
            if index['name']=='_id_':continue
            keys=list(index.pop('key').items());index.pop('v',None);index.pop('ns',None);db[key].create_index(keys,**index)
        if db[key].count_documents({})!=item['count']:raise ValueError('Contagem divergente: '+key)
    return {'restored':name,'collections':len(manifest['collections']),'missing_media':sum(1 for i in manifest['files'].values() if i.get('missing'))}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['backup','restore']);parser.add_argument('--directory',required=True);parser.add_argument('--test-database');parser.add_argument('--media-target');args=parser.parse_args()
    settings=get_settings()
    with MongoClient(configured_mongodb_uri(settings),serverSelectionTimeoutMS=6000) as client:
        if args.mode=='backup':result=backup(client[settings.mongodb_database],Path(args.directory).resolve());print(json.dumps({'collections':len(result['collections']),'files':len(result['files'])}))
        else:
            if not args.test_database or not args.media_target:parser.error('Restore exige --test-database e --media-target.')
            print(json.dumps(restore(client,args.test_database,Path(args.directory).resolve(),Path(args.media_target).resolve())))
